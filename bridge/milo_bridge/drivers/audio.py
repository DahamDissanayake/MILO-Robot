"""I2S audio: stereo mic capture and speaker playback via sounddevice/ALSA.

Format is locked by the protocol: capture stereo s16le @ 16 kHz in 20 ms
frames; playback mono s16le @ 16 kHz (TTS from the brain).

``rms()`` is the loud-sound detector used by sleep mode's perk-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np

SAMPLE_RATE = 16_000
CHANNELS_IN = 2
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320 per channel


def rms(pcm: bytes) -> float:
    """RMS level of interleaved s16le PCM. Empty-safe."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples * samples)))


class AudioIO:
    """Wraps sounddevice streams; imports lazily so tests never need ALSA."""

    def __init__(self, device: str | int | None = None):
        self._device = device
        self._playback = None

    async def capture_frames(self) -> AsyncIterator[bytes]:
        """Yields 20 ms interleaved stereo s16le frames forever."""
        import sounddevice as sd  # type: ignore

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        def on_block(indata, frames, time_info, status) -> None:
            data = bytes(indata)
            try:
                loop.call_soon_threadsafe(queue.put_nowait, data)
            except RuntimeError:
                pass  # loop closed mid-shutdown

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS_IN,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=on_block,
        ):
            while True:
                yield await queue.get()

    def play_pcm(self, pcm: bytes) -> None:
        """Queue mono s16le TTS audio to the speaker."""
        import sounddevice as sd  # type: ignore

        if self._playback is None:
            self._playback = sd.RawOutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=self._device
            )
            self._playback.start()
        self._playback.write(pcm)

    def close(self) -> None:
        if self._playback is not None:
            self._playback.stop()
            self._playback.close()
            self._playback = None
