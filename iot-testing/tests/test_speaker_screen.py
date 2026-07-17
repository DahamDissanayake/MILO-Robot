from datetime import datetime, timezone

import numpy as np

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.speaker import SpeakerScreen, generate_tone


def test_generate_tone_has_correct_length() -> None:
    pcm = generate_tone(frequency_hz=440.0, duration_s=1.0, sample_rate=16_000)
    assert len(pcm) == 16_000 * 2  # int16 = 2 bytes/sample, mono


def test_generate_tone_is_not_silent() -> None:
    pcm = generate_tone(frequency_hz=440.0, duration_s=0.1, sample_rate=16_000)
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert samples.max() > 10_000


def test_speaker_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = SpeakerScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
