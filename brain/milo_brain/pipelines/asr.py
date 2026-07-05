"""Speech-to-text with faster-whisper. Model size follows the brain tier
(small on a 6 GB card, medium on the big box); loads lazily on first use."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float  # mean segment probability, 0-1


class WhisperAsr:
    def __init__(self, model_size: str = "small", device: str = "auto"):
        self._model_size = model_size
        self._device = device
        self._model = None

    def _load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self._model_size, device=self._device, compute_type="auto")

    def transcribe(self, mono_int16: np.ndarray) -> Transcript:
        if self._model is None:
            self._load()
        audio = mono_int16.astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(audio, language="en", beam_size=3)
        texts, probs = [], []
        for segment in segments:
            texts.append(segment.text.strip())
            probs.append(np.exp(segment.avg_logprob))
        if not texts:
            return Transcript(text="", confidence=0.0)
        return Transcript(text=" ".join(texts).strip(), confidence=float(np.mean(probs)))
