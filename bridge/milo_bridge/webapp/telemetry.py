"""Telemetry snapshot pushed to every WS client and used by /api/status."""
from __future__ import annotations

import time

# The bridge deliberately avoids a psutil dependency; read the two numbers
# we need straight from the kernel, degrading to None off-Linux.


def _cpu_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return None


_last_cpu: tuple[float, float] | None = None


def _cpu_percent() -> float | None:
    global _last_cpu
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(float, parts))
        idle, total = vals[3] + vals[4], sum(vals)
    except (OSError, IndexError, ValueError):
        return None
    if _last_cpu is None:
        _last_cpu = (idle, total)
        return None
    didle, dtotal = idle - _last_cpu[0], total - _last_cpu[1]
    _last_cpu = (idle, total)
    if dtotal <= 0:
        return None
    return round(100.0 * (1.0 - didle / dtotal), 1)


def _mem_percent() -> float | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = float(v.strip().split()[0])
        return round(100.0 * (1.0 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        return None


_START = time.monotonic()


def collect_telemetry(deps) -> dict:
    imu = None
    if deps.imu is not None:
        try:
            state = deps.imu.update()
            imu = {
                "pitch": state.pitch, "roll": state.roll,
                "gyro": list(state.gyro), "accel": list(state.accel),
            }
        except Exception:
            imu = None
    return {
        "t": "telemetry",
        "cpu_percent": _cpu_percent(),
        "temp_c": _cpu_temp_c(),
        "mem_percent": _mem_percent(),
        "uptime_s": round(time.monotonic() - _START, 1),
        "link": deps.get_link_state(),
        "owner": deps.broker.owner if deps.broker else "none",
        "gait_backend": getattr(deps.gait, "backend", None),
        "imu": imu,
    }
