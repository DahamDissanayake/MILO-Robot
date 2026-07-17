"""Collector seams. Every external read goes through run_cmd/read_file,
which return None on any failure so the dashboard never crashes off-Pi."""

from __future__ import annotations

import subprocess
from pathlib import Path

CMD_TIMEOUT_S = 2.0


def run_cmd(args: list[str]) -> str | None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=CMD_TIMEOUT_S)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def read_file(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
