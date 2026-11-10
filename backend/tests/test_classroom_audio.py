import queue
from pathlib import Path

import numpy as np

from audio.capture import AudioCapture
from audio.classroom import ClassroomAudio, input_rms
from audio.vad import VoiceActivityDetector


def tone(frequency, amplitude, samples=480):
    time = np.arange(samples) / 16000
    return (amplitude * np.sin(2 * np.pi * frequency * time)).astype(np.float32)


def test_enhancement_filters_hum_and_limits_output():
    rumble = ClassroomAudio()
    voice = ClassroomAudio()
    for _ in range(32):
        low = rumble.process(tone(33.333333, 0.001))
        high = voice.process(tone(1000, 0.001))
    assert input_rms(low) < input_rms(high) * 0.65
    loud = voice.process(tone(1000, 1.0))
    assert loud.dtype == np.float32
    assert np.max(np.abs(loud)) <= 1.0


def test_gain_raises_quiet_voice_without_amplifying_silence():
    processor = ClassroomAudio()
    voice = tone(1000, 0.012)
    for _ in range(45):
        result = processor.process(voice)
    assert input_rms(result) > 1.5 * input_rms(voice)
    assert 0 < processor.gain_db < 10
    for _ in range(90):
        result = processor.process(np.zeros_like(voice))
    assert input_rms(result) < 1e-5
    assert abs(processor.gain_db) < 0.1


def test_far_field_uses_lower_threshold_and_longer_padding(monkeypatch):
    monkeypatch.setenv("TINGKEBANLV_DISABLE_VAD_MODEL", "1")
    near = VoiceActivityDetector(lambda *_: None)
    far = VoiceActivityDetector(lambda *_: None, profile="far-field")
    assert far._speech_threshold < near._speech_threshold
    assert far._silence_duration > near._silence_duration
    assert far._speech_pad > near._speech_pad


def test_microphone_switch_signals_capture_worker_without_restarting_pipeline(monkeypatch):
    devices = queue.Queue()
    capture = AudioCapture(lambda _: None, mode="microphone")

    def capture_device(index):
        devices.put(index)
        capture._device_changed.wait(2)

    monkeypatch.setattr(capture, "_capture_device", capture_device)
    capture.start()
    try:
        assert devices.get(timeout=1) is None
        capture.select_microphone(2)
        assert devices.get(timeout=2) == 2
    finally:
        capture.stop()


def test_synthetic_noise_uses_reproducible_ten_db_snr(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from classroom_noise import mix_classroom_noise

    clean = tone(1000, 0.15, samples=16000)
    noisy = mix_classroom_noise(clean, 16000)
    np.testing.assert_array_equal(noisy, mix_classroom_noise(clean, 16000))
    snr = 20 * np.log10(input_rms(clean) / input_rms(noisy - clean))
    assert np.isclose(snr, 10.0, atol=0.001)
