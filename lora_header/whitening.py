"""
whitening.py
------------
Порт таблицы "отбеливания" (whitening) из референсного энкодера LoRa-SDR
(MyriadRF/Lime Microsystems) -- см. Sx1272ComputeWhitening в
https://github.com/myriadrf/LoRa-SDR/blob/master/LoRaCodes.hpp

Отбеливание -- это просто XOR данных с псевдослучайной последовательностью
(чтобы в эфире не было длинных одинаковых последовательностей бит).
XOR сам себя обращает, поэтому "отбеливание" и "снятие отбеливания" --
одна и та же операция.

ВАЖНО: в отличие от типичного отбеливания байт целиком, здесь отбеливаются
уже ЗАКОДИРОВАННЫЕ Hamming/parity кодовые слова (каждое -- 4+RDD бит) ДО
их декодирования -- то есть эту функцию нужно применять к кодовым словам
сразу после деинтерливинга, но ПЕРЕД hamming/parity-декодированием.
"""

from __future__ import annotations
import numpy as np

_OFS0 = [6, 4, 2, 0, -112, -114, -302, -34]   # смещения для RDD != 1
_OFS1 = [6, 4, 2, 0, -360]                     # смещения для RDD == 1 (CR 4/5)
_WHITEN_LEN = 510

_WHITEN_SEQ = [
    0x0102291EA751AAFF, 0xD24B050A8D643A17, 0x5B279B671120B8F4, 0x032B37B9F6FB55A2,
    0x994E0F87E95E2D16, 0x7CBCFC7631984C26, 0x281C8E4F0DAEF7F9, 0x1741886EB7733B15,
]


def dewhiten_codewords(codewords: list[int], bit_ofs: int, rdd: int) -> list[int]:
    """Снять отбеливание со списка кодовых слов (каждое -- 4+rdd бит).

    bit_ofs -- начальное смещение в общей псевдослучайной последовательности
    (см. header.py/payload.py -- для "хвоста" заголовка это 0, для основных
    данных payload -- смещение, на котором остановилось отбеливание заголовка).
    """
    ofs = _OFS1 if rdd == 1 else _OFS0
    result = []
    for j, cw in enumerate(codewords):
        x = 0
        for i in range(4 + rdd):
            t = (ofs[i] + j + bit_ofs + _WHITEN_LEN) % _WHITEN_LEN
            if (_WHITEN_SEQ[t >> 6] >> (t & 0x3F)) & 1:
                x |= 1 << i
        result.append(cw ^ x)
    return result


def generate_whitening_bytes(n):
    out = []
    reg = 0xFF
    for _ in range(n):
        out.append(reg)
        bit = ((reg >> 7) ^ (reg >> 5) ^ (reg >> 4) ^ (reg >> 3)) & 1
        reg = ((reg << 1) & 0xFF) | bit
    return np.array(out, dtype=np.uint8)