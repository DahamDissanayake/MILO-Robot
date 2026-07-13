"""Ring buffer of formatted log lines + optional live line hook."""
from __future__ import annotations

import logging
from collections import deque
from typing import Callable


class RingBufferLogHandler(logging.Handler):
    def __init__(self, capacity: int = 400):
        super().__init__()
        self._buf: deque[str] = deque(maxlen=capacity)
        self.on_line: Callable[[str], None] | None = None
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        self._buf.append(line)
        if self.on_line is not None:
            try:
                self.on_line(line)
            except Exception:
                pass

    def lines(self, n: int = 200) -> list[str]:
        items = list(self._buf)
        return items[-n:]
