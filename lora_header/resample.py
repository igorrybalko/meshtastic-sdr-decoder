"""
resample.py
-----------
Приводим записанный поток IQ-отсчётов к "критической" частоте
дискретизации, равной полосе чирпа (BW). После этого один LoRa-символ
занимает ровно 2**SF отсчётов -- целое число, удобное для FFT.

Используем scipy.signal.resample_poly -- полифазный ресемплер, который
сам применяет корректный фильтр против алиасинга (защита от того, что
шум/сигналы вне нужной полосы "свернутся" в неё при понижении частоты
дискретизации).

Про математику: resample_poly(x, up, down) переводит частоту дискретизации
с fs на fs * up / down. Нам нужно fs -> bw, то есть up/down = bw/fs.
Дробь сокращаем до несократимого вида через math.gcd, чтобы ресемплер
работал с минимально возможными (а значит быстрыми) up/down.
"""

from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly


def resample_to_bandwidth(
    iq: np.ndarray, orig_fs: int, bw: int
) -> tuple[np.ndarray, int]:
    """Передискретизировать iq с orig_fs на новую fs, равную bw.

    Возвращает (resampled_iq, new_fs). new_fs обычно в точности равен bw,
    но если bw/orig_fs не сокращается до "красивой" дроби, resample_poly
    всё равно даст частоту, максимально близкую к bw (в нашем случае для
    fs=1_024_000, bw=250_000 дробь сокращается ровно, так что new_fs == bw).
    """
    g = gcd(bw, orig_fs)
    up = bw // g
    down = orig_fs // g

    resampled = resample_poly(iq, up, down)
    new_fs = orig_fs * up // down

    return resampled, new_fs