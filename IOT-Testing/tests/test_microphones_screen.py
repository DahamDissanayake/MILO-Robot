import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.microphones import MicScreen, level_bar, save_wav, split_channels


def test_split_channels_deinterleaves_stereo_pcm() -> None:
    # L=100, R=200, L=101, R=201 (interleaved int16)
    samples = np.array([100, 200, 101, 201], dtype=np.int16)
    left, right = split_channels(samples.tobytes())
    assert np.frombuffer(left, dtype=np.int16).tolist() == [100, 101]
    assert np.frombuffer(right, dtype=np.int16).tolist() == [200, 201]


def test_level_bar_scales_between_0_and_width() -> None:
    assert level_bar(0.0, max_level=4000.0, width=30) == "-" * 30
    assert level_bar(4000.0, max_level=4000.0, width=30) == "#" * 30
    assert level_bar(8000.0, max_level=4000.0, width=30) == "#" * 30  # clamped


def test_save_wav_writes_readable_file(tmp_path: Path) -> None:
    pcm = np.array([0, 100, -100, 200], dtype=np.int16).tobytes()
    wav_path = tmp_path / "test.wav"
    save_wav(wav_path, pcm, channels=2, sample_rate=16_000)
    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm


def test_mic_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = MicScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
