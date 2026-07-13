"""I2S audio: stereo mic capture and speaker playback via ALSA CLI tools.

Format is locked by the protocol: capture stereo s16le @ 16 kHz in 20 ms
frames; playback mono s16le @ 16 kHz (TTS from the brain).

Shells out to ``arecord``/``aplay`` (alsa-utils) instead of going through
sounddevice/PortAudio. On the target hardware, PortAudio's ALSA host API
enumerates *zero* devices when no ALSA default is configured (``sd.query_
devices()`` returns ``[]``) -- and since sounddevice resolves every device,
even an explicit name or index, by matching against that same enumeration,
no device can be opened through it at all in that state. ``arecord``/
``aplay`` open ALSA PCMs by name directly against alsa-lib, sidestepping
PortAudio's device table entirely -- the same path WIRING-GUIDE.md's own
hardware verification (``arecord -D plughw:0 ...``) already proves works.

``rms()`` is the loud-sound detector used by sleep mode's perk-up.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import AsyncIterator

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHANNELS_IN = 2
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320 per channel
FRAME_BYTES_IN = FRAME_SAMPLES * CHANNELS_IN * 2  # int16 stereo

DEFAULT_DEVICE = "plughw:0,0"  # the robot's I2S HAT (see WIRING-GUIDE.md)


def rms(pcm: bytes) -> float:
    """RMS level of interleaved s16le PCM. Empty-safe."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples * samples)))


def capture_command(device: str) -> list[str]:
    """``arecord`` invocation for raw interleaved stereo s16le capture."""
    return ["arecord", "-D", device, "-c", str(CHANNELS_IN), "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-t", "raw"]


def playback_command(device: str) -> list[str]:
    """``aplay`` invocation for raw mono s16le playback."""
    return ["aplay", "-D", device, "-c", "1", "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-t", "raw"]


class AudioIO:
    """Captures/plays via arecord/aplay subprocesses; no ALSA needed to import."""

    def __init__(self, device: str | None = None):
        self._device = device or DEFAULT_DEVICE
        self._playback: subprocess.Popen[bytes] | None = None

    async def capture_frames(self) -> AsyncIterator[bytes]:
        """Yields 20 ms interleaved stereo s16le frames forever."""
        proc = await asyncio.create_subprocess_exec(
            *capture_command(self._device),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        try:
            while True:
                try:
                    yield await proc.stdout.readexactly(FRAME_BYTES_IN)
                except asyncio.IncompleteReadError as exc:
                    stderr = await proc.stderr.read() if proc.stderr else b""
                    raise RuntimeError(
                        f"arecord on {self._device!r} exited: {stderr.decode(errors='replace').strip()}"
                    ) from exc
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    def play_pcm(self, pcm: bytes) -> None:
        """Write mono s16le TTS audio to the speaker."""
        if self._playback is None:
            self._playback = subprocess.Popen(playback_command(self._device), stdin=subprocess.PIPE)
        assert self._playback.stdin is not None
        self._playback.stdin.write(pcm)
        self._playback.stdin.flush()

    def close(self) -> None:
        if self._playback is not None:
            if self._playback.stdin is not None:
                self._playback.stdin.close()
            self._playback.wait()
            self._playback = None
