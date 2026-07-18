"""Text-to-speech with Piper -> 16 kHz mono s16le, chunked for the wire."""

from __future__ import annotations

import numpy as np

from ._lazy import LazyLoad

TARGET_RATE = 16_000
FRAME_MS = 20


def chunk_pcm(pcm: bytes, frame_ms: int = FRAME_MS, rate: int = TARGET_RATE) -> list[bytes]:
    """Split mono s16le audio into fixed-duration frames (last one padded)."""
    frame_bytes = rate * frame_ms // 1000 * 2
    if not pcm:
        return []
    chunks = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
    if len(chunks[-1]) < frame_bytes:
        chunks[-1] = chunks[-1] + b"\x00" * (frame_bytes - len(chunks[-1]))
    return chunks


def resample_s16(pcm: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    """Linear-interpolation resample; fine for speech."""
    if src_rate == dst_rate:
        return pcm.astype(np.int16)
    duration = len(pcm) / src_rate
    dst_n = int(round(duration * dst_rate))
    src_t = np.linspace(0.0, duration, len(pcm), endpoint=False)
    dst_t = np.linspace(0.0, duration, dst_n, endpoint=False)
    return np.interp(dst_t, src_t, pcm.astype(np.float64)).astype(np.int16)


class PiperTts(LazyLoad):
    def __init__(self, voice: str = "en_US-lessac-medium"):
        super().__init__()
        self._voice_name = voice
        self._voice = None

    def _load(self) -> None:
        from piper import PiperVoice

        self._voice = PiperVoice.load(self._voice_name)

    def synthesize(self, text: str) -> bytes:
        """16 kHz mono s16le for ``{"t":"tts"}`` frames."""
        self.ensure_loaded()
        samples: list[np.ndarray] = []
        src_rate = TARGET_RATE
        for chunk in self._voice.synthesize(text):
            samples.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
            src_rate = chunk.sample_rate
        if not samples:
            return b""
        audio = np.concatenate(samples)
        return resample_s16(audio, src_rate).tobytes()
