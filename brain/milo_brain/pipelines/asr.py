"""Speech-to-text with faster-whisper. Model size follows the brain tier
(small on a 6 GB card, medium on the big box); loads lazily on first use."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from ._lazy import LazyLoad

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float  # mean segment probability, 0-1


class WhisperAsr(LazyLoad):
    def __init__(self, model_size: str = "small", device: str = "auto"):
        super().__init__()
        self._model_size = model_size
        self._device = device
        self._model = None
        self._device_in_use: str | None = None

    def _load(self) -> None:
        self._model = self._build_model(self._device)
        self._device_in_use = self._device

    def _build_model(self, device: str):
        from faster_whisper import WhisperModel

        return WhisperModel(self._model_size, device=device, compute_type="auto")

    def transcribe(self, mono_int16: np.ndarray) -> Transcript:
        self.ensure_loaded()
        audio = mono_int16.astype(np.float32) / 32768.0
        try:
            return self._run_transcribe(audio)
        except Exception as exc:
            # ctranslate2 defers CUDA/cuBLAS init to the first real
            # inference call, so a broken GPU runtime (e.g. a missing
            # cublas64_12.dll) surfaces here, not in _load(). Fall back to
            # CPU once and retry this same utterance instead of losing it;
            # if we're already on CPU there's nowhere left to fall back to.
            if self._device_in_use == "cpu":
                raise
            log.warning(
                "whisper device %r failed (%s), falling back to cpu",
                self._device_in_use, exc,
            )
            self._model = self._build_model("cpu")
            self._device_in_use = "cpu"
            return self._run_transcribe(audio)

    def _run_transcribe(self, audio: np.ndarray) -> Transcript:
        segments, _info = self._model.transcribe(audio, language="en", beam_size=3)
        texts, probs = [], []
        for segment in segments:
            texts.append(segment.text.strip())
            probs.append(np.exp(segment.avg_logprob))
        if not texts:
            return Transcript(text="", confidence=0.0)
        return Transcript(text=" ".join(texts).strip(), confidence=float(np.mean(probs)))
