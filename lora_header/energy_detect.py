"""
energy_detect.py
-----------------
Дешёвый первый фильтр: ищем участки записи, где мощность сигнала заметно
выше фонового шума. Это не даёт точной синхронизации по символам (для
этого нужен chirp.py + согласованная фильтрация), зато отбрасывает
99% "пустой" записи почти бесплатно — и уже потом дорогой поиск преамбулы
запускаем только внутри найденных окон.

Идея простая (это по сути то, что мы делали руками на этапе анализа):
1. Берём |iq|^2 — мгновенную мощность.
2. Сглаживаем скользящим средним (чтобы не реагировать на случайные
   выбросы шума отсчёт-в-отсчёт).
3. Оцениваем уровень фона (шума) как медиану — она устойчива к тому,
   что сам пакет является "выбросом" в данных.
4. Всё, что выше фона на threshold_db дБ — считаем "пакетом".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class BurstRegion:
    """Найденный всплеск энергии в ИСХОДНЫХ (не передискретизированных)
    отсчётах, с запасом (padding) по краям на случай неточной границы.
    """
    start: int
    end: int

    def __len__(self) -> int:
        return self.end - self.start


def find_burst_regions(
    iq: np.ndarray,
    smoothing_samples: int = 500,
    threshold_db: float = 10.0,
    min_duration_samples: int = 2000,
    padding_samples: int = 20_000,
) -> list[BurstRegion]:
    """Найти участки записи с мощностью выше фона.

    smoothing_samples   -- окно сглаживания мощности.
    threshold_db        -- порог превышения над медианным уровнем шума, дБ.
    min_duration_samples -- отбрасывать всплески короче этого (защита от
                             одиночных импульсных помех).
    padding_samples      -- добавить запас по краям найденного региона,
                             чтобы точно не обрезать начало/конец пакета
                             (преамбулу или хвост payload).
    """
    power = np.abs(iq) ** 2

    kernel = np.ones(smoothing_samples) / smoothing_samples
    smooth_power = np.convolve(power, kernel, mode="same")

    noise_floor = np.median(smooth_power)
    # Защита от log(0): медиана мощности шума на реальных данных положительна,
    # но на всякий случай подстрахуемся крошечным эпсилон.
    noise_floor = max(noise_floor, 1e-12)
    threshold = noise_floor * (10 ** (threshold_db / 10))

    is_burst = smooth_power > threshold

    # Находим границы непрерывных участков is_burst == True.
    # np.diff даёт +1 на переходе False->True (начало) и -1 на True->False (конец).
    diff = np.diff(is_burst.astype(np.int8))
    starts = np.where(diff == 1)[0] + 1
    ends = np.where(diff == -1)[0] + 1

    # Крайние случаи: если запись начинается/заканчивается уже "внутри" всплеска.
    if is_burst[0]:
        starts = np.insert(starts, 0, 0)
    if is_burst[-1]:
        ends = np.append(ends, len(is_burst))

    regions: list[BurstRegion] = []
    for s, e in zip(starts, ends):
        if (e - s) < min_duration_samples:
            continue
        padded_start = max(0, s - padding_samples)
        padded_end = min(len(iq), e + padding_samples)
        regions.append(BurstRegion(start=padded_start, end=padded_end))

    return regions