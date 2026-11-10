"""Streaming classroom enhancement for mono, normalized 16 kHz audio."""

import math

import numpy as np


def input_rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


class ClassroomAudio:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._alpha = math.exp(-2 * math.pi * 100 / sample_rate)
        self._previous_input = 0.0
        self._previous_output = 0.0
        self._noise_power = None
        self._gain = 1.0

    @property
    def gain_db(self) -> float:
        return 20 * math.log10(self._gain)

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not audio.size:
            return audio

        # Carry filter state across PortAudio callbacks.
        high = np.empty(audio.size, dtype=np.float32)
        previous = self._previous_input
        filtered = self._previous_output
        for index, sample in enumerate(audio.tolist()):
            filtered = self._alpha * (filtered + sample - previous)
            high[index] = filtered
            previous = sample
        self._previous_input = previous
        self._previous_output = filtered

        spectrum = np.fft.rfft(high)
        power = np.square(np.abs(spectrum))
        if self._noise_power is None or self._noise_power.shape != power.shape:
            self._noise_power = power * 0.1
        else:
            # Track the floor slowly upward so sustained speech is not learned as noise.
            rate = np.where(power < self._noise_power, 0.12, 0.002)
            self._noise_power += rate * (power - self._noise_power)

        retained = np.maximum(0.70, 1 - 0.55 * self._noise_power / np.maximum(power, 1e-12))
        reduced = np.fft.irfft(spectrum * np.sqrt(retained), n=high.size)
        # Feather the correction at frame edges to avoid block-boundary clicks.
        cleaned = high + (reduced - high) * np.hanning(high.size)

        rms = input_rms(cleaned)
        target = min(3.0, max(0.5, 0.055 / rms)) if rms >= 0.003 else 1.0
        duration = high.size / self.sample_rate
        time_constant = 0.4 if target > self._gain else 0.08
        previous_gain = self._gain
        self._gain += (target - self._gain) * (1 - math.exp(-duration / time_constant))
        ramp = np.linspace(previous_gain, self._gain, high.size)
        return np.clip(cleaned * ramp, -1, 1).astype(np.float32)
