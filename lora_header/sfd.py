"""
sfd.py
------
Продолжение синхронизации после preamble.py: находим, где заканчивается
служебная часть пакета и начинается заголовок (header) с полезными данными.

Структура LoRa-пакета после преамбулы (стандарт, см. также Meshtastic):
    [N преамбул-символов, все = 0] [2 символа sync word] [2.25 down-чирпа SFD] [header...] [payload...]

Особенность: SFD занимает НЕЦЕЛОЕ число символов (2.25), поэтому после него
все последующие символы (header, payload) сдвинуты на четверть символа
(N/4 отсчётов) относительно "наивной" сетки, продолженной от преамбулы.
Если это не учесть, вся дальнейшая демодуляция будет читать соседние
символы "внахлёст" и давать мусор.

Метод поиска SFD (ВТОРАЯ версия, надёжнее первой -- см. ниже "История"):
Определяем НАПРАВЛЕНИЕ свипа частоты (вверх/вниз) для каждого символьного
окна на "наивной" сетке, продолженной от преамбулы (aligned_start + k*N).
Направление находим напрямую и однозначно: берём мгновенную частоту
(разность фаз соседних отсчётов), разворачиваем (unwrap) и находим наклон
линейной регрессией. Положительный наклон -- up-чирп (преамбула/sync/
header/data), отрицательный -- down-чирп (SFD). Преамбула и sync word --
up, SFD -- down: первый символ с отрицательным наклоном после преамбулы
и есть начало SFD.

История: ПЕРВАЯ версия искала позицию максимума пика при дечирпинге
UP-опорным чирпом (см. git-историю/README). Метод работал на одной записи,
но оказался структурно ненадёжным: пик образует широкий, не всегда чётко
локализованный максимум (в одном случае -- острый треугольник по центру
SFD, а не чёткая граница по краю), из-за чего на второй тестовой записи
находил позицию со сдвигом на пол-символа и все дальнейшие декодирования
ломались. Определение НАПРАВЛЕНИЯ свипа устраняет эту двусмысленность
полностью: оно даёт однозначный (up/down), а не "пиковый" (более/менее
похоже) результат для каждого символьного окна.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .chirp import make_downchirp, dechirp_symbol
from .preamble import PreambleMatch


@dataclass
class SfdMatch:
    # Сколько символов преамбулы реально обнаружено (все бины ~0 подряд).
    # Может отличаться от config.PREAMBLE_SYMBOLS -- это фактическое
    # значение, измеренное по записи.
    preamble_length_detected: int

    # Позиция начала SFD (down-чирпов) в передискретизированном потоке
    # (на "наивной" сетке, продолженной от преамбулы -- т.е. без учёта
    # накопленного дрейфа тактовой частоты, если он есть).
    sfd_start_resampled: int

    # Позиция начала заголовка (header) -- сразу после 2.25 символов SFD.
    # ВОТ ЭТО и есть главный результат модуля: отсюда начинается
    # демодуляция заголовка на следующем этапе.
    header_start_resampled: int


def _count_preamble_symbols(
    resampled: np.ndarray,
    aligned_start: int,
    N: int,
    down_ref: np.ndarray,
    zero_tolerance: int = 4,
    max_symbols_to_check: int = 64,
) -> int:
    """Сколько символов подряд, начиная с aligned_start, дают бин ~0.

    zero_tolerance -- бин считается "нулём", если он в пределах
    [0, zero_tolerance] или [N - zero_tolerance, N) (на случай небольшого
    остаточного смещения по частоте/времени).
    """
    count = 0
    for i in range(max_symbols_to_check):
        seg = resampled[aligned_start + i * N: aligned_start + (i + 1) * N]
        if len(seg) < N:
            break
        s, _, _ = dechirp_symbol(seg, down_ref)
        is_zero_like = s <= zero_tolerance or s >= (N - zero_tolerance)
        if not is_zero_like:
            break
        count += 1
    return count


def _chirp_slope(segment: np.ndarray) -> float:
    """Знак и величина наклона мгновенной частоты внутри символьного окна.

    Положительный -- частота растёт (up-чирп), отрицательный -- падает
    (down-чирп). Считаем через разность фаз соседних отсчётов (мгновенная
    частота), разворачиваем (unwrap) фазу и берём наклон линейной
    регрессией по времени -- устойчиво к шуму, не требует знания
    опорного чирпа/бина.
    """
    inst_freq = np.unwrap(np.angle(segment[1:] * np.conj(segment[:-1])))
    slope = np.polyfit(np.arange(len(inst_freq)), inst_freq, 1)[0]
    return float(slope)


def find_sfd(
    resampled: np.ndarray,
    preamble: PreambleMatch,
    bw: int,
    sf: int,
    max_symbols_after_preamble: int = 8,
) -> SfdMatch | None:
    """Найти точное начало SFD и, соответственно, заголовка пакета.

    resampled -- передискретизированный (fs==bw) кусок записи, ТОТ ЖЕ,
                 что использовался в preamble.find_preamble (чтобы
                 индексы совпадали).
    preamble  -- результат preamble.find_preamble().

    Возвращает None, если не удалось найти два подряд down-чирпа в
    разумных пределах после преамбулы (например, если запись обрывается
    раньше, чем начинается SFD).
    """
    N = 2 ** sf
    down_ref = make_downchirp(sf, bw, fs=bw)

    aligned_start = preamble.start_sample_resampled

    preamble_len = _count_preamble_symbols(resampled, aligned_start, N, down_ref)
    if preamble_len == 0:
        return None

    # Идём по символам на "наивной" сетке сразу после преамбулы и ищем
    # первый ИЗ ДВУХ ПОДРЯД символов с отрицательным наклоном (down-чирп).
    # Одного отрицательного символа недостаточно -- SFD состоит из двух
    # ПОЛНЫХ down-чирпов подряд (плюс ещё четверть символа), поэтому
    # требуем именно пару, чтобы не спутать со случайным шумовым выбросом.
    search_start = aligned_start + preamble_len * N

    slopes = []
    for i in range(max_symbols_after_preamble):
        pos = search_start + i * N
        seg = resampled[pos:pos + N]
        if len(seg) < N:
            return None
        slopes.append(_chirp_slope(seg))

    sfd_offset = None
    for i in range(len(slopes) - 1):
        if slopes[i] < 0 and slopes[i + 1] < 0:
            sfd_offset = i
            break

    if sfd_offset is None:
        return None

    sfd_start = search_start + sfd_offset * N

    # SFD занимает 2.25 символа -- заголовок начинается сразу за ним.
    header_start = sfd_start + round(2.25 * N)

    return SfdMatch(
        preamble_length_detected=preamble_len,
        sfd_start_resampled=sfd_start,
        header_start_resampled=header_start,
    )