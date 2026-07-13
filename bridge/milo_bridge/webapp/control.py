"""Single gate for anything that moves hardware.

Observation is never brokered — only motion. The brain has motion rights
implicitly whenever no web client holds the slot. STOP is handled by the
callers (always allowed) and never routes through acquire.
"""
from __future__ import annotations

import time
from typing import Callable


class ControlBroker:
    def __init__(self, on_change: Callable[[str], None] | None = None, timeout_s: float = 10.0):
        self._web_owner: str | None = None
        self._brain = False
        self._on_change = on_change
        self._timeout_s = timeout_s
        self._last_hb: float = 0.0

    @property
    def owner(self) -> str:
        if self._web_owner is not None:
            return "web"
        return "brain" if self._brain else "none"

    def _emit(self, before: str) -> None:
        if self._on_change is not None and self.owner != before:
            self._on_change(self.owner)

    def set_brain_connected(self, connected: bool) -> None:
        before = self.owner
        self._brain = connected
        self._emit(before)

    def acquire_web(self, client_id: str) -> bool:
        if self._web_owner is not None and self._web_owner != client_id:
            return False
        before = self.owner
        self._web_owner = client_id
        self._last_hb = time.monotonic()
        self._emit(before)
        return True

    def release_web(self, client_id: str) -> None:
        if self._web_owner != client_id:
            return
        before = self.owner
        self._web_owner = None
        self._emit(before)

    def heartbeat(self, client_id: str) -> None:
        if self._web_owner == client_id:
            self._last_hb = time.monotonic()

    def expire(self, now: float | None = None) -> bool:
        """Release web control if the owner has gone quiet. Returns True if released."""
        if self._web_owner is None:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last_hb < self._timeout_s:
            return False
        before = self.owner
        self._web_owner = None
        self._emit(before)
        return True

    def allow_brain_motion(self) -> bool:
        return self._web_owner is None

    def is_web_controller(self, client_id: str) -> bool:
        return self._web_owner == client_id
