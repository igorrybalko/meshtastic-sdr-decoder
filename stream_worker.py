import asyncio

from PySide6.QtCore import QThread, Signal

from rtl_source_async import read_iq_stream_async
import numpy as np

from config import HEADER_OFFSET

from stream_buffer import IQStreamBuffer
from detector import find_packet_starts
from packet_decoder import decode_packet

from lora_header.extract_header import get_header
from lora_header.chirp import make_upchirp

from telegram_publisher import TelegramPublisher


WINDOW_SIZE = 2_097_152
OVERLAP = 131_072
PACKET_BUFFER_SIZE = 5_000_000


class SDRWorker(QThread):
    iq_block = Signal(object)
    status = Signal(str)
    error = Signal(str)
    packet_decoded = Signal(dict)

    def __init__(
        self,
        center_freq,
        sample_rate,
        gain,
        bandwidth,
        spreading_factor,
        coding_rate,
        modem_preset=None,
        block_size=16384,
    ):
        super().__init__()

        self.center_freq = center_freq
        self.sample_rate = sample_rate
        self.gain = gain
        self.block_size = block_size
        self.bandwidth = bandwidth
        self.spreading_factor = spreading_factor
        self.coding_rate = coding_rate
        self.n = 2 ** self.spreading_factor
        self.modem_preset = modem_preset

        self._stop_requested = False
        
        self.samples_per_symbol = (
            self.sample_rate
            * (self.n)
            / self.bandwidth
        )

        self.ref = make_upchirp(
            self.spreading_factor,
            self.bandwidth,
            fs=self.bandwidth,
        )

        self.buffer = IQStreamBuffer(
            window_size=WINDOW_SIZE,
            overlap=OVERLAP,
        )
        
        self.packet_buffer = None
        self.last_window_end = None
        
        self.telegram_publisher = TelegramPublisher()
        if self.telegram_publisher.enabled:
            print("Telegram publishing enabled (strict PKI text-message policy)")
        else:
            print("Telegram publishing disabled in settings or credentials are missing")


    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            asyncio.run(self._run())

        except Exception as exc:
            self.error.emit(str(exc))

    
    async def _run(self):
        self.status.emit("Connecting to RTL-SDR...")

        try:
            async for samples in read_iq_stream_async(
                center_freq=self.center_freq,
                sample_rate=self.sample_rate,
                gain=self.gain,
                block_size=self.block_size,
            ):
                if self._stop_requested:
                    break

                self.iq_block.emit(samples)

                windows = self.buffer.add(samples)

                for window_start, window in windows:
                    window_end = window_start + len(window)

                    # Ищем начало пакета
                    if self.packet_buffer is None:
                        try:
                            starts = find_packet_starts(
                                window,
                                self.samples_per_symbol,
                                self.ref,
                                self.n,
                            )
                        except RuntimeError as exc:
                            print(f"Detector: {exc}")
                            continue

                        if not starts:
                            continue

                        local_start = starts[0]
                        packet_start = window_start + local_start

                        print(
                            f"Meshtastic packet detected at "
                            f"{packet_start:,}"
                        )

                        self.status.emit(
                            f"Packet detected at {packet_start:,}"
                        )

                        self.packet_buffer = (
                            window[local_start:].copy()
                        )
                        self.last_window_end = window_end

                        continue

                    # Пакет уже обнаружен.
                    # Добавляем только новые samples,
                    # не копируя повторно перекрытие окон.
                    new_start = max(
                        self.last_window_end,
                        window_start + OVERLAP,
                    )

                    local_new_start = new_start - window_start

                    if local_new_start < len(window):
                        new_samples = window[local_new_start:]

                        self.packet_buffer = np.concatenate(
                            (
                                self.packet_buffer,
                                new_samples,
                            )
                        )

                    self.last_window_end = window_end

                    self.status.emit(
                        f"Collecting packet: "
                        f"{len(self.packet_buffer):,} samples"
                    )

                    # Достаточно IQ для первой попытки декодирования
                    if len(self.packet_buffer) < PACKET_BUFFER_SIZE:
                        continue

                    print("Trying to decode Meshtastic header...")

                    try:
                        mh, packet_length, data_bytes = get_header(
                            self.packet_buffer,
                            self.bandwidth,
                            self.spreading_factor,
                            0.0,
                            modem_preset=self.modem_preset
                        )

                        print("Header decoded:")
                        print("  Packet length:", packet_length)
                        print("  Mesh header:", mh)
                        print("  Data bytes:", data_bytes.hex())
                        
                        payload_start = (
                            20 * self.samples_per_symbol
                            + 8 * self.samples_per_symbol
                            + HEADER_OFFSET
                        )

                        print("Trying full packet decoder...")

                        result = decode_packet(
                            samples=self.packet_buffer,
                            mh=mh,
                            packet_length=packet_length,
                            data_bytes=data_bytes,
                            payload_start=payload_start,
                            ref=self.ref,
                            ss=self.samples_per_symbol,
                            n=self.n,
                            coding_rate=self.coding_rate,
                        )

                        print("Packet decoder result:", result)
                        
                        if isinstance(result, dict):
                            self.packet_decoded.emit(result)
                                          
                            # Only verified PKI text messages are queued for Telegram.
                            self.telegram_publisher.publish(result)


                        self.status.emit(
                            f"Header decoded: {packet_length} bytes"
                        )

                    except Exception as exc:
                        import traceback

                        print("Packet decoding failed:")
                        traceback.print_exc()

                        self.status.emit(
                            f"Packet decoding failed: {exc}"
                        )

                    # На этом этапе проверяем заголовок.
                    # Полный декодер подключим следующим шагом.
                    self.packet_buffer = None
                    self.last_window_end = None

        finally:
            self.telegram_publisher.close()
            self.status.emit("Stopped")
