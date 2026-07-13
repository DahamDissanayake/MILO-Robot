"""In-memory session tokens and per-IP login throttling.

Neither structure is persisted across a bridge restart — a restart simply
requires re-login, which is fine and keeps this simple (no session-store
file, no cleanup-on-boot logic).
"""

from __future__ import annotations

import secrets
import time
from typing import Callable

TOKEN_BYTES = 32
FAILURE_WINDOW_S = 60.0
FAILURE_LIMIT = 5
COOLDOWN_S = 30.0


class SessionStore:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}  # token -> username

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._tokens[token] = username
        return token

    def is_valid(self, token: str) -> bool:
        return token in self._tokens

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)


class LoginThrottle:
    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def allow(self, ip: str) -> bool:
        locked_until = self._locked_until.get(ip)
        if locked_until is not None and self._now() < locked_until:
            return False
        return True

    def record_failure(self, ip: str) -> None:
        now = self._now()
        window_start = now - FAILURE_WINDOW_S
        recent = [t for t in self._failures.get(ip, []) if t >= window_start]
        recent.append(now)
        self._failures[ip] = recent
        if len(recent) >= FAILURE_LIMIT:
            self._locked_until[ip] = now + COOLDOWN_S

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._locked_until.pop(ip, None)
