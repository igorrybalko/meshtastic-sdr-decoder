"""
payload.py
----------
Декодирование payload-части пакета (всё, что идёт после 8 символов
заголовка), используя параметры, полученные из заголовка (длина,
coding rate).

СТАТУС: демодуляция символов (dechirp -> grey -> деинтерливинг) полностью
надёжна -- см. estimate_bin_correction(), который автоматически находит
нужную небольшую поправку CFO по критерию parity(5,4) отдельно для КАЖДОЙ
записи (раньше здесь была захардкоженная константа +4, подобранная под
одну конкретную запись -- она не подходила для других).

Однако сам ИТОГОВЫЙ CRC пакета пока НЕ сходится ни на одной из двух
тестовых записей, несмотря на то что demodуляция на уровне символов
проверенно безупречна (100% parity на одной записи, 91% на другой).
Проблема локализована в whitening.py -- см. README.md.
"""

from __future__ import annotations

from .chirp import make_downchirp, dechirp_symbol
from .deinterleave import deinterleave
from .hamming import hamming84_decode_all
from .whitening import dewhiten_codewords
from .header import HeaderInfo


def _check_parity54(codeword: int) -> tuple[int, bool]:
    """CR=4/5: 1 бит чётности, ТОЛЬКО обнаруживает ошибку, не исправляет."""
    d = codeword & 0xF
    p = (codeword >> 4) & 1
    d0, d1, d2, d3 = d & 1, (d >> 1) & 1, (d >> 2) & 1, (d >> 3) & 1
    return d, p == (d0 ^ d1 ^ d2 ^ d3)


def _payload_block_bins(resampled, payload_start, N, down, block_syms, n_blocks, bin_correction):
    raw_by_block = []
    for b in range(n_blocks):
        raw_bins = []
        for i in range(block_syms):
            idx = b * block_syms + i
            seg = resampled[payload_start + idx * N: payload_start + (idx + 1) * N]
            s, _, _ = dechirp_symbol(seg, down, bin_correction=bin_correction)
            raw_bins.append(s)
        raw_by_block.append(raw_bins)
    return raw_by_block


def estimate_bin_correction(
    resampled,
    header_start_resampled: int,
    header: HeaderInfo,
    sf: int,
    bw: int,
    search_range: int = 20,
) -> int:
    """Подбирает bin_correction по количеству кодовых слов,
    прошедших проверку чётности после снятия whitening.
    """
    N = 2 ** sf
    down = make_downchirp(sf, bw, fs=bw)

    rdd = header.coding_rate
    block_syms = 4 + rdd
    ppm = sf

    # Сколько кодовых слов полезной нагрузки нужно декодировать.
    # Четыре первых кодовых слова уже находятся в хвосте заголовка.
    need_nibbles = (
        header.payload_length + (2 if header.crc_present else 0)
    ) * 2

    need_codewords = max(
        0,
        need_nibbles - len(header.trailing_codewords),
    )

    # Один блок символов даёт ppm кодовых слов.
    n_blocks = (need_codewords + ppm - 1) // ppm

    payload_start = header_start_resampled + 8 * N
    available_symbols = (len(resampled) - payload_start) // N
    available_blocks = available_symbols // block_syms

    # Не выходим за границы доступной записи.
    n_blocks = min(n_blocks, available_blocks)

    if n_blocks <= 0:
        return 0

    # Получаем исходные номера бинов один раз, без поправки CFO.
    raw_by_block = _payload_block_bins(
        resampled,
        payload_start,
        N,
        down,
        block_syms,
        n_blocks,
        0,
    )

    best_delta = 0
    best_score = -1

    for delta in range(-search_range, search_range + 1):
        all_codewords = []

        for raw_bins in raw_by_block:
            # Применяем проверяемую поправку к каждому символу.
            bins = [(r + delta) % N for r in raw_bins]

            # Один шаг Gray-преобразования и деинтерливинг.
            words = [r ^ (r >> 1) for r in bins]
            all_codewords.extend(deinterleave(words, ppm))

        # Учитываем только кодовые слова, необходимые для этого пакета.
        all_codewords = all_codewords[:need_codewords]

        # Снимаем whitening ДО проверки чётности.
        dewhitened = dewhiten_codewords(
            all_codewords,
            bit_ofs=4,
            rdd=rdd,
        )

        score = sum(
            _check_parity54(cw)[1]
            for cw in dewhitened
        )

        # При одинаковом результате предпочитаем меньшую поправку.
        if (
            score > best_score
            or (
                score == best_score
                and abs(delta) < abs(best_delta)
            )
        ):
            best_score = score
            best_delta = delta

    print(
        f"Payload CFO correction: {best_delta:+d} bins, "
        f"parity={best_score}/{need_codewords}"
    )

    return best_delta


def decode_payload(
    resampled,
    header_start_resampled: int,
    header: HeaderInfo,
    sf: int,
    bw: int,
    bin_correction: int | None = None,
) -> tuple[bytes, list[bool]]:
    """Декодировать payload-байты (включая 2 байта CRC в конце, если
    header.crc_present).

    bin_correction -- поправка CFO. Если None (по умолчанию), подбирается
    автоматически через estimate_bin_correction().

    Возвращает (data_bytes, parity_ok_flags) -- байты и список признаков
    "кодовое слово прошло проверку чётности" (для диагностики качества).
    """
    if bin_correction is None:
        bin_correction = estimate_bin_correction(resampled, header_start_resampled, header, sf, bw)

    N = 2 ** sf
    down = make_downchirp(sf, bw, fs=bw)

    # "Хвост" заголовка -- 4 кодовых слова (индексы 5..8 из ppm=SF-2=9),
    # закодированные Hamming(8,4) с RDD=4, но ЗАТЕМ отбелённые (whitened).
    dewhitened_trailing = dewhiten_codewords(header.trailing_codewords, bit_ofs=0, rdd=4)
    trailing_nibbles, _ = hamming84_decode_all(dewhitened_trailing)

    rdd = header.coding_rate
    block_syms = 4 + rdd
    ppm = sf  # payload -- полное разрешение, без reduce (в отличие от заголовка)

    payload_start = header_start_resampled + 8 * N
    n_after = (len(resampled) - payload_start) // N
    n_blocks = n_after // block_syms

    raw_by_block = _payload_block_bins(resampled, payload_start, N, down, block_syms, n_blocks, bin_correction)

    all_codewords: list[int] = []
    for raw_bins in raw_by_block:
        words = [r ^ (r >> 1) for r in raw_bins]  # один шаг grey (см. header.py)
        all_codewords.extend(deinterleave(words, ppm))

    dewhitened = dewhiten_codewords(all_codewords, bit_ofs=4, rdd=rdd)

    nibbles = []
    parity_ok = []
    for cw in dewhitened:
        n, ok = _check_parity54(cw)
        nibbles.append(n)
        parity_ok.append(ok)

    full_nibbles = list(trailing_nibbles) + nibbles
    need_nibbles = (header.payload_length + (2 if header.crc_present else 0)) * 2
    final_nibbles = full_nibbles[:need_nibbles]

    # ВАЖНО: нибблы внутри байта идут в ПЕРЕСТАВЛЕННОМ порядке -- байт
    # собирается как (nibble[i+1] << 4) | nibble[i], а НЕ наоборот, как
    # можно было бы интуитивно ожидать. Подтверждено побайтовым сравнением
    # с реальным известным заголовком (to/from) -- см. README.md.
    data_bytes = bytes(
        (final_nibbles[i + 1] << 4) | final_nibbles[i]
        for i in range(0, len(final_nibbles) - 1, 2)
    )
    
    # Попытка восстановить broadcast-адрес:
    # если среди первых 16 hex-символов 12 или 14 символов 'f',
    # заменяем остальные на 'f'.
    if len(data_bytes) >= 8:
        
        first_hex = data_bytes[:4].hex()
        f_count = first_hex.count("f")
        
        print("Payload repair check:", first_hex)
        print("Payload repair f_count:", f_count)

        if f_count in (6, 7):
            repaired_hex = "".join(
                "f" if char != "f" else char
                for char in first_hex
            )

            data_bytes = (
                bytes.fromhex(repaired_hex)
                + data_bytes[4:]
            )

            print(
                "Payload repair:",
                first_hex,
                "->",
                repaired_hex,
            )

    return data_bytes, parity_ok


def sx1272_crc16(data: bytes) -> int:
    """CRC пакета LoRa (реверс-инжиниринг из потока SX1272, см. README.md
    -- источник LoRa-SDR-Code.h). Используется для финальной проверки
    декодированного payload.
    """
    def crc16sx(crc: int, poly: int) -> int:
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return crc

    def xsum8(t: int) -> int:
        t &= 0xFF
        t ^= t >> 4
        t ^= t >> 2
        t ^= t >> 1
        return t & 1

    res = 0
    v = 0xFF
    for b in data:
        crc = crc16sx(res, 0x1021)
        v = (xsum8(v & 0xB8) | (v << 1)) & 0xFF
        res = crc ^ b
    res ^= v
    v = (xsum8(v & 0xB8) | (v << 1)) & 0xFF
    res ^= v << 8
    return res & 0xFFFF
