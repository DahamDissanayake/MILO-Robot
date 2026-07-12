"""Backend service health: systemd units, bridge process, journal, hardware presence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psutil

from . import read_file, run_cmd

BRIDGE_UNIT = "milo-bridge.service"
EXTRA_UNITS = ("ssh.service", "avahi-daemon.service")
JOURNAL_LINES = 15
SHOW_PROPS = "ActiveState,SubState,MainPID,NRestarts,ActiveEnterTimestamp"


@dataclass(frozen=True)
class UnitStatus:
    name: str
    active_state: str
    sub_state: str
    pid: int | None
    n_restarts: int | None
    since: str | None


def parse_systemctl_show(name: str, text: str) -> UnitStatus | None:
    props: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    if "ActiveState" not in props:
        return None
    raw_pid = props.get("MainPID", "0")
    raw_restarts = props.get("NRestarts", "")
    return UnitStatus(
        name=name,
        active_state=props.get("ActiveState", "unknown"),
        sub_state=props.get("SubState", ""),
        pid=int(raw_pid) if raw_pid.isdigit() and raw_pid != "0" else None,
        n_restarts=int(raw_restarts) if raw_restarts.isdigit() else None,
        since=props.get("ActiveEnterTimestamp") or None,
    )


def unit_status(name: str) -> UnitStatus | None:
    out = run_cmd(["systemctl", "show", name, "--property", SHOW_PROPS])
    return parse_systemctl_show(name, out) if out else None


def parse_asound_cards(text: str) -> bool:
    return "voicehat" in (text or "").lower()


@dataclass(frozen=True)
class HardwarePresence:
    i2c: bool
    camera: bool
    voicehat: bool


def collect_hardware() -> HardwarePresence:
    dev = Path("/dev")
    return HardwarePresence(
        i2c=(dev / "i2c-1").exists(),
        camera=dev.is_dir() and any(dev.glob("video*")),
        voicehat=parse_asound_cards(read_file("/proc/asound/cards") or ""),
    )


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    cpu_percent: float
    mem_percent: float


def top_processes(limit: int = 5) -> tuple[ProcessInfo, ...]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        info = p.info
        procs.append(
            ProcessInfo(
                pid=info["pid"],
                name=info["name"] or "?",
                cpu_percent=info["cpu_percent"] or 0.0,
                mem_percent=info["memory_percent"] or 0.0,
            )
        )
    procs.sort(key=lambda pr: pr.cpu_percent, reverse=True)
    return tuple(procs[:limit])


@dataclass(frozen=True)
class BridgeProcess:
    cpu_percent: float
    rss_bytes: int


# psutil.Process must be reused across calls or cpu_percent is always 0.0.
_proc_cache: dict[int, psutil.Process] = {}


def bridge_process(pid: int | None) -> BridgeProcess | None:
    if not pid:
        return None
    try:
        p = _proc_cache.get(pid)
        if p is None or not p.is_running():
            p = psutil.Process(pid)
            _proc_cache.clear()
            _proc_cache[pid] = p
        return BridgeProcess(
            cpu_percent=p.cpu_percent(interval=None), rss_bytes=p.memory_info().rss
        )
    except psutil.Error:
        return None


@dataclass(frozen=True)
class JournalTail:
    lines: tuple[str, ...]
    error: str | None


def journal_tail(unit: str = BRIDGE_UNIT, n: int = JOURNAL_LINES) -> JournalTail:
    out = run_cmd(["journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "short"])
    if out is None:
        return JournalTail(
            (), "journalctl unavailable (is this user in the systemd-journal group?)"
        )
    return JournalTail(tuple(out.strip().splitlines()), None)


@dataclass(frozen=True)
class ServicesSnapshot:
    bridge: UnitStatus | None
    bridge_proc: BridgeProcess | None
    extras: tuple[UnitStatus | None, ...]
    hardware: HardwarePresence
    journal: JournalTail


def collect_slow() -> ServicesSnapshot:
    bridge = unit_status(BRIDGE_UNIT)
    return ServicesSnapshot(
        bridge=bridge,
        bridge_proc=bridge_process(bridge.pid if bridge else None),
        extras=tuple(unit_status(u) for u in EXTRA_UNITS),
        hardware=collect_hardware(),
        journal=journal_tail(),
    )
