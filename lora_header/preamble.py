"""
preamble.py
-----------
Точная синхронизация: находим ТОЧНЫЙ номер отсчёта, с которого начинается
первый символ преамбулы LoRa-пакета (в передискретизированном потоке,
где 1 символ == 2**SF отсчётов).

Метод (после экспериментов оказался надёжнее согласованной фильтрации,
см. пояснение ниже):

1. energy_detect.find_burst_regions() уже даёт ГРУБУЮ оценку начала
   пакета -- с точностью в пределах одного символа (несколько тысяч
   отсчётов), этого достаточно как отправной точки.
2. Берём один символ (N=2**SF отсчётов), начиная от этой грубой оценки,
   и дечирпим его. Поскольку преамбула Meshtastic -- это много одинаковых
   up-чирпов символа "0" подряд, ЛЮБОЕ окно длиной N, выровненное по сетке
   символов (пусть и с произвольным сдвигом относительно истинного начала),
   даёт СТАБИЛЬНЫЙ, но не обязательно нулевой бин b -- потому что скользящее
   окно внутри периодического сигнала эквивалентно "наблюдению" чирпа с
   циклическим сдвигом b (это ровно то же самое свойство, которым LoRa
   кодирует символы). Значит величина b -- это и есть ошибка нашей грубой
   привязки (в отсчётах), и её можно скомпенсировать ОДНИМ вычислением,
   без перебора:
       aligned_start = coarse_start + (N - b) % N

   (Почему именно "N - b", а не "b" -- определено экспериментально и
   согласуется с тем, как в chirp.py закодирован циклический сдвиг символа;
   см. комментарий в chirp.make_upchirp про знак сдвига.)

   Мы НЕ используем согласованную фильтрацию (кросс-корреляцию с целым
   шаблоном преамбулы) как основной метод, потому что она давала обманчивые
   результаты: из-за небольшого расхождения тактовых частот передатчика и
   приёмника (sample clock offset) длинная преамбула слегка "плывёт"
   относительно идеального шаблона, и пик корреляции по всей преамбуле
   получается смазанным / не там, где нужно. Точная привязка по ОДНОМУ
   символу гораздо устойчивее.
3. Проверяем находку: дечирпим несколько идущих подряд символов от
   aligned_start и требуем, чтобы они были близки друг к другу
   (это отсекает случайные ложные срабатывания вроде переходных
   процессов на старте записи).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import config
from .chirp import make_downchirp, dechirp_symbol
from .energy_detect import BurstRegion
from .resample import resample_to_bandwidth


@dataclass
class PreambleMatch:
    """Результат успешной синхронизации по преамбуле."""

    # Индекс отсчёта начала преамбулы В ИСХОДНОМ (не передискретизированном)
    # потоке -- удобно, чтобы потом вырезать сырые данные пакета.
    start_sample_original: int

    # То же самое, но в передискретизированном потоке (fs == BANDWIDTH_HZ),
    # где 1 символ == 2**SF отсчётов -- этим будем пользоваться при
    # демодуляции заголовка/данных на следующих этапах.
    start_sample_resampled: int

    # Бины первых num_preamble_symbols символов после найденной привязки
    # (для отладки/проверки -- должны быть маленькими и близкими друг к другу).
    preamble_bins: list[int]

    # Средняя амплитуда пика по этим символам (оценка SNR).
    mean_peak_magnitude: float


def _estimate_burst_onset_resampled(
    resampled: np.ndarray, smoothing: int = 50, noise_ref_samples: int = 500
) -> int:
    """Грубо оценить, с какого отсчёта (в передискретизированном потоке)
    начинается реальный сигнал внутри региона (регион уже вырезан вокруг
    всплеска энергии, но с запасом/padding по краям -- нужно уточнить
    начало уже внутри этого небольшого куска).

    ВАЖНО: уровень шума оцениваем ТОЛЬКО по самому началу региона
    (первые noise_ref_samples отсчётов). Мы не можем использовать медиану
    по всему региону, как в energy_detect.py -- там весь регион в среднем
    в основном ТИШИНА (одиночный короткий пакет на фоне долгой записи),
    а здесь регион уже вырезан вплотную вокруг пакета, и если сам пакет
    длинный, сигнал может занимать БОЛЬШУЮ ЧАСТЬ региона -- тогда медиана
    по всему региону покажет уровень сигнала, а не шума, и порог "сигнал
    выше медианы в 10 раз" никогда не сработает. Начало региона же благодаря
    padding в find_burst_regions гарантированно захватывает чистую тишину.
    """
    power = np.abs(resampled) ** 2
    kernel = np.ones(smoothing) / smoothing
    smooth = np.convolve(power, kernel, mode="same")

    noise_floor = np.median(smooth[:noise_ref_samples])
    noise_floor = max(noise_floor, 1e-12)
    threshold = noise_floor * 10  # +10 дБ над фоном

    above = np.where(smooth > threshold)[0]
    if len(above) == 0:
        return 0
    return int(above[0])


def find_preamble(
    iq: np.ndarray,
    region: BurstRegion,
    bw: int,
    sf: int,
    orig_fs: int,
    num_preamble_symbols: int = config.PREAMBLE_SYMBOLS,
    max_bin_spread: int = 4,
) -> PreambleMatch | None:
    """Найти точное начало преамбулы внутри заданного региона.

    Возвращает None, если внутри региона не нашлось ничего похожего на
    преамбулу -- значит, это был ложный регион (например, переходный
    процесс в начале записи, а не реальный LoRa-пакет).
    """
    raw_segment = iq[region.start:region.end]
    resampled, new_fs = resample_to_bandwidth(raw_segment, orig_fs, bw)
    assert new_fs == bw, f"Ожидали new_fs=={bw}, получили {new_fs}"

    N = 2 ** sf
    down = make_downchirp(sf, bw, fs=bw)

    coarse_start = _estimate_burst_onset_resampled(resampled)
    if coarse_start + N > len(resampled):
        return None  # регион слишком короткий -- точно не пакет

    # Один dechirp грубого окна сразу даёт нужную поправку (см. docstring).
    first_segment = resampled[coarse_start: coarse_start + N]
    b, _, _ = dechirp_symbol(first_segment, down)
    aligned_start = coarse_start + (N - b) % N
    
    if aligned_start + num_preamble_symbols * N > len(resampled):
        return None

    # Проверяем находку по нескольким следующим символам подряд.
    bins = []
    peaks = []
    for i in range(num_preamble_symbols):
        seg = resampled[aligned_start + i * N: aligned_start + (i + 1) * N]
        s, p, _ = dechirp_symbol(seg, down)
        bins.append(s)
        peaks.append(p)

    # "Настоящая" преамбула -- символы должны быть близки друг к другу
    # (расхождение из-за небольшого дрейфа тактовой частоты допустимо,
    # но не хаотичный разброс, как было бы на случайном шуме/данных).
    spread = max(bins) - min(bins)
    
    print(
        f"Preamble diagnostic: "
        f"bins={bins}, "
        f"spread={spread}, "
        f"max_bin_spread={max_bin_spread}, "
        f"peaks={peaks}"
    )
    
    if spread > max_bin_spread:
        print("Preamble rejected: bin spread too large")
        return None

    start_sample_original = region.start + round(aligned_start * orig_fs / bw)

    return PreambleMatch(
        start_sample_original=start_sample_original,
        start_sample_resampled=aligned_start,
        preamble_bins=bins,
        mean_peak_magnitude=float(np.mean(peaks)),
    )