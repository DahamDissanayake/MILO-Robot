"""Face detection + 512-d embeddings on the MJPEG stream (InsightFace).

Analysis runs at 2-5 fps regardless of the 15 fps stream — faces don't move
that fast, VRAM is precious on the small tier, and CPU fallback stays viable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from ._lazy import LazyLoad


@dataclass(frozen=True)
class FaceObservation:
    bbox: tuple[float, float, float, float]
    embedding: np.ndarray  # (512,) float32


class InsightFaceAnalyzer(LazyLoad):
    def __init__(self, use_gpu: bool = True):
        super().__init__()
        self._use_gpu = use_gpu
        self._app = None

    def _load(self) -> None:
        from insightface.app import FaceAnalysis

        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if self._use_gpu
            else ["CPUExecutionProvider"]
        )
        self._app = FaceAnalysis(name="buffalo_l", providers=providers)
        self._app.prepare(ctx_id=0 if self._use_gpu else -1, det_size=(640, 640))

    def __call__(self, bgr_image: np.ndarray) -> list[FaceObservation]:
        self.ensure_loaded()
        return [
            FaceObservation(
                bbox=tuple(float(v) for v in face.bbox),
                embedding=np.asarray(face.normed_embedding, dtype=np.float32),
            )
            for face in self._app.get(bgr_image)
        ]


class FaceVision:
    """Throttled JPEG -> faces. The analyzer is injectable for tests."""

    def __init__(self, analyzer=None, analysis_fps: float = 3.0, clock=time.monotonic):
        self._analyzer = analyzer or InsightFaceAnalyzer()
        self._min_interval = 1.0 / analysis_fps
        self._clock = clock
        self._last_run = -1e9
        self.last_faces: list[FaceObservation] = []

    @property
    def status(self) -> str:
        return getattr(self._analyzer, "status", "ready")

    @property
    def error(self) -> str | None:
        return getattr(self._analyzer, "error", None)

    def process_jpeg(self, jpeg: bytes) -> list[FaceObservation] | None:
        """Returns fresh observations, or None when throttled/undecodable."""
        now = self._clock()
        if now - self._last_run < self._min_interval:
            return None
        self._last_run = now
        image = _decode_jpeg(jpeg)
        if image is None:
            return None
        self.last_faces = self._analyzer(image)
        return self.last_faces


def _decode_jpeg(jpeg: bytes) -> np.ndarray | None:
    try:
        import cv2

        image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        return image
    except ImportError:
        try:  # PIL fallback (RGB -> BGR)
            import io

            from PIL import Image

            rgb = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))
            return rgb[:, :, ::-1].copy()
        except Exception:
            return None
    except Exception:
        return None
