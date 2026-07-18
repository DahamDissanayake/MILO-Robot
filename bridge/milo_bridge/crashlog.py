"""Persistent crash/error log: unhandled exceptions -- both full process
crashes and background-task failures asyncio would otherwise only log once
and forget -- survive here across restarts. Cleared only by a deliberate
Full Restart from the dashboard (webapp/api/system.py), not automatically
on every boot, so a crash followed by an accidental power cycle still shows
up until someone deliberately clears it.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)


class CrashLog:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, exc: BaseException, context: str = "") -> None:
        entry = {
            "t": time.time(),
            "kind": kind,
            "context": context,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            log.warning("failed to write crash log entry", exc_info=True)

    def entries(self, n: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        out: list[dict] = []
        for line in self._path.read_text(encoding="utf-8").splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def count(self) -> int:
        if not self._path.exists():
            return 0
        with self._path.open(encoding="utf-8") as f:
            return sum(1 for _ in f)

    def clear(self) -> None:
        self._path.write_text("", encoding="utf-8")
