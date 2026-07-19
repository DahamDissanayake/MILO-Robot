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
        self._model, self._device_in_use = self._build_probed(self._device)

    def _build_model(self, device: str):
        from faster_whisper import WhisperModel

        return WhisperModel(self._model_size, device=device, compute_type="auto")

    def _build_probed(self, device: str):
        """Construct on ``device`` and validate it with a tiny probe inference.
        ctranslate2 defers CUDA/cuBLAS init to the first inference, so a broken
        GPU runtime (e.g. a missing cublas64_12.dll) surfaces HERE -- during
        _load()/warm-up -- instead of stalling the operator's first real
        utterance with a mid-conversation rebuild. Falls back to CPU once; a
        genuine CPU failure propagates."""
        model = self._build_model(device)
        try:
            self._probe(model)
            return model, device
        except Exception as exc:
            if device == "cpu":
                raise
            log.warning("whisper device %r failed (%s), falling back to cpu", device, exc)
            model = self._build_model("cpu")
            self._probe(model)
            return model, "cpu"

    def _probe(self, model) -> None:
        segments, _info = model.transcribe(
            np.zeros(1600, dtype=np.float32), language="en", beam_size=1
        )
        list(segments)  # consume the generator so inference actually runs

    def transcribe(self, mono_int16: np.ndarray) -> Transcript:
        self.ensure_loaded()
        return self._run_transcribe(mono_int16.astype(np.float32) / 32768.0)

    def _run_transcribe(self, audio: np.ndarray) -> Transcript:
        # NOTE: do NOT enable faster-whisper's vad_filter here -- the brain
        # already gates segments through its own Silero VAD (see
        # pipelines/vad.py) before ASR, so a second in-Whisper VAD pass
        # double-trims and truncates real speech down to a word or two.
        # condition_on_previous_text=False just stops Whisper echoing an
        # earlier utterance's text into this one; it doesn't affect length.
        segments, _info = self._model.transcribe(
            audio, language="en", beam_size=3, condition_on_previous_text=False,
        )
        texts, probs = [], []
        for segment in segments:
            texts.append(segment.text.strip())
            probs.append(np.exp(segment.avg_logprob))
        if not texts:
            return Transcript(text="", confidence=0.0)
        return Transcript(text=" ".join(texts).strip(), confidence=float(np.mean(probs)))
