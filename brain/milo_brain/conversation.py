"""A small bounded log of spoken exchanges (what Milo heard and replied),
shown live on the brain TUI's conversation view."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Exchange:
    heard: str
    reply: str
    ts: float


class ConversationLog:
    def __init__(self, maxlen: int = 50):
        self._items: deque[Exchange] = deque(maxlen=maxlen)

    def add(self, heard: str, reply: str) -> None:
        self._items.append(Exchange(heard=heard, reply=reply, ts=time.time()))

    def recent(self, n: int) -> list[Exchange]:
        items = list(self._items)
        return items[-n:] if n < len(items) else items
