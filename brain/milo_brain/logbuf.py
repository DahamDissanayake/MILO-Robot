"""Ring buffer of formatted log lines -- mirrors
bridge/milo_bridge/webapp/logbuf.py's RingBufferLogHandler. This is what
powers the TUI's Logs screen: background task errors (a failed handshake,
a dropped connection, zeroconf noise) would otherwise be invisible once
Textual has taken over the terminal, since writing straight to stderr
from a background task corrupts/vanishes into its alternate screen buffer
instead of appearing anywhere the user can read.
"""
from __future__ import annotations

import logging
from collections import deque


class RingBufferLogHandler(logging.Handler):
    def __init__(self, capacity: int = 400):
        super().__init__()
        self._buf: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        self._buf.append(line)

    def lines(self, n: int = 200) -> list[str]:
        items = list(self._buf)
        return items[-n:]
