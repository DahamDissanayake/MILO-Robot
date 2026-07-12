"""Storage stats: real mounted filesystems plus MILO data sizes under ~/.milo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psutil

# Must match the bridge's conventions (bridge/milo_bridge/config.py, main.py).
MILO_DIR = Path.home() / ".milo"
GRAPH_DB = MILO_DIR / "graph.db"
POLICY = MILO_DIR / "policy.onnx"

SKIP_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "ramfs"}


@dataclass(frozen=True)
class Disk:
    mount: str
    total: int
    used: int
    percent: float


@dataclass(frozen=True)
class MiloData:
    milo_dir_bytes: int | None
    graph_db_bytes: int | None
    policy_bytes: int | None


@dataclass(frozen=True)
class StorageSnapshot:
    disks: tuple[Disk, ...]
    milo: MiloData


def keep_partition(fstype: str) -> bool:
    return bool(fstype) and fstype not in SKIP_FS


def dir_size(path: Path) -> int | None:
    if not path.is_dir():
        return None
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def collect() -> StorageSnapshot:
    disks = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        if not keep_partition(part.fstype) or part.mountpoint in seen:
            continue
        seen.add(part.mountpoint)
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append(Disk(part.mountpoint, u.total, u.used, u.percent))
    return StorageSnapshot(
        disks=tuple(disks),
        milo=MiloData(
            milo_dir_bytes=dir_size(MILO_DIR),
            graph_db_bytes=file_size(GRAPH_DB),
            policy_bytes=file_size(POLICY),
        ),
    )
