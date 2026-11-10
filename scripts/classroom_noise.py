"""Deterministic classroom-like noise for the ADR-003 A/B evaluation."""

import numpy as np


def mix_classroom_noise(audio: np.ndarray, sample_rate: int, snr_db: float = 10.0) -> np.ndarray:
    rng = np.random.default_rng(20261110)
    time = np.arange(audio.size, dtype=np.float64) / sample_rate
    ventilation = np.sin(2 * np.pi * 48 * time) + 0.45 * np.sin(2 * np.pi * 72 * time)
    # Soft syllable-like babble plus brief rustling, without words from the reference.
    babble = np.zeros(audio.size)
    for frequency, pace in ((170, 3.1), (240, 4.3), (340, 2.7)):
        phase = rng.uniform(0, 2 * np.pi)
        envelope = np.maximum(0, np.sin(2 * np.pi * pace * time + phase)) ** 2
        babble += envelope * np.sin(2 * np.pi * frequency * time + phase)
    rustle = rng.standard_normal(audio.size) * np.maximum(0, np.sin(2 * np.pi * 0.38 * time)) ** 12
    noise = ventilation + 0.35 * babble + 0.22 * rustle
    signal_power = np.mean(np.square(audio, dtype=np.float64))
    noise_power = np.mean(noise ** 2)
    if signal_power <= 0 or noise_power <= 0:
        return audio
    mixed = audio + noise * np.sqrt(signal_power / (noise_power * 10 ** (snr_db / 10)))
    peak = float(np.max(np.abs(mixed)))
    return (mixed / max(1.0, peak)).astype(np.float32)
