"""System stats: CPU, memory, temperature, throttle flags, static host info."""

from __future__ import annotations

import os
import platform
import socket
import time
from dataclasses import dataclass

import psutil

from . import read_file, run_cmd

THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
MODEL_PATH = "/proc/device-tree/model"
OS_RELEASE_PATH = "/etc/os-release"


@dataclass(frozen=True)
class ThrottleFlags:
    under_voltage: bool
    freq_capped: bool
    throttled: bool
    soft_temp_limit: bool
    past_under_voltage: bool
    past_freq_capped: bool
    past_throttled: bool
    past_soft_temp_limit: bool


def decode_throttled(text: str) -> ThrottleFlags | None:
    """Decode `vcgencmd get_throttled` output ("throttled=0x50005" or bare hex)."""
    value = text.strip().split("=")[-1]
    try:
        bits = int(value, 16)
    except ValueError:
        return None
    return ThrottleFlags(
        under_voltage=bool(bits & 0x1),
        freq_capped=bool(bits & 0x2),
        throttled=bool(bits & 0x4),
        soft_temp_limit=bool(bits & 0x8),
        past_under_voltage=bool(bits & 0x10000),
        past_freq_capped=bool(bits & 0x20000),
        past_throttled=bool(bits & 0x40000),
        past_soft_temp_limit=bool(bits & 0x80000),
    )


def parse_os_release(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


@dataclass(frozen=True)
class SystemStatic:
    hostname: str
    model: str | None
    os_name: str | None
    kernel: str | None
    core_count: int


def collect_static() -> SystemStatic:
    model = read_file(MODEL_PATH)
    return SystemStatic(
        hostname=socket.gethostname(),
        model=model.strip("\x00").strip() if model else None,
        os_name=parse_os_release(read_file(OS_RELEASE_PATH) or ""),
        kernel=platform.release(),
        core_count=psutil.cpu_count(logical=True) or 1,
    )


@dataclass(frozen=True)
class SystemFast:
    cpu_percent: float
    per_core: tuple[float, ...]
    load_avg: tuple[float, float, float] | None
    freq_mhz: float | None
    temp_c: float | None
    mem_total: int
    mem_used: int
    mem_percent: float
    swap_total: int
    swap_used: int
    swap_percent: float
    uptime_s: float


def read_temp_c() -> float | None:
    raw = read_file(THERMAL_PATH)
    if raw:
        try:
            return int(raw.strip()) / 1000.0
        except ValueError:
            pass
    out = run_cmd(["vcgencmd", "measure_temp"])  # temp=48.9'C
    if out and "=" in out:
        try:
            return float(out.split("=")[1].split("'")[0])
        except (ValueError, IndexError):
            return None
    return None


def collect_fast() -> SystemFast:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    try:
        freq = psutil.cpu_freq()
    except Exception:
        freq = None
    try:
        load: tuple[float, float, float] | None = os.getloadavg()
    except (AttributeError, OSError):
        load = None
    return SystemFast(
        cpu_percent=psutil.cpu_percent(),
        per_core=tuple(psutil.cpu_percent(percpu=True)),
        load_avg=load,
        freq_mhz=freq.current if freq else None,
        temp_c=read_temp_c(),
        mem_total=mem.total,
        mem_used=mem.used,
        mem_percent=mem.percent,
        swap_total=swap.total,
        swap_used=swap.used,
        swap_percent=swap.percent,
        uptime_s=time.time() - psutil.boot_time(),
    )


def collect_throttle() -> ThrottleFlags | None:
    out = run_cmd(["vcgencmd", "get_throttled"])
    return decode_throttled(out) if out else None
