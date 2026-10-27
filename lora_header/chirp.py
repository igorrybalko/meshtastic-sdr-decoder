"""
chirp.py
--------
Генерация "идеальных" LoRa-чирпов и утилита демодуляции одного символа
через dechirp + FFT. Эти функции — основа вообще всего LoRa-демодулятора:
и поиск преамбулы, и разбор заголовка, и разбор данных все использует
одно и то же: "умножить принятый символ на опорный down-чирп и посмотреть,
на какой частоте окажется пик в спектре".

Немного теории (коротко, без формул на бумаге):
- LoRa-символ — это чирп (chirp) длительностью T = 2^SF / BW секунд,
  частота которого линейно нарастает от -BW/2 до +BW/2 (up-чирп).
- Число SF (Spreading Factor) определяет, сколько РАЗНЫХ символов можно
  закодировать: 2^SF штук (числа от 0 до 2^SF - 1).
- Кодирование значения s: чирп просто "сдвинут по кругу" (циклически) —
  начинается не с -BW/2, а с частоты, соответствующей s, и, доходя до
  +BW/2, скачком продолжается с -BW/2 (циклический перенос).
- Downchirp — это тот же чирп, но частота убывает (зеркальный по частоте
  и по времени). Математически это просто комплексно-сопряжённый upchirp.
- Приём (демодуляция): умножаем принятый символ на ЭТАЛОННЫЙ downchirp
  (это называется "dechirp" — "снятие чирпа"). Так циклический сдвиг
  превращается в чистый синусоидальный тон нужной частоты. Берём FFT —
  номер бина с максимальной энергией и есть искомое число s.

Здесь мы работаем на "критической" частоте дискретизации fs == BW —
то есть один символ занимает ровно 2**SF отсчётов (см. resample.py).
"""

from __future__ import annotations

import numpy as np


def make_upchirp(sf: int, bw: float, fs: float, symbol: int = 0) -> np.ndarray:
    """Сгенерировать один up-чирп длиной 2**sf отсчётов (при fs == bw).

    symbol=0 -- "чистый" эталонный up-чирп (частота от -bw/2 до +bw/2).
    symbol=s (0 <= s < 2**sf) -- тот же чирп, но циклически сдвинутый —
    именно так LoRa кодирует переданное число s в один символ.
    """
    
    n_samples = round(fs * (2 ** sf) / bw)  # при fs==bw это ровно 2**sf
    t = np.arange(n_samples) / fs  # моменты времени отсчётов, секунды

    symbol_duration = (2 ** sf) / bw  # T, секунд
    chirp_rate = bw / symbol_duration  # k = скорость нарастания частоты, Гц/с

    # Мгновенная фаза "чистого" up-чирпа (символ 0):
    #   freq(t) = -bw/2 + k * t   (линейно растёт от -bw/2 до +bw/2)
    #   phase(t) = 2*pi * integral(freq(t) dt) = 2*pi*(-bw/2 * t + k/2 * t^2)
    phase = 2 * np.pi * (-bw / 2 * t + chirp_rate / 2 * t ** 2)
    base_chirp = np.exp(1j * phase)

    if symbol == 0:
        return base_chirp

    # Циклический сдвиг во времени на symbol/bw секунд <=> сдвиг на
    # symbol отсчётов (при fs==bw). Знак минус — чтобы после dechirp
    # (умножения на down-чирп символа 0 и FFT) пик оказывался ровно
    # в бине symbol (проверено тестом: без минуса получался бин N-symbol).
    shift_samples = round(symbol * fs / bw)
    return np.roll(base_chirp, -shift_samples)


def make_downchirp(sf: int, bw: float, fs: float) -> np.ndarray:
    """Эталонный down-чирп (частота убывает от +bw/2 до -bw/2).

    Это в точности комплексно-сопряжённый чистый up-чирп — стандартный
    математический факт для симметричного линейного чирпа.
    """
    return np.conj(make_upchirp(sf, bw, fs, symbol=0))


def dechirp_symbol(
    segment: np.ndarray, ref_downchirp: np.ndarray, bin_correction: int = 0
) -> tuple[int, float, np.ndarray]:
    """Демодулировать один символ: dechirp + FFT.

    bin_correction -- целочисленная поправка номера бина (по кругу, mod N).
    Нужна из-за небольшой остаточной несогласованности частоты приёмника
    (CFO, carrier frequency offset): для ЭТОЙ конкретной записи она
    подобрана эмпирически (см. sync.py) и равна +4 -- заголовок (который
    работает на пониженном разрешении SF-2) её "не замечает", а вот
    полноразрешающий payload без неё декодируется с большим количеством
    ошибок чётности (проверено: без поправки verно ~66% кодовых слов,
    с поправкой -- ~91%).

    Возвращает:
        symbol_value  -- номер бина с максимальной энергией (0..N-1),
                          это и есть переданное число s.
        peak_magnitude -- величина пика (для оценки SNR/уверенности).
        spectrum       -- полный спектр (модуль FFT), пригодится для отладки.
    """
    if len(segment) != len(ref_downchirp):
        raise ValueError(
            f"Длина сегмента ({len(segment)}) не совпадает с длиной "
            f"эталонного чирпа ({len(ref_downchirp)})"
        )

    dechirped = segment * ref_downchirp
    spectrum = np.abs(np.fft.fft(dechirped))

    symbol_value = int(np.argmax(spectrum))
    peak_magnitude = float(spectrum[symbol_value])

    if bin_correction:
        n = len(ref_downchirp)
        symbol_value = (symbol_value + bin_correction) % n

    return symbol_value, peak_magnitude, spectrum