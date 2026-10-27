"""
deinterleave.py
---------------
Обращаем "диагональное" переплетение битов LoRa.

При передаче LoRa берёт ppm кодовых слов (по одному на каждый будущий
LoRa-символ... нет, наоборот: берёт по одному биту от каждого из
(4+cr) кодовых слов на каждый символ, со сдвигом -- отсюда "диагональ")
и распределяет их так, чтобы одиночная ошибка целого символа (например,
из-за помехи) повредила РАЗНЫЕ кодовые слова по одному биту в каждом,
а не одно кодовое слово целиком -- тогда Hamming-код может её исправить.

Реализация ниже сверена с реальным работающим декодером (rpp0/gr-lora,
функция deinterleave() в decoder_impl.cc) через циклический сдвиг (rotl):
для i-го принятого символа (i = 0..bits_per_word-1) его ppm-битное
значение циклически сдвигается влево на i позиций, после чего бит на
позиции x идёт в бит i кодового слова x.
"""

from __future__ import annotations


def _rotl(value: int, shift: int, bits: int) -> int:
    """Циклический сдвиг влево value (bits-битного числа) на shift позиций."""
    shift %= bits
    mask = (1 << bits) - 1
    return ((value << shift) | (value >> (bits - shift))) & mask


def deinterleave(symbols: list[int], ppm: int) -> list[int]:
    """Деинтерливинг.

    symbols -- список "слов" (уже после dechirp+gray), каждое ppm-битное,
               длина списка == bits_per_word (для заголовка это 4+4=8,
               для payload -- 4+cr).
    Возвращает список из ppm кодовых слов, каждое длиной len(symbols) бит.
    """
    bits_per_word = len(symbols)
    codewords = [0] * ppm

    for i, word in enumerate(symbols):
        rotated = _rotl(word, i, ppm)
        for x in range(ppm):
            bit = (rotated >> x) & 1
            codewords[x] |= bit << i

    return codewords
