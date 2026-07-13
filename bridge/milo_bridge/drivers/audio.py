"""I2S audio: stereo mic capture and speaker playback via sounddevice/ALSA.

Format is locked by the protocol: capture stereo s16le @ 16 kHz in 20 ms
frames; playback mono s16le @ 16 kHz (TTS from the brain).

``rms()`` is the loud-sound detector used by sleep mode's perk-up.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable

import numpy as np

log = logging.getLogger(__name__)

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


def pick_fallback_device(devices: Iterable[dict], *, min_input: int = 0, min_output: int = 0) -> int:
    """First device with enough channels, in PortAudio's enumeration order."""
    for index, info in enumerate(devices):
        if info["max_input_channels"] >= min_input and info["max_output_channels"] >= min_output:
            return index
    raise LookupError("no ALSA device with the required channels")


def resolve_device(
    explicit: str | int | None, default_index: int, devices: Iterable[dict], **channels: int
) -> str | int | None:
    """Explicit device wins; else PortAudio's default; else the first capable
    device. ``default_index`` is -1 when the Pi has no ALSA default configured
    (see WIRING-GUIDE.md, which verifies mics via an explicit ``plughw:0``)."""
    if explicit is not None:
        return explicit
    if default_index != -1:
        return None
    device = pick_fallback_device(devices, **channels)
    log.warning("no default ALSA device; falling back to device %d", device)
    return device


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

        device = resolve_device(self._device, sd.default.device[0], sd.query_devices(), min_input=CHANNELS_IN)
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS_IN,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=device,
            callback=on_block,
        ):
            while True:
                yield await queue.get()

    def play_pcm(self, pcm: bytes) -> None:
        """Queue mono s16le TTS audio to the speaker."""
        import sounddevice as sd  # type: ignore

        if self._playback is None:
            device = resolve_device(self._device, sd.default.device[1], sd.query_devices(), min_output=1)
            self._playback = sd.RawOutputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device
            )
            self._playback.start()
        self._playback.write(pcm)

    def close(self) -> None:
        if self._playback is not None:
            self._playback.stop()
            self._playback.close()
            self._playback = None
