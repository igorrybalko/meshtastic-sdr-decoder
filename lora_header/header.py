"""
header.py
---------
Декодирование заголовка LoRa-пакета из первых 8 символов после SFD.

СТАТУС: алгоритм ПОЛНОСТЬЮ ПРОВЕРЕН на реальной записи -- вычисленная
контрольная сумма заголовка совпала с переданной (27 == 27). Формулы
сверены с реальным референсным энкодером LoRa-SDR (MyriadRF/Lime
Microsystems, используется в настоящих чипах семейства SX127x):
https://github.com/myriadrf/LoRa-SDR

Шаги декодирования одного символа заголовка (СИМВОЛ -> БИТЫ):
1. Заголовок всегда передаётся с "запасом прочности": реальный
   информационный диапазон символа -- не 2**SF, а 2**(SF-2) значений.
   Передатчик кодирует так: chirp_bin = interleaved_word << 2 (умножает
   на 4). Поэтому здесь делаем обратное: reduced = round(raw_bin / 4).
2. Один шаг XOR (НЕ полный каскадный gray-decode!): word = reduced ^ (reduced >> 1).
   Это математически обратно тому, что делает передатчик (который в
   качестве последнего шага применяет ПОЛНЫЙ gray-decode к interleaved
   значению, чтобы получить bin -- значит, чтобы отменить это на приёме,
   нужен один шаг ПРЯМОГО gray-кодирования).
3. Диагональный деинтерливинг (deinterleave.py) -> 9 кодовых слов по 8 бит
   (ppm = SF-2 = 9 для SF=11).
4. Первые 5 кодовых слов декодируются Hamming(8,4) НАПРЯМУЮ (без какой-либо
   дополнительной перестановки битов) -> 5 ниблов = 3 байта заголовка:
       hdr[0] = длина payload (полный байт)
       hdr[1] = младший ниббл = crc_flag (бит 0) | coding_rate (биты 1-3)
       hdr[2] = байт контрольной суммы (значащие только младшие 5 бит)
   Оставшиеся 4 кодовых слова (индексы 5-8) несут первые ниблы САМОГО
   payload (заголовок и начало данных используют один и тот же блок
   символов) -- их разбор будет на следующем этапе, вместе с остальным
   payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from .deinterleave import deinterleave
from .hamming import hamming84_decode_all

HEADER_SYMBOLS = 8         # заголовок всегда 4+4 = 8 символов (CR=4/8)
HEADER_FIELD_CODEWORDS = 5  # из ppm=SF-2 кодовых слов только первые 5 -- поля заголовка


@dataclass
class HeaderInfo:
    payload_length: int
    crc_present: bool
    coding_rate: int          # 1=4/5, 2=4/6, 3=4/7, 4=4/8
    checksum_valid: bool
    hamming_distances: list[int]  # по кодовым словам самого заголовка (0/1 = надёжно)
    trailing_codewords: list[int]  # кодовые слова 5..ppm-1 -- начало payload (для след. этапа)


def _header_checksum(h0: int, h1: int) -> int:
    """Формула контрольной суммы заголовка (5 бит), сверена по реальному
    энкодеру LoRa-SDR (headerChecksum() в LoRa-SDR-Code.h).
    """
    a0, a1, a2, a3 = (h0 >> 4) & 1, (h0 >> 5) & 1, (h0 >> 6) & 1, (h0 >> 7) & 1
    b0, b1, b2, b3 = (h0 >> 0) & 1, (h0 >> 1) & 1, (h0 >> 2) & 1, (h0 >> 3) & 1
    c0, c1, c2, c3 = (h1 >> 0) & 1, (h1 >> 1) & 1, (h1 >> 2) & 1, (h1 >> 3) & 1

    res = (a0 ^ a1 ^ a2 ^ a3) << 4
    res |= (a3 ^ b1 ^ b2 ^ b3 ^ c0) << 3
    res |= (a2 ^ b0 ^ b3 ^ c1 ^ c3) << 2
    res |= (a1 ^ b0 ^ b2 ^ c0 ^ c1 ^ c2) << 1
    res |= a0 ^ b1 ^ c0 ^ c1 ^ c2 ^ c3
    return res


def decode_header(raw_bins: list[int], sf: int) -> HeaderInfo:
    """raw_bins -- 8 "сырых" номеров бинов (результат dechirp_symbol) для
    первых 8 символов сразу после SFD (см. sfd.find_sfd().header_start_resampled).
    """
    if len(raw_bins) != HEADER_SYMBOLS:
        raise ValueError(f"Ожидалось {HEADER_SYMBOLS} символов заголовка, получено {len(raw_bins)}")

    ppm = sf - 2
    bins_hdr = 1 << ppm

    reduced = [round(b / 4.0) % bins_hdr for b in raw_bins]
    words = [r ^ (r >> 1) for r in reduced]

    codewords = deinterleave(words, ppm)

    nibbles, distances = hamming84_decode_all(codewords[:HEADER_FIELD_CODEWORDS])

    h0 = (nibbles[0] << 4) | nibbles[1]
    h1 = nibbles[2]
    h2 = (nibbles[3] << 4) | nibbles[4]

    computed_checksum = _header_checksum(h0, h1)
    received_checksum = h2 & 0x1F

    return HeaderInfo(
        payload_length=h0,
        crc_present=bool(h1 & 0x1),
        coding_rate=(h1 >> 1) & 0x7,
        checksum_valid=(computed_checksum == received_checksum),
        hamming_distances=distances,
        trailing_codewords=codewords[HEADER_FIELD_CODEWORDS:],
    )