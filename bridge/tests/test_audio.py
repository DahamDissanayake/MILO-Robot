import subprocess

import numpy as np

from milo_bridge.drivers.audio import (
    DEFAULT_DEVICE,
    FRAME_SAMPLES,
    AudioIO,
    capture_command,
    playback_command,
    rms,
)


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


def test_capture_command_uses_stereo_raw_format():
    assert capture_command("plughw:0,0") == [
        "arecord", "-D", "plughw:0,0", "-c", "2", "-r", "16000", "-f", "S16_LE", "-t", "raw",
    ]


def test_playback_command_uses_mono_raw_format():
    assert playback_command("plughw:0,0") == [
        "aplay", "-D", "plughw:0,0", "-c", "1", "-r", "16000", "-f", "S16_LE", "-t", "raw",
    ]


def test_audio_io_defaults_to_known_hardware_device():
    assert AudioIO()._device == DEFAULT_DEVICE


def test_audio_io_explicit_device_wins():
    assert AudioIO("plughw:1,0")._device == "plughw:1,0"


class FakeStdin:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written += data

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakePopen:
    """Stand-in for subprocess.Popen recording what would've been aplay's stdin."""

    instances: list["FakePopen"] = []

    def __init__(self, cmd, stdin=None):
        self.cmd = cmd
        self.stdin = FakeStdin()
        self.waited = False
        FakePopen.instances.append(self)

    def wait(self) -> None:
        self.waited = True


def test_play_pcm_starts_aplay_once_and_writes_to_its_stdin(monkeypatch):
    FakePopen.instances.clear()
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    audio = AudioIO("plughw:2,0")

    audio.play_pcm(b"abc")
    audio.play_pcm(b"def")

    assert len(FakePopen.instances) == 1
    assert FakePopen.instances[0].cmd == playback_command("plughw:2,0")
    assert FakePopen.instances[0].stdin.written == b"abcdef"


def test_close_closes_stdin_and_waits(monkeypatch):
    FakePopen.instances.clear()
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    audio = AudioIO()
    audio.play_pcm(b"abc")

    audio.close()

    proc = FakePopen.instances[0]
    assert proc.stdin.closed
    assert proc.waited
    assert audio._playback is None


def test_close_is_a_noop_before_any_playback():
    AudioIO().close()  # must not raise
