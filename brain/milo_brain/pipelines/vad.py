"""Voice activity detection: gate the incoming mic stream into speech segments.

The segmentation state machine is pure and testable; the speech classifier is
injectable — Silero VAD in production, anything in tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ._lazy import LazyLoad

SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class SpeechSegment:
    stereo: np.ndarray      # (n, 2) int16 — kept for direction estimation
    mono: np.ndarray        # (n,) int16 — fed to ASR
    start_ts: float
    end_ts: float


def stereo_from_bytes(frame: bytes) -> np.ndarray:
    return np.frombuffer(frame, dtype=np.int16).reshape(-1, 2)


def downmix(stereo: np.ndarray) -> np.ndarray:
    return (stereo.astype(np.int32).sum(axis=1) // 2).astype(np.int16)


class SileroSpeechDetector(LazyLoad):
    """Loads Silero VAD lazily (torch hub); callable(mono int16) -> bool.

    Silero's model rejects any chunk where sr / len(chunk) > 31.25 -- at
    16 kHz that's anything under 512 samples (32 ms). The wire protocol
    locks frames at 320 samples (20 ms, see
    bridge/milo_bridge/drivers/audio.py's FRAME_SAMPLES), so raw frames are
    buffered here and only handed to the model once 512 samples have
    accumulated; the decision from the last full window is reused for the
    frames in between.
    """

    REQUIRED_SAMPLES = 512  # sr / 31.25 at 16 kHz -- Silero's minimum chunk length

    def __init__(self, threshold: float = 0.35, model=None):
        # 0.35, not Silero's usual 0.5: a higher bar clips the quieter starts
        # and ends of a phrase (e.g. hearing only the loud middle word), so a
        # lower threshold keeps the whole utterance as speech. The brain still
        # gates on a full segment closing, so the extra sensitivity mostly
        # widens real speech rather than admitting noise.
        super().__init__()
        self._threshold = threshold
        self._model = model
        self._torch = None
        self._buffer = np.empty(0, dtype=np.int16)
        self._last_speaking = False
        if model is not None:
            self.status = "ready"

    # Pinned so an upstream release can't silently change model behavior
    # (or chunking rules) under us -- bump deliberately, re-verify
    # REQUIRED_SAMPLES still holds, and note it in the commit.
    _HUB_REPO = "snakers4/silero-vad:v6.2.1"

    def _load(self) -> None:
        import torch

        self._model, _ = torch.hub.load(
            self._HUB_REPO, "silero_vad", trust_repo=True
        )
        self._torch = torch

    def __call__(self, mono: np.ndarray) -> bool:
        self.ensure_loaded()
        if self._torch is None:
            import torch

            self._torch = torch
        self._buffer = np.concatenate([self._buffer, mono])
        while len(self._buffer) >= self.REQUIRED_SAMPLES:
            chunk, self._buffer = (
                self._buffer[: self.REQUIRED_SAMPLES],
                self._buffer[self.REQUIRED_SAMPLES :],
            )
            audio = self._torch.from_numpy(chunk.astype(np.float32) / 32768.0)
            score = float(self._model(audio, SAMPLE_RATE).item())
            self._last_speaking = score >= self._threshold
        return self._last_speaking


class VadSegmenter:
    """Feed 20 ms stereo frames; returns a SpeechSegment when one closes.

    A segment opens on the first speech frame and closes after
    ``min_silence_ms`` of non-speech (or at ``max_segment_s``, force-flushed).
    """

    def __init__(
        self,
        is_speech: Callable[[np.ndarray], bool] | None = None,
        # 700ms (not 400) of silence to close: natural mid-sentence pauses
        # ("I said hi ... not bye") are often 400-600ms, and closing that
        # early splits one sentence into fragments -- of which only the first
        # gets transcribed (the rest is dropped while ASR is busy). pre_roll
        # 10 frames (200ms, not 100) keeps more of a phrase's quiet onset.
        min_silence_ms: int = 700,
        max_segment_s: float = 15.0,
        pre_roll_frames: int = 10,
    ):
        self._is_speech = is_speech or SileroSpeechDetector()
        self._min_silence_ms = min_silence_ms
        self._max_segment_s = max_segment_s
        self._pre_roll: list[np.ndarray] = []
        self._pre_roll_frames = pre_roll_frames
        self._active: list[np.ndarray] = []
        self._start_ts = 0.0
        self._silence_ms = 0.0
        self._last_ts = 0.0

    @property
    def status(self) -> str:
        return getattr(self._is_speech, "status", "ready")

    @property
    def error(self) -> str | None:
        return getattr(self._is_speech, "error", None)

    def push(self, frame: bytes, ts: float) -> SpeechSegment | None:
        stereo = stereo_from_bytes(frame)
        frame_ms = 1000.0 * len(stereo) / SAMPLE_RATE
        self._last_ts = ts
        speaking = self._is_speech(downmix(stereo))

        if not self._active:
            self._pre_roll.append(stereo)
            if len(self._pre_roll) > self._pre_roll_frames:
                self._pre_roll.pop(0)
            if speaking:
                self._active = list(self._pre_roll)
                self._pre_roll = []
                self._start_ts = ts - frame_ms / 1000.0 * len(self._active)
                self._silence_ms = 0.0
            return None

        self._active.append(stereo)
        self._silence_ms = 0.0 if speaking else self._silence_ms + frame_ms
        duration = sum(len(a) for a in self._active) / SAMPLE_RATE
        if self._silence_ms >= self._min_silence_ms or duration >= self._max_segment_s:
            return self._flush()
        return None

    def _flush(self) -> SpeechSegment:
        stereo = np.concatenate(self._active)
        self._active = []
        return SpeechSegment(
            stereo=stereo, mono=downmix(stereo), start_ts=self._start_ts, end_ts=self._last_ts
        )
