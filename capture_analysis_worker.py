"""QThread wrapper for saved IQ capture analysis."""
from PySide6.QtCore import QThread, Signal

import sys
from dataclasses import dataclass
from typing import Callable, Any

import numpy as np

from lora_header.chirp import make_upchirp
from detector import find_packet_starts
from lora_header.extract_header import get_header
from packet_decoder import decode_packet

from config import HEADER_OFFSET


@dataclass
class Capture:
    iq: np.ndarray
    source: str


def load_capture(path: str) -> Capture:
    """Load and validate a one-dimensional complex IQ capture from a .npy file."""
    iq = np.load(path)

    if iq.ndim != 1:
        raise ValueError("IQ data must be one-dimensional")

    if not np.iscomplexobj(iq):
        raise ValueError("Expected complex IQ samples")

    return Capture(iq=iq, source=path)

def analyze_capture(
    capture_path: str,
    bandwidth_hz: float,
    sf: int,
    n,
    ss,
    sample_rate,
    coding_rate,
    modem_preset,
    progress_callback: Callable[[str], Any] | None = None,
    packet_callback: Callable[[dict], Any] | None = None,
) -> list[dict]:
    """Detect and decode packets in an IQ .npy capture.

    Callbacks are optional. ``packet_callback`` is invoked for each decoded
    result dictionary; the returned list contains those same dictionaries.
    """
    capture = load_capture(capture_path)
    samples = capture.iq

    if progress_callback:
        progress_callback(
            f"Loaded {capture.source}: {len(samples):,} IQ samples; "
            f"{len(samples) / ss:.2f} s"
        )
        progress_callback("Searching for LoRa packet starts...")

    ref = make_upchirp(sf, bandwidth_hz, fs=bandwidth_hz)

    packet_starts = find_packet_starts(samples, ss, ref, n)
    if progress_callback:
        progress_callback(f"Detected {len(packet_starts)} candidate packet(s)")

    results: list[dict] = []
    for index, packet_start in enumerate(packet_starts, start=1):
        
        start_seconds = round(
            packet_start / sample_rate, 3
        )
        
        if progress_callback:
            progress_callback(
                f"Decoding packet {index}/{len(packet_starts)} "
                f"at {start_seconds:.3f} s..."
            )

        try:
            mh, packet_length, data_bytes = get_header(
                samples, bandwidth_hz, sf, start_seconds, modem_preset
            )
            payload_start = packet_start + 20 * ss + 8 * ss + HEADER_OFFSET
            result = decode_packet(
                samples=samples,
                mh=mh,
                packet_length=packet_length,
                data_bytes=data_bytes,
                payload_start=payload_start,
                ref=ref,
                ss=ss,
                n=n,
                coding_rate=coding_rate,
            )
            if isinstance(result, dict):
                results.append(result)
                if packet_callback:
                    packet_callback(result)
            if progress_callback:
                progress_callback(
                    f"Packet {index}: "
                    + ("decoded" if isinstance(result, dict) else "no decoded result")
                )
        except Exception as exc:
            message = f"Packet {index} failed: {type(exc).__name__}: {exc}"
            print(message, file=sys.stderr, flush=True)
            if progress_callback:
                progress_callback(message)

    summary = (
        f"Analysis complete: {len(packet_starts)} candidate(s), "
        f"{len(results)} decoded packet(s)"
    )
    print(summary, flush=True)
    if progress_callback:
        progress_callback(summary)
    return results


class CaptureAnalysisWorker(QThread):
    status = Signal(str)
    error = Signal(str)
    packet_decoded = Signal(dict)

    def __init__(
        self, 
        capture_path, 
        bandwidth, 
        spreading_factor, 
        sample_rate,
        coding_rate,
        modem_preset=None,
    ):
        super().__init__()
        self.capture_path = capture_path
        self.bandwidth = bandwidth
        self.spreading_factor = spreading_factor
        self.sample_rate = sample_rate
        self.n = 2 ** spreading_factor
        self.coding_rate = coding_rate
        self.samples_per_symbol = (
            self.sample_rate
            * (self.n)
            / self.bandwidth
        )
        self.modem_preset = modem_preset

    def run(self):
        try:
            analyze_capture(
                self.capture_path,
                bandwidth_hz=self.bandwidth,
                sf=self.spreading_factor,
                n=self.n,
                ss=self.samples_per_symbol,
                sample_rate=self.sample_rate,
                coding_rate=self.coding_rate,
                modem_preset=self.modem_preset,
                progress_callback=self.status.emit,
                packet_callback=self.packet_decoded.emit,
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.error.emit(f"{type(exc).__name__}: {exc}")
