
import numpy as np


class IQStreamBuffer:
    def __init__(self, window_size=2_097_152, overlap=131_072):
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        if overlap < 0 or overlap >= window_size:
            raise ValueError("Invalid overlap")

        self.window_size = window_size
        self.overlap = overlap
        self.step = window_size - overlap

        self.buffer = np.empty(0, dtype=np.complex64)
        self.buffer_start = 0
        self.total_samples = 0

    def add(self, samples):
        samples = np.asarray(samples, dtype=np.complex64)

        self.buffer = np.concatenate((self.buffer, samples))
        self.total_samples += len(samples)

        windows = []

        while len(self.buffer) >= self.window_size:
            windows.append((
                self.buffer_start,
                self.buffer[:self.window_size].copy()
            ))

            self.buffer = self.buffer[self.step:]
            self.buffer_start += self.step

        return windows