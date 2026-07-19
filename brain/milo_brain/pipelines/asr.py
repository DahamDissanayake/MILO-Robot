"""Speech-to-text with faster-whisper. Model size follows the brain tier
(small on a 6 GB card, medium on the big box); loads lazily on first use."""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ._lazy import LazyLoad

log = logging.getLogger(__name__)

_CUDA_DLLS_READY = False


def _resolved_device(model, requested: str) -> str:
    """The concrete device ctranslate2 chose (``"cuda"``/``"cpu"``), not the
    ``"auto"`` we asked for -- so status/logs report where ASR actually runs."""
    try:
        return model.model.device
    except Exception:
        return requested


def _ensure_cuda_dll_path() -> None:
    """On Windows, put the pip-installed CUDA runtime DLLs on the DLL search
    path so ctranslate2 can load ``cublas64_12.dll`` / cuDNN at first inference.

    The CUDA libs ship as ``nvidia-*-cu12`` wheels that unpack to
    ``.../site-packages/nvidia/<lib>/bin``. ctranslate2 loads them lazily via a
    plain ``LoadLibrary`` at the first GPU inference, which searches the dirs
    registered with ``os.add_dll_directory`` -- but nothing registers them by
    default, so without this the GPU model builds fine and then dies mid-probe
    with "Library cublas64_12.dll is not found". We locate the wheels through
    the ``nvidia`` namespace package (robust to venv layout quirks) rather than
    guessing ``site-packages``. Best-effort + idempotent: any failure just
    leaves the existing CUDA->CPU fallback to catch a genuinely broken GPU."""
    global _CUDA_DLLS_READY
    if _CUDA_DLLS_READY or os.name != "nt":
        return
    _CUDA_DLLS_READY = True  # only attempt once, even on failure
    try:
        spec = importlib.util.find_spec("nvidia")
        roots = list(spec.submodule_search_locations) if spec else []
    except Exception:
        roots = []
    for root in roots:
        for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            bindir = Path(root) / sub / "bin"
            if not bindir.is_dir():
                continue
            try:
                os.add_dll_directory(str(bindir))
            except OSError as exc:  # pragma: no cover - platform-specific
                log.debug("could not add CUDA dll dir %s: %s", bindir, exc)
            # add_dll_directory alone can miss a transitively-loaded lib
            # (cublas -> cublasLt/cudart); prepending PATH is the belt-and-
            # suspenders that reliably lets ctranslate2's LoadLibrary find them.
            if str(bindir) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")


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
        _ensure_cuda_dll_path()  # must run before ctranslate2's first GPU inference
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
            return model, _resolved_device(model, device)
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
        segments, _info = self._model.transcribe(audio, language="en", beam_size=3)
        texts, probs = [], []
        for segment in segments:
            # Whisper's own "this window isn't really speech" signal -- high on
            # the phantom "Bye."/"Thank you." it emits for unclear/near-silent
            # audio. Dropping these keeps hallucinations out of the transcript
            # without touching genuine speech (whose no_speech_prob is low).
            if getattr(segment, "no_speech_prob", 0.0) > 0.6:
                continue
            texts.append(segment.text.strip())
            probs.append(np.exp(segment.avg_logprob))
        if not texts:
            return Transcript(text="", confidence=0.0)
        return Transcript(text=" ".join(texts).strip(), confidence=float(np.mean(probs)))
