"""Text-to-speech with Piper -> 16 kHz mono s16le, chunked for the wire."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ._lazy import LazyLoad

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
FRAME_MS = 20
DEFAULT_VOICES_DIR = Path.home() / ".milo-brain" / "piper-voices"


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
    """Loads a Piper voice, downloading it on first use if it isn't cached.
    A voice that can't be fetched/loaded degrades to silence (logged once)
    rather than crashing every reply."""

    def __init__(self, voice: str = "en_US-amy-medium", voices_dir=None,
                 download=None, loader=None):
        super().__init__()
        self._voice_name = voice
        self._voices_dir = Path(voices_dir) if voices_dir else DEFAULT_VOICES_DIR
        self._download = download
        self._loader = loader
        self._voice = None
        self._warned = False

    def _load(self) -> None:
        download = self._download
        loader = self._loader
        if download is None:
            from piper.download_voices import download_voice
            download = download_voice
        if loader is None:
            from piper import PiperVoice
            loader = PiperVoice.load
        model_path = self._voices_dir / f"{self._voice_name}.onnx"
        if not model_path.exists():
            self._voices_dir.mkdir(parents=True, exist_ok=True)
            log.info("downloading Piper voice %r to %s", self._voice_name, self._voices_dir)
            download(self._voice_name, self._voices_dir)
        self._voice = loader(model_path)

    def synthesize(self, text: str) -> bytes:
        """16 kHz mono s16le for ``{"t":"tts"}`` frames. Returns b"" (silence)
        if the voice can't be loaded, logging the reason exactly once."""
        if self.status == "error":
            return b""
        try:
            self.ensure_loaded()
        except Exception:
            if not self._warned:
                log.warning(
                    "TTS voice %r unavailable (%s); robot will stay silent until restart",
                    self._voice_name, self.error,
                )
                self._warned = True
            return b""
        samples: list[np.ndarray] = []
        src_rate = TARGET_RATE
        for chunk in self._voice.synthesize(text):
            samples.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
            src_rate = chunk.sample_rate
        if not samples:
            return b""
        audio = np.concatenate(samples)
        return resample_s16(audio, src_rate).tobytes()
