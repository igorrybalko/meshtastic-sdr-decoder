"""
hamming.py
----------
Декодирование Hamming(8,4): из 8-битного кодового слова (4 бита данных +
4 бита чётности) восстанавливаем исходный ниббл (4 бита), по возможности
исправляя одну битовую ошибку.

Формулы чётности (генератор) взяты из независимого реверс-инжиниринга
LoRa PHY (EPFL, Tapparel J., "Complete Reverse Engineering of LoRa PHY"):
    p0 = d0 ^ d1 ^ d2
    p1 = d1 ^ d2 ^ d3
    p2 = d0 ^ d1 ^ d3
    p3 = d0 ^ d2 ^ d3

Вместо того чтобы вычислять синдром через таблицу перестановок бит (что
в источниках описано неоднозначно и по-разному в разных реализациях),
используем декодирование методом максимального правдоподобия (ML)
полным перебором: пробуем все 16 возможных ниблов, для каждого строим
"эталонное" кодовое слово по формулам выше и берём то, что ближе всего
(по расстоянию Хэмминга) к принятому. Для кода с 4 битами избыточности
это гарантированно исправляет любую одиночную битовую ошибку и totally
эквивалентно классическому синдромному декодированию, но не требует
знать точный (неочевидный) порядок бит для синдрома.
"""

from __future__ import annotations


def _encode_nibble(nibble: int) -> int:
    d0 = nibble & 1
    d1 = (nibble >> 1) & 1
    d2 = (nibble >> 2) & 1
    d3 = (nibble >> 3) & 1
    p0 = d0 ^ d1 ^ d2
    p1 = d1 ^ d2 ^ d3
    p2 = d0 ^ d1 ^ d3
    p3 = d0 ^ d2 ^ d3
    return d0 | (d1 << 1) | (d2 << 2) | (d3 << 3) | (p0 << 4) | (p1 << 5) | (p2 << 6) | (p3 << 7)


# Таблица "ниббл -> эталонное 8-битное кодовое слово" считается один раз.
_CODEBOOK: list[int] = [_encode_nibble(n) for n in range(16)]


def hamming84_decode(codeword: int) -> tuple[int, int]:
    """Декодировать одно 8-битное кодовое слово.

    Возвращает (nibble, hamming_distance). hamming_distance == 0 значит
    "без ошибок", 1 значит "одна ошибка исправлена", >=2 означает, что
    исправить уже не получится и результату не стоит доверять.
    """
    best_nibble, best_dist = 0, 99
    for nibble, ref in enumerate(_CODEBOOK):
        dist = bin(ref ^ codeword).count("1")
        if dist < best_dist:
            best_dist = dist
            best_nibble = nibble
    return best_nibble, best_dist


def hamming84_decode_all(codewords: list[int]) -> tuple[list[int], list[int]]:
    """Декодировать список кодовых слов. Возвращает (ниблы, расстояния)."""
    nibbles = []
    distances = []
    for cw in codewords:
        n, d = hamming84_decode(cw)
        nibbles.append(n)
        distances.append(d)
    return nibbles, distances
