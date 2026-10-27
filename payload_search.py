import heapq

import numpy as np
from scipy.signal import resample

from phy import gray_inverse, gray_raw_symbol

TOPK = 16
KEEP = 5_000

def _cr_symbol_count(coding_rate):
    """Return the number of physical symbols per diagonal block."""
    if coding_rate not in (5, 8):
        raise ValueError("coding_rate must be 5 (CR 4/5) or 8 (CR 4/8)")
    return coding_rate


def _encode_parity(nibble, coding_rate):
    """Return the encoded parity bits for a 4-bit LoRa data nibble."""
    d0 = (nibble >> 0) & 1
    d1 = (nibble >> 1) & 1
    d2 = (nibble >> 2) & 1
    d3 = (nibble >> 3) & 1

    if coding_rate == 5:
        # Existing CR 4/5 implementation: one parity bit.
        return [d0 ^ d1 ^ d2 ^ d3]

    # Hamming(8,4) parity equations used by hamming.py.
    return [
        d0 ^ d1 ^ d2,
        d1 ^ d2 ^ d3,
        d0 ^ d1 ^ d3,
        d0 ^ d2 ^ d3,
    ]



def build_spectra(
    samples, ref, num_blocks, payload_start, ss,
    samples_per_chirp=None, coding_rate=5,
):
    symbol_count = _cr_symbol_count(coding_rate)
    n_symbols = int(samples_per_chirp or len(ref))
    raw_count = int(np.ceil(ss)) + 2
    spec = []

    for i in range(num_blocks * symbol_count):
        pos = payload_start + i * ss
        st = int(round(pos))
        raw = samples[st:st + raw_count]
        if len(raw) < raw_count:
            raise ValueError(f"Not enough samples for payload symbol {i}")
        sym = resample(raw, n_symbols)
        mag = np.abs(np.fft.fft(sym * np.conj(ref)))
        idx = np.argpartition(mag, -TOPK)[-TOPK:]
        idx = idx[np.argsort(mag[idx])[::-1]]
        spec.append((idx, mag[idx]))

    return spec, raw_count


def candidate_block(
    block, spec, samples, ref, raw_count, src, payload_start, ss,
    samples_per_chirp=None, coding_rate=5,
):
    """Search one CR 4/5 or CR 4/8 interleaver block.

    For both supported rates, the first four physical symbols carry the
    four data-bit columns. The remaining symbol(s) are reconstructed from
    the code's parity equations. CR 4/5 behavior and candidate tuple format
    are intentionally retained for compatibility with the existing decoder.
    """
    symbol_count = _cr_symbol_count(coding_rate)
    n_symbols = int(samples_per_chirp or len(ref))
    if len(src) != 11 or any(len(row) != symbol_count for row in src):
        raise ValueError(
            f"source map does not match CR 4/{coding_rate}; "
            f"expected 11 rows of {symbol_count} entries"
        )

    start = block * symbol_count
    idxs = [spec[start + i][0] for i in range(symbol_count)]
    mags = [spec[start + i][1] for i in range(symbol_count)]

    # Full spectra are needed only for the symbols reconstructed from parity.
    fullmag = {}
    for i in range(4, symbol_count):
        pos = payload_start + (block * symbol_count + i) * ss
        st = int(round(pos))
        raw = samples[st:st + raw_count]
        if len(raw) < raw_count:
            raise ValueError(f"Not enough samples for payload symbol {block * symbol_count + i}")
        sm = resample(raw, n_symbols)
        fullmag[i] = np.abs(np.fft.fft(sm * np.conj(ref)))

    gs = [np.asarray(gray_raw_symbol(x, n_symbols), dtype=np.int64) for x in idxs]
    K = TOPK
    # The search space remains K**4: enumerate four data-bearing symbols.
    total = K ** 4
    chunk = 100_000
    heap = []

    for base in range(0, total, chunk):
        count = min(chunk, total - base)
        q = np.arange(base, base + count, dtype=np.int64)
        aa = q % K; q //= K
        bb = q % K; q //= K
        cc = q % K; q //= K
        dd = q % K
        choice = [aa, bb, cc, dd]
        known = [gs[i][choice[i]] for i in range(4)]

        # Construct the Gray-coded bit planes of each parity symbol.
        required_gray = [np.zeros(count, dtype=np.int64) for _ in range(symbol_count - 4)]
        for j in range(11):
            data = np.zeros(count, dtype=np.int64)
            for bit in range(4):
                si, sb = src[j][bit]
                if si >= 4:
                    raise RuntimeError("Unexpected data-bit source-map orientation")
                data |= ((known[si] >> (10 - sb)) & 1) << bit

            parity_bits = _encode_parity(data, coding_rate)
            for parity_index, parity in enumerate(parity_bits):
                codeword_column = 4 + parity_index
                si, sb = src[j][codeword_column]
                expected_symbol = codeword_column
                if si != expected_symbol:
                    raise RuntimeError("Unexpected parity source-map orientation")
                required_gray[si - 4] |= parity.astype(np.int64) << (10 - sb)

        recovered = []
        score = (
            np.log(mags[0][aa] + 1e-9)
            + np.log(mags[1][bb] + 1e-9)
            + np.log(mags[2][cc] + 1e-9)
            + np.log(mags[3][dd] + 1e-9)
        )
        for offset, gray_bits in enumerate(required_gray, start=4):
            raw_bin = gray_inverse(gray_bits)
            raw_index = (raw_bin + 1) & (n_symbols - 1)
            recovered.append(raw_index)
            score += np.log(fullmag[offset][raw_index] + 1e-9)

        take_n = min(2000, count)
        take = np.argpartition(score, -take_n)[-take_n:]
        for z in take:
            sc = float(score[z])
            candidate_symbols = [
                int(idxs[0][aa[z]]), int(idxs[1][bb[z]]),
                int(idxs[2][cc[z]]), int(idxs[3][dd[z]]),
            ] + [int(values[z]) for values in recovered]
            tup = (sc, *candidate_symbols)
            if len(heap) < KEEP:
                heapq.heappush(heap, tup)
            elif sc > heap[0][0]:
                heapq.heapreplace(heap, tup)

    return sorted(heap, reverse=True)


def make_plain(candidate_nibs, init_nibs, white, packet_length):
    ns = np.array(init_nibs + list(candidate_nibs), dtype=np.uint8)[:(packet_length + 2) * 2]
    raw = (ns[0::2] | (ns[1::2] << 4)).astype(np.uint8)
    raw[:packet_length] ^= white
    return raw