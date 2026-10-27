"""LoRa PHY helpers used by the current Meshtastic SDR prototype."""
import numpy as np


def _diag(syms):
    rows = []
    for x, v in enumerate(syms, start=1):
        bits = np.array([(v >> i) & 1 for i in range(10, -1, -1)], dtype=np.uint8)
        rows.append(np.roll(bits, 1 - x))
    mat = np.array(rows).T
    out = []
    for row in mat:
        val = 0
        for i, b in enumerate(row):
            val |= int(b) << i
        out.append(val)
    return list(reversed(out))


def source_map(symbol_count=5):
    """Map encoded codeword bits to physical-symbol bits.

    ``symbol_count`` is the number of physical symbols in one diagonal
    interleaver block. The current project supports CR 4/5 (5 symbols) and
    CR 4/8 (8 symbols). This function builds the interleaver map only; it
    does not implement CR-specific parity checking or candidate recovery.
    """
    if symbol_count not in (5, 8):
        raise ValueError("symbol_count must be 5 (CR 4/5) or 8 (CR 4/8)")

    src = []
    for j in range(11):
        row = []
        for bit_column in range(symbol_count):
            found = None
            for symbol_index in range(symbol_count):
                for symbol_bit in range(11):
                    syms = [0] * symbol_count
                    syms[symbol_index] = 1 << (10 - symbol_bit)
                    if (_diag(syms)[j] >> bit_column) & 1:
                        found = (symbol_index, symbol_bit)
                        break
                if found is not None:
                    break
            row.append(found)
        src.append(row)
    return src


# Preserve the existing CR 4/5 map and expose the CR 4/8 interleaver map.
SRC = source_map(5)
SRC_CR48 = source_map(8)


def decode_block(g, coding_rate=5):
    """Extract the four data bits from one interleaver block.

    This remains a nibble extractor, not a full CR 4/8 error-correcting
    decoder. CR 4/8 payload candidate recovery is handled separately.
    """
    if coding_rate == 5:
        src = SRC
        expected_symbols = 5
    elif coding_rate == 8:
        src = SRC_CR48
        expected_symbols = 8
    else:
        raise ValueError("coding_rate must be 5 (CR 4/5) or 8 (CR 4/8)")

    if len(g) != expected_symbols:
        raise ValueError(
            f"CR 4/{coding_rate} block requires {expected_symbols} symbols; got {len(g)}"
        )

    out = []
    for j in range(11):
        v = 0
        for bit_column, (symbol_index, symbol_bit) in enumerate(src[j]):
            v |= ((int(g[symbol_index]) >> (10 - symbol_bit)) & 1) << bit_column
        out.append(v & 0x0F)
    return out


def gray_raw_symbol(raw_bin, n):
    v = (np.asarray(raw_bin, dtype=np.int64) - 1) & (n - 1)
    return v ^ (v >> 1)


def gray_inverse(x):
    x = np.asarray(x, dtype=np.int64).copy()
    result = x.copy()
    shift = result >> 1
    while np.any(shift):
        result ^= shift
        shift >>= 1
    return result
