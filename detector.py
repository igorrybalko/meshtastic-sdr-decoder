import numpy as np
from scipy.signal import resample


def preamble_correlation_score(samples, start, ss, ref, n):
    """
    Calculate correlation quality for several consecutive
    LoRa preamble upchirps.
    """

    raw_count = int(np.ceil(ss)) + 2

    scores = []

    for s in range(6):
        pos = start + s * ss
        st = int(round(pos))

        raw = samples[st:st + raw_count]

        if len(raw) < raw_count:
            return None

        sym = resample(raw, n)

        corr = np.vdot(ref, sym)

        # Normalize by signal energy so that the score
        # depends less on absolute signal amplitude.
        denom = np.linalg.norm(ref) * np.linalg.norm(sym)

        if denom <= 0:
            return None

        score = abs(corr) / denom

        scores.append(score)
        
        #print(f"Preamble correlation scores: {scores}")

    return float(np.mean(scores))


def refine_preamble_timing(samples, rough_start, ss, ref, n):
    # Грубый детектор уже достаточно близок.
    # Ищем точное начало только в пределах четверти символа.
    search_radius = int(0.25 * ss)

    search_from = max(0, int(rough_start - search_radius))
    search_to = min(
        len(samples) - int(8 * ss),
        int(rough_start + search_radius)
    )

    coarse_step = 4

    best_pos = None
    best_score = -np.inf

    for start in range(search_from, search_to + 1, coarse_step):
        score = preamble_correlation_score(samples, start, ss, ref, n)

        if score is None:
            continue

        if score > best_score:
            best_score = score
            best_pos = start

    if best_pos is None:
        raise RuntimeError("Unable to refine LoRa chirp timing")

    # Точная проверка вокруг лучшей позиции
    fine_from = max(search_from, best_pos - coarse_step)
    fine_to = min(search_to, best_pos + coarse_step)

    for start in range(fine_from, fine_to + 1):
        score = preamble_correlation_score(samples, start, ss, ref, n)

        if score is None:
            continue

        if score > best_score:
            best_score = score
            best_pos = start

    return best_pos, best_score


def _estimate_noise_threshold(samples):
    power = np.abs(samples) ** 2

    window = 1024
    kernel = np.ones(window, dtype=np.float32) / window

    smooth = np.convolve(
        power.astype(np.float32),
        kernel,
        mode="same"
    )

    noise_region = smooth[:min(len(smooth), 1_000_000)]

    noise_median = float(np.median(noise_region))
    noise_level = float(
        np.median(np.abs(noise_region - noise_median))
    )

    threshold = noise_median + 12.0 * noise_level

    if threshold <= noise_median:
        threshold = noise_median * 3.0

    return threshold


def find_rf_bursts(samples, ss, threshold=None):
    """
    Find all significant RF bursts in an IQ capture.

    Returns:
        List of (start, end) sample positions.
    """

    power = np.abs(samples) ** 2

    window = 1024
    kernel = np.ones(window, dtype=np.float32) / window

    smooth = np.convolve(
        power.astype(np.float32),
        kernel,
        mode="same"
    )

    if threshold is None:
        threshold = _estimate_noise_threshold(samples)

    active = smooth > threshold
    
    # print(
    #     f"Threshold: {threshold:.8f}, "
    #     f"Max power: {float(np.max(smooth)):.8f}"
    # )

    changes = np.diff(active.astype(np.int8))

    starts = np.flatnonzero(changes == 1) + 1
    ends = np.flatnonzero(changes == -1) + 1

    if active[0]:
        starts = np.r_[0, starts]

    if active[-1]:
        ends = np.r_[ends, len(active)]

    # Ignore short energy spikes.
    min_length = int(2 * ss)

    bursts = [
        (int(s), int(e))
        for s, e in zip(starts, ends)
        if e - s > min_length
    ]

    return bursts


def find_packet_start(samples, ss, ref, n, burst_start=None, threshold=None):
    """
    Fast LoRa burst detector.

    Stage 1:
        Find the high-energy RF burst.

    Stage 2:
        Search for the beginning of the LoRa preamble
        around the detected burst edge.
    """

    # ---------------------------------------------------------
    # Stage 1: energy detection
    # ---------------------------------------------------------

    power = np.abs(samples) ** 2

    # Smooth the power over a short interval.
    # 1024 samples ~= 1 ms at 1.024 Msps.
    window = 1024

    kernel = np.ones(window, dtype=np.float32) / window

    smooth = np.convolve(
        power.astype(np.float32),
        kernel,
        mode="same"
    )

    # Estimate noise from the first part of the capture.
    noise_region = smooth[:min(len(smooth), 1_000_000)]

    noise_median = float(np.median(noise_region))
    noise_level = float(np.median(np.abs(noise_region - noise_median)))

    threshold = noise_median + 12.0 * noise_level

    # Safety floor for unusual captures.
    if threshold <= noise_median:
        threshold = noise_median * 3.0

    active = smooth > threshold

    # Find contiguous active regions.
    changes = np.diff(active.astype(np.int8))

    starts = np.flatnonzero(changes == 1) + 1
    ends = np.flatnonzero(changes == -1) + 1

    if active[0]:
        starts = np.r_[0, starts]

    if active[-1]:
        ends = np.r_[ends, len(active)]

    if burst_start is None:
        if len(starts) == 0:
            raise RuntimeError("No RF burst found")

        regions = [
            (int(e - s), int(s), int(e))
            for s, e in zip(starts, ends)
            if e - s > int(2 * ss)
        ]

        if not regions:
            raise RuntimeError("No sufficiently long RF burst found")

        regions.sort(reverse=True)

        burst_len, burst_start, burst_end = regions[0]
    else:
        print(f"RF burst candidate at {burst_start:,}")

    # ---------------------------------------------------------
    # Stage 2: refine packet start
    # ---------------------------------------------------------

    raw_count = int(np.ceil(ss)) + 2

    # The energy detector usually starts slightly before the
    # actual LoRa symbol boundary. The real packet start should
    # therefore be close to the beginning of the RF burst.
    search_from = burst_start
    search_to = min(
        len(samples) - int(8 * ss),
        burst_start + int(2 * ss)
    )

    step = 32

    candidates = []

    for start in range(search_from, search_to, step):

        peak_bins = []
        peak_ratios = []

        for s in range(6):

            pos = start + s * ss
            st = int(round(pos))

            raw = samples[st:st + raw_count]

            if len(raw) < raw_count:
                break

            sym = resample(raw, n)

            mag = np.abs(
                np.fft.fft(sym * np.conj(ref))
            )

            peak_bin = int(np.argmax(mag))
            peak = float(mag[peak_bin])
            median = float(np.median(mag))

            if median <= 0:
                break

            peak_bins.append(peak_bin)
            peak_ratios.append(peak / median)

        if len(peak_bins) != 6:
            continue

        # A LoRa preamble consists of repeated upchirps.
        # After dechirping they should produce approximately
        # the same FFT peak in every symbol.

        bin_std = float(np.std(peak_bins))
        ratio_mean = float(np.mean(peak_ratios))

        # Strong peak + stable frequency bin.
        score = np.log(ratio_mean + 1e-9) - 0.05 * bin_std

        candidates.append(
            (score, start, bin_std, ratio_mean, peak_bins)
        )

    if not candidates:
        raise RuntimeError(
            "Unable to find LoRa preamble candidate"
        )

    # Sort by quality first.
    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    # Instead of simply taking the strongest candidate,
    # walk backwards to find the earliest candidate that
    # has essentially the same preamble quality.

    best_score = candidates[0][0]

    # Allow candidates within 97% of the best score.
    quality_limit = best_score - 0.03 * abs(best_score)

    good = [
        c for c in candidates
        if c[0] >= quality_limit
    ]

    # The earliest stable candidate is what we want.
    best = min(
        good,
        key=lambda x: x[1]
    )

    best_score, best_start, bin_std, ratio_mean, peak_bins = best
    
    # if bin_std > 20:
    #     raise RuntimeError(
    #         f"Preamble candidate rejected: bin_std={bin_std:.1f}"
    #     )

    # Final timing refinement.
    exact_start, corr_score = refine_preamble_timing(
        samples,
        best_start,
        ss,
        ref,
        n,
    )

    return exact_start


def find_packet_starts(samples, ss, ref, n, threshold=None):
    """
    Find and refine the start of every detected LoRa packet.
    """

    bursts = find_rf_bursts(samples, ss, threshold)

    if not bursts:
        raise RuntimeError("No RF bursts found")

    packet_starts = []

    for i, (start, end) in enumerate(bursts, 1):

        try:
            packet_start = find_packet_start(
                samples,
                ss,
                ref,
                n,
                burst_start=start,
                threshold=threshold
            )
            
            corr = preamble_correlation_score(
                samples,
                packet_start,
                ss,
                ref,
                n,
            )

            print(
                f"Burst {i}: "
                f"candidate={packet_start}, "
                f"correlation={corr}"
            )
            
            packet_starts.append(packet_start)

        except RuntimeError as exc:
            print(f"Burst {i}: preamble not found: {exc}")

    return packet_starts

def find_packet_starts_chunked(
    samples,
    ss,
    ref,
    n,
    window_size=2_097_152,
    overlap=131_072,
):
    """Find packet starts in overlapping IQ windows."""

    threshold = _estimate_noise_threshold(samples[:min(len(samples), 1_000_000)])
    step = window_size - overlap
    packet_starts = []

    offset = 0

    while offset < len(samples):
        end = min(offset + window_size, len(samples))
        window = samples[offset:end]

        if len(window) < int(8 * ss):
            break

        try:
            starts = find_packet_starts(window, ss, ref, n, threshold=threshold)
            
            print(
                f"Window {offset:,}–{end:,}: "
                f"local starts = {starts}"
            )

            for start in starts:
                absolute_start = offset + start
                packet_starts.append(absolute_start)

        # except RuntimeError:
        #     pass
        
        except RuntimeError as exc:
            print(
                f"Window {offset:,}–{end:,}: "
                f"{exc}"
            )

        offset += step

    # Remove duplicate detections caused by overlapping windows.
    packet_starts.sort()

    unique = []
    min_distance = int(ss)

    for start in packet_starts:
        if not unique or start - unique[-1] > min_distance:
            unique.append(start)

    return unique