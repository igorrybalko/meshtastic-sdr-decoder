

from __future__ import annotations

import sys

import config
from protocol import parse_radio_header

from .energy_detect import BurstRegion
from .preamble import find_preamble
from .resample import resample_to_bandwidth
from .sfd import find_sfd
from .chirp import make_downchirp, dechirp_symbol
from .header import decode_header
from .payload import decode_payload


def extract_header(
    iq,
    bw,
    sf,
    approx_start_seconds: float,
    margin_before_seconds: float = 0.05,
    margin_after_seconds: float = 5.0,
) -> bytes:
    """Вернуть 16 байт открытого заголовка Meshtastic-пакета.

    approx_start_seconds -- время начала пакета (преамбулы), с точностью
    до, скажем, ~10-20 мс -- этого достаточно, реальная точная
    привязка находится автоматически внутри (см. preamble.find_preamble).

    ВАЖНО: to/from/id передаются открытым текстом, но физически они
    закодированы ТЕМИ ЖЕ символами, что и остальной payload -- то есть
    требуют полного прохода через deinterleave + whitening + сборку байт
    (см. payload.decode_payload). Более ранняя версия этой функции
    пыталась получить эти байты "напрямую", без whitening -- это давало
    неверный результат (см. историю разработки в README.md: порядок
    ниблов и bit_ofs для whitening были определены неверно до тех пор,
    пока не появился реальный известный to/from для сверки).
    """

    center_sample = int(approx_start_seconds * config.SAMPLE_RATE_HZ)
    before_samples = int(margin_before_seconds * config.SAMPLE_RATE_HZ)
    after_samples = int(margin_after_seconds * config.SAMPLE_RATE_HZ)
    region = BurstRegion(
        start=max(0, center_sample - before_samples),
        end=min(len(iq), center_sample + after_samples),
    )

    match = find_preamble(iq, region, bw, sf, config.SAMPLE_RATE_HZ)
    if match is None:
        raise RuntimeError(
            "Преамбула не найдена рядом с указанным временем -- "
            "проверьте approx_start_seconds или увеличьте margin_before/after_seconds"
        )

    raw_segment = iq[region.start:region.end]
    resampled, new_fs = resample_to_bandwidth(raw_segment, config.SAMPLE_RATE_HZ, bw)

    sfd_match = find_sfd(resampled, match, bw, sf)
    if sfd_match is None:
        raise RuntimeError("Не удалось найти SFD -- возможно, окно поиска обрезало пакет")

    N = 2 ** sf
    down = make_downchirp(sf, bw, fs=bw)
    header_start = sfd_match.header_start_resampled

    header_bins = [
        dechirp_symbol(resampled[header_start + i * N: header_start + (i + 1) * N], down)[0]
        for i in range(8)
    ]
    hdr = decode_header(header_bins, sf)
    if not hdr.checksum_valid:
        raise RuntimeError(
            f"Контрольная сумма заголовка не сошлась ({hdr}) -- "
            "результату дальше нельзя доверять"
        )

    # Декодируем payload (whitening + верный порядок ниблов уже внутри) и
    # берём только первые 16 байт -- это и есть открытый заголовок пакета.
    data_bytes, parity_ok = decode_payload(resampled, header_start, hdr, sf, bw)
    
    if not all(parity_ok[:8]):  # первые ~8 кодовых слов формируют эти 16 байт
        print(
            "ВНИМАНИЕ: не все кодовые слова, формирующие to/from/id, "
            "прошли проверку чётности -- результат может быть неточным",
            file=sys.stderr,
        )

    return data_bytes, hdr.payload_length


def get_header(samples, bw, sf, start_seconds):

    data_bytes, payload_length = extract_header(samples, bw, sf, start_seconds)
    mh = parse_radio_header(data_bytes)

    print(f"  to   = !{mh['to']:08x}")
    print(f"  from = !{mh['from']:08x}")
    print(f"  id   = 0x{mh['packet_id']:08x}")
    print(f"  hop_limit={mh['hop_limit']}  want_ack={mh['want_ack']}  "
          f"via_mqtt={mh['via_mqtt']}  hop_start={mh['hop_start']}")
    print(f"  channel_hash = 0x{mh['channel_hash']:02x}")
    
    return mh, payload_length, data_bytes



