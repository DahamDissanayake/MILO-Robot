"""Shared status tracking for pipeline classes that lazily load a heavy
model on first use (Silero VAD, Whisper, Piper, InsightFace). Subclasses
implement _load() (sets whatever model attribute they own, raises on
failure); callers use ensure_loaded() instead of hand-rolling
`if self._model is None: self._load()`, and the dashboard reads .status/
.error to show what's actually working -- including while _load() is
still running. A threading.Lock makes ensure_loaded() safe when a
background warm-up thread and a first-real-use thread call it at once
(both go through asyncio.to_thread, i.e. real OS threads).
"""

from __future__ import annotations

import threading


class LazyLoad:
    def __init__(self) -> None:
        self.status: str = "not_loaded"  # "not_loaded" | "loading" | "ready" | "error"
        self.error: str | None = None
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        raise NotImplementedError

    def ensure_loaded(self) -> None:
        if self.status == "ready":
            return
        with self._load_lock:
            if self.status == "ready":  # another thread finished while we waited
                return
            self.status, self.error = "loading", None
            try:
                self._load()
                self.status, self.error = "ready", None
            except Exception as exc:
                self.status, self.error = "error", str(exc)
                raise
