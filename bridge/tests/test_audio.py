import numpy as np
import pytest

from milo_bridge.drivers.audio import FRAME_SAMPLES, pick_fallback_device, resolve_device, rms


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


DEVICES = [
    {"name": "vc4-hdmi", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "plughw:0,0", "max_input_channels": 2, "max_output_channels": 2},
]


def test_pick_fallback_device_finds_first_match():
    assert pick_fallback_device(DEVICES, min_input=2) == 1


def test_pick_fallback_device_raises_when_none_match():
    with pytest.raises(LookupError):
        pick_fallback_device(DEVICES, min_input=8)


def test_resolve_device_explicit_wins():
    assert resolve_device("plughw:1,0", default_index=-1, devices=DEVICES, min_input=2) == "plughw:1,0"


def test_resolve_device_uses_portaudio_default_when_valid():
    assert resolve_device(None, default_index=1, devices=DEVICES, min_input=2) is None


def test_resolve_device_falls_back_when_no_default():
    assert resolve_device(None, default_index=-1, devices=DEVICES, min_input=2) == 1
