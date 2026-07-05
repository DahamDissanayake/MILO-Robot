import numpy as np

from milo_bridge.drivers.audio import FRAME_SAMPLES, rms


def test_rms_empty_is_zero():
    assert rms(b"") == 0.0


def test_rms_silence_is_zero():
    assert rms(np.zeros(FRAME_SAMPLES, dtype=np.int16).tobytes()) == 0.0


def test_rms_full_scale_square_wave():
    samples = np.full(FRAME_SAMPLES, 10000, dtype=np.int16)
    samples[::2] = -10000
    assert abs(rms(samples.tobytes()) - 10000) < 1.0


def test_rms_scales_with_amplitude():
    quiet = (1000 * np.sin(np.linspace(0, 20 * np.pi, FRAME_SAMPLES))).astype(np.int16)
    loud = (20000 * np.sin(np.linspace(0, 20 * np.pi, FRAME_SAMPLES))).astype(np.int16)
    assert rms(loud.tobytes()) > 10 * rms(quiet.tobytes())
