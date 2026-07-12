# MILO-Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-screen live Textual TUI dashboard (`milo-dash`) showing the Pi's system, network, storage, and milo-bridge service health, runnable off-Pi with `n/a` degradation.

**Architecture:** Pure-function collectors return frozen dataclasses; every external read goes through `run_cmd`/`read_file` seams that return `None` on any failure. Parsers are standalone functions tested with canned fixture text. The Textual app owns two refresh cadences (2 s psutil data, 10 s subprocess data), collects in worker threads, and pushes snapshots into dumb panel widgets.

**Tech Stack:** Python ≥3.11, `textual>=0.60`, `psutil>=5.9`, pytest. No `milo-bridge` dependency.

**Spec:** `docs/superpowers/specs/2026-07-13-milo-dashboard-design.md`

## Global Constraints

- Package name `milo-dashboard`, import name `milo_dashboard`, console script `milo-dash`, also runnable as `python -m milo_dashboard`.
- `requires-python = ">=3.11"`; deps exactly `textual>=0.60`, `psutil>=5.9`; dev extra `pytest>=8`.
- Depends only on textual + psutil — never import `milo_bridge`.
- All subprocess calls time out after 2.0 s and return `None` on any failure.
- Every snapshot field that can be unavailable is `Optional`; widgets render `None` as dim `n/a`.
- Data-path constants must match the bridge: `~/.milo/`, `~/.milo/graph.db`, `~/.milo/policy.onnx` (`bridge/milo_bridge/config.py:14,41-42`, `bridge/milo_bridge/main.py:33`).
- Fixture text for parser tests lives as string constants inside the test modules.
- Run tests from repo root: `python -m pytest MILO-Dashboard/tests -v`.
- Commit after each task, no co-author trailer.

---

### Task 1: Package scaffold + collector seams

**Files:**
- Create: `MILO-Dashboard/pyproject.toml`
- Create: `MILO-Dashboard/milo_dashboard/__init__.py`
- Create: `MILO-Dashboard/milo_dashboard/collectors/__init__.py`
- Create: `MILO-Dashboard/tests/__init__.py` (empty)
- Test: `MILO-Dashboard/tests/test_seams.py`

**Interfaces:**
- Produces: `milo_dashboard.collectors.run_cmd(args: list[str]) -> str | None`, `read_file(path: str | Path) -> str | None`, `CMD_TIMEOUT_S = 2.0`. All later collectors use these.

- [ ] **Step 1: Write the failing tests**

`MILO-Dashboard/tests/test_seams.py`:

```python
"""Seam tests: run_cmd/read_file must never raise."""

from milo_dashboard.collectors import read_file, run_cmd


def test_run_cmd_missing_binary_returns_none():
    assert run_cmd(["definitely-not-a-real-binary-xyz"]) is None


def test_run_cmd_nonzero_exit_returns_none():
    import sys
    out = run_cmd([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert out is None


def test_run_cmd_captures_stdout():
    import sys
    out = run_cmd([sys.executable, "-c", "print('hello')"])
    assert out is not None and out.strip() == "hello"


def test_read_file_missing_returns_none():
    assert read_file("/definitely/not/a/real/path/xyz") is None


def test_read_file_reads_text(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("data", encoding="utf-8")
    assert read_file(p) == "data"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest MILO-Dashboard/tests -v`
Expected: collection error `ModuleNotFoundError: No module named 'milo_dashboard'`

- [ ] **Step 3: Write scaffold + implementation**

`MILO-Dashboard/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "milo-dashboard"
version = "0.1.0"
description = "Project Milo dashboard: live TUI system/network/storage/services monitor for the Pi"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
dependencies = [
    "textual>=0.60",
    "psutil>=5.9",
]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
milo-dash = "milo_dashboard.app:main"

[tool.setuptools.packages.find]
include = ["milo_dashboard*"]
```

`MILO-Dashboard/milo_dashboard/__init__.py`:

```python
"""MILO Dashboard: live TUI system monitor for the robot's Pi."""

__version__ = "0.1.0"
```

`MILO-Dashboard/milo_dashboard/collectors/__init__.py`:

```python
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
```

Install editable: `pip install -e "MILO-Dashboard[dev]"`

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest MILO-Dashboard/tests -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: MILO-Dashboard package scaffold with collector seams"
```

---

### Task 2: System collector

**Files:**
- Create: `MILO-Dashboard/milo_dashboard/collectors/system.py`
- Test: `MILO-Dashboard/tests/test_system.py`

**Interfaces:**
- Consumes: `run_cmd`, `read_file` from Task 1.
- Produces: `ThrottleFlags` (bools: `under_voltage, freq_capped, throttled, soft_temp_limit, past_under_voltage, past_freq_capped, past_throttled, past_soft_temp_limit`), `decode_throttled(text: str) -> ThrottleFlags | None`, `parse_os_release(text: str) -> str | None`, `SystemStatic(hostname, model, os_name, kernel, core_count)`, `collect_static() -> SystemStatic`, `SystemFast(cpu_percent, per_core, load_avg, freq_mhz, temp_c, mem_total, mem_used, mem_percent, swap_total, swap_used, swap_percent, uptime_s)`, `collect_fast() -> SystemFast`, `collect_throttle() -> ThrottleFlags | None`, `read_temp_c() -> float | None`.

- [ ] **Step 1: Write the failing tests**

`MILO-Dashboard/tests/test_system.py`:

```python
from milo_dashboard.collectors.system import (
    collect_fast,
    collect_static,
    decode_throttled,
    parse_os_release,
)

OS_RELEASE = '''PRETTY_NAME="Raspberry Pi OS Lite (64-bit)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
'''


def test_decode_throttled_clean():
    f = decode_throttled("throttled=0x0")
    assert f is not None
    assert not f.under_voltage and not f.past_under_voltage
    assert not f.throttled and not f.past_throttled


def test_decode_throttled_past_events():
    # 0x50005: under-voltage + throttled now, and both in the past
    f = decode_throttled("throttled=0x50005")
    assert f.under_voltage and f.throttled
    assert f.past_under_voltage and f.past_throttled
    assert not f.freq_capped and not f.past_freq_capped


def test_decode_throttled_bare_hex_and_garbage():
    assert decode_throttled("0x10000").past_under_voltage
    assert decode_throttled("not hex") is None


def test_parse_os_release():
    assert parse_os_release(OS_RELEASE) == "Raspberry Pi OS Lite (64-bit)"
    assert parse_os_release("NAME=x") is None


def test_collect_static_never_raises():
    st = collect_static()
    assert st.hostname
    assert st.core_count >= 1


def test_collect_fast_never_raises():
    f = collect_fast()
    assert 0.0 <= f.mem_percent <= 100.0
    assert f.uptime_s > 0
    assert len(f.per_core) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest MILO-Dashboard/tests/test_system.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_dashboard.collectors.system'`

- [ ] **Step 3: Write implementation**

`MILO-Dashboard/milo_dashboard/collectors/system.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest MILO-Dashboard/tests/test_system.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: dashboard system collector (cpu/mem/temp/throttle)"
```

---

### Task 3: Network collector

**Files:**
- Create: `MILO-Dashboard/milo_dashboard/collectors/network.py`
- Test: `MILO-Dashboard/tests/test_network.py`

**Interfaces:**
- Consumes: `run_cmd` from Task 1.
- Produces: `Interface(name, is_up, ipv4, mac)`, `WifiLink(ssid, signal_dbm, bitrate_mbps)`, `parse_iw_link(text) -> WifiLink | None`, `parse_default_route(text) -> str | None`, `RateTracker` with `.update(rx_total, tx_total, now=None) -> (rx_bps, tx_bps)`, `NetworkFast(interfaces, rx_rate_bps, tx_rate_bps, rx_total, tx_total)`, `collect_fast(tracker) -> NetworkFast`, `NetworkSlow(wifi, gateway, internet_ok)`, `collect_slow() -> NetworkSlow`, `check_internet() -> bool`.

- [ ] **Step 1: Write the failing tests**

`MILO-Dashboard/tests/test_network.py`:

```python
from milo_dashboard.collectors.network import (
    RateTracker,
    collect_fast,
    parse_default_route,
    parse_iw_link,
)

IW_CONNECTED = """Connected to aa:bb:cc:dd:ee:ff (on wlan0)
\tSSID: HomeNet-5G
\tfreq: 2437
\tRX: 123456 bytes (789 packets)
\tTX: 65432 bytes (321 packets)
\tsignal: -52 dBm
\trx bitrate: 65.0 MBit/s
\ttx bitrate: 72.2 MBit/s MCS 7 short GI
"""

IW_NOT_CONNECTED = "Not connected.\n"

IP_ROUTE = "default via 192.168.1.1 dev wlan0 proto dhcp src 192.168.1.42 metric 600\n"


def test_parse_iw_link_connected():
    link = parse_iw_link(IW_CONNECTED)
    assert link is not None
    assert link.ssid == "HomeNet-5G"
    assert link.signal_dbm == -52
    assert link.bitrate_mbps == 72.2


def test_parse_iw_link_not_connected():
    assert parse_iw_link(IW_NOT_CONNECTED) is None
    assert parse_iw_link("") is None


def test_parse_default_route():
    assert parse_default_route(IP_ROUTE) == "192.168.1.1"
    assert parse_default_route("") is None


def test_rate_tracker_first_call_zero():
    t = RateTracker()
    assert t.update(1000, 500, now=10.0) == (0.0, 0.0)


def test_rate_tracker_computes_rates():
    t = RateTracker()
    t.update(1000, 500, now=10.0)
    rx, tx = t.update(3000, 1500, now=12.0)
    assert rx == 1000.0  # (3000-1000)/2s
    assert tx == 500.0


def test_rate_tracker_counter_reset_yields_zero():
    t = RateTracker()
    t.update(5000, 5000, now=10.0)
    assert t.update(100, 100, now=12.0) == (0.0, 0.0)


def test_collect_fast_never_raises():
    snap = collect_fast(RateTracker())
    assert snap.rx_total >= 0
    for iface in snap.interfaces:
        assert iface.name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest MILO-Dashboard/tests/test_network.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_dashboard.collectors.network'`

- [ ] **Step 3: Write implementation**

`MILO-Dashboard/milo_dashboard/collectors/network.py`:

```python
"""Network stats: interfaces, Wi-Fi link, gateway, RX/TX rates, reachability."""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass

import psutil

from . import run_cmd

WIFI_IFACE = "wlan0"


@dataclass(frozen=True)
class Interface:
    name: str
    is_up: bool
    ipv4: str | None
    mac: str | None


@dataclass(frozen=True)
class WifiLink:
    ssid: str
    signal_dbm: int | None
    bitrate_mbps: float | None


def parse_iw_link(text: str) -> WifiLink | None:
    if not text or text.strip().startswith("Not connected"):
        return None
    m = re.search(r"SSID:\s*(.+)", text)
    if m is None:
        return None
    ssid = m.group(1).strip()
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", text)
    signal = int(m.group(1)) if m else None
    m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", text)
    bitrate = float(m.group(1)) if m else None
    return WifiLink(ssid=ssid, signal_dbm=signal, bitrate_mbps=bitrate)


def parse_default_route(text: str) -> str | None:
    m = re.search(r"default via (\S+)", text or "")
    return m.group(1) if m else None


class RateTracker:
    """Turns two successive byte-counter readings into bytes/sec rates."""

    def __init__(self) -> None:
        self._last: tuple[float, int, int] | None = None

    def update(
        self, rx_total: int, tx_total: int, now: float | None = None
    ) -> tuple[float, float]:
        now = time.monotonic() if now is None else now
        rates = (0.0, 0.0)
        if self._last is not None:
            t0, rx0, tx0 = self._last
            dt = now - t0
            if dt > 0 and rx_total >= rx0 and tx_total >= tx0:
                rates = ((rx_total - rx0) / dt, (tx_total - tx0) / dt)
        self._last = (now, rx_total, tx_total)
        return rates


@dataclass(frozen=True)
class NetworkFast:
    interfaces: tuple[Interface, ...]
    rx_rate_bps: float
    tx_rate_bps: float
    rx_total: int
    tx_total: int


def collect_fast(tracker: RateTracker) -> NetworkFast:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    interfaces = []
    for name in sorted(addrs):
        if name == "lo" or name.lower().startswith("loopback"):
            continue
        ipv4 = mac = None
        for a in addrs[name]:
            if a.family == socket.AF_INET:
                ipv4 = a.address
            elif a.family == psutil.AF_LINK:
                mac = a.address
        st = stats.get(name)
        interfaces.append(
            Interface(name=name, is_up=bool(st and st.isup), ipv4=ipv4, mac=mac)
        )
    io = psutil.net_io_counters()
    rx_rate, tx_rate = tracker.update(io.bytes_recv, io.bytes_sent)
    return NetworkFast(
        interfaces=tuple(interfaces),
        rx_rate_bps=rx_rate,
        tx_rate_bps=tx_rate,
        rx_total=io.bytes_recv,
        tx_total=io.bytes_sent,
    )


@dataclass(frozen=True)
class NetworkSlow:
    wifi: WifiLink | None
    gateway: str | None
    internet_ok: bool | None


def check_internet(host: str = "1.1.1.1", port: int = 53, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect_slow() -> NetworkSlow:
    return NetworkSlow(
        wifi=parse_iw_link(run_cmd(["iw", "dev", WIFI_IFACE, "link"]) or ""),
        gateway=parse_default_route(run_cmd(["ip", "route", "show", "default"]) or ""),
        internet_ok=check_internet(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest MILO-Dashboard/tests/test_network.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: dashboard network collector (ifaces/wifi/rates)"
```

---

### Task 4: Storage collector

**Files:**
- Create: `MILO-Dashboard/milo_dashboard/collectors/storage.py`
- Test: `MILO-Dashboard/tests/test_storage.py`

**Interfaces:**
- Consumes: nothing beyond psutil/stdlib.
- Produces: `Disk(mount, total, used, percent)`, `MiloData(milo_dir_bytes, graph_db_bytes, policy_bytes)`, `StorageSnapshot(disks, milo)`, `collect() -> StorageSnapshot`, `keep_partition(fstype: str) -> bool`, `dir_size(path: Path) -> int | None`, `file_size(path: Path) -> int | None`, constants `MILO_DIR`, `GRAPH_DB`, `POLICY`.

- [ ] **Step 1: Write the failing tests**

`MILO-Dashboard/tests/test_storage.py`:

```python
from pathlib import Path

from milo_dashboard.collectors.storage import (
    GRAPH_DB,
    MILO_DIR,
    POLICY,
    collect,
    dir_size,
    file_size,
    keep_partition,
)


def test_milo_paths_match_bridge_conventions():
    assert MILO_DIR == Path.home() / ".milo"
    assert GRAPH_DB == MILO_DIR / "graph.db"
    assert POLICY == MILO_DIR / "policy.onnx"


def test_keep_partition_filters_pseudo_filesystems():
    assert keep_partition("ext4")
    assert keep_partition("vfat")
    assert not keep_partition("tmpfs")
    assert not keep_partition("squashfs")
    assert not keep_partition("")


def test_dir_size_missing_dir_is_none(tmp_path):
    assert dir_size(tmp_path / "nope") is None


def test_dir_size_sums_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 5)
    assert dir_size(tmp_path) == 15


def test_file_size(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"z" * 7)
    assert file_size(p) == 7
    assert file_size(tmp_path / "missing") is None


def test_collect_never_raises():
    snap = collect()
    assert isinstance(snap.disks, tuple)
    for d in snap.disks:
        assert 0.0 <= d.percent <= 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest MILO-Dashboard/tests/test_storage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_dashboard.collectors.storage'`

- [ ] **Step 3: Write implementation**

`MILO-Dashboard/milo_dashboard/collectors/storage.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest MILO-Dashboard/tests/test_storage.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: dashboard storage collector (disks + ~/.milo data)"
```

---

### Task 5: Services collector

**Files:**
- Create: `MILO-Dashboard/milo_dashboard/collectors/services.py`
- Test: `MILO-Dashboard/tests/test_services.py`

**Interfaces:**
- Consumes: `run_cmd`, `read_file` from Task 1.
- Produces: `UnitStatus(name, active_state, sub_state, pid, n_restarts, since)`, `parse_systemctl_show(name, text) -> UnitStatus | None`, `parse_asound_cards(text) -> bool`, `HardwarePresence(i2c, camera, voicehat)`, `ProcessInfo(pid, name, cpu_percent, mem_percent)`, `top_processes(limit=5) -> tuple[ProcessInfo, ...]`, `BridgeProcess(cpu_percent, rss_bytes)`, `JournalTail(lines, error)`, `ServicesSnapshot(bridge, bridge_proc, extras, hardware, journal)`, `collect_slow() -> ServicesSnapshot`, constants `BRIDGE_UNIT = "milo-bridge.service"`, `EXTRA_UNITS = ("ssh.service", "avahi-daemon.service")`.

- [ ] **Step 1: Write the failing tests**

`MILO-Dashboard/tests/test_services.py`:

```python
from milo_dashboard.collectors.services import (
    collect_slow,
    parse_asound_cards,
    parse_systemctl_show,
    top_processes,
)

SHOW_ACTIVE = """ActiveState=active
SubState=running
MainPID=1234
NRestarts=2
ActiveEnterTimestamp=Sun 2026-07-12 20:11:03 BST
"""

SHOW_FAILED = """ActiveState=failed
SubState=failed
MainPID=0
NRestarts=5
ActiveEnterTimestamp=
"""

ASOUND_CARDS = """ 0 [sndrpigooglevoi]: RPi-simple - snd_rpi_googlevoicehat_soundcar
                      snd_rpi_googlevoicehat_soundcard
"""


def test_parse_systemctl_show_active():
    u = parse_systemctl_show("milo-bridge.service", SHOW_ACTIVE)
    assert u is not None
    assert u.active_state == "active" and u.sub_state == "running"
    assert u.pid == 1234
    assert u.n_restarts == 2
    assert u.since == "Sun 2026-07-12 20:11:03 BST"


def test_parse_systemctl_show_failed_has_no_pid():
    u = parse_systemctl_show("milo-bridge.service", SHOW_FAILED)
    assert u.active_state == "failed"
    assert u.pid is None
    assert u.since is None


def test_parse_systemctl_show_empty_returns_none():
    assert parse_systemctl_show("x.service", "") is None


def test_parse_asound_cards():
    assert parse_asound_cards(ASOUND_CARDS)
    assert not parse_asound_cards(" 0 [Headphones]: bcm2835 Headphones")
    assert not parse_asound_cards("")


def test_top_processes_sorted_and_limited():
    procs = top_processes(limit=3)
    assert len(procs) <= 3
    cpus = [p.cpu_percent for p in procs]
    assert cpus == sorted(cpus, reverse=True)


def test_collect_slow_never_raises():
    snap = collect_slow()
    assert isinstance(snap.journal.lines, tuple)
    assert isinstance(snap.hardware.i2c, bool)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest MILO-Dashboard/tests/test_services.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_dashboard.collectors.services'`

- [ ] **Step 3: Write implementation**

`MILO-Dashboard/milo_dashboard/collectors/services.py`:

```python
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
        return BridgeProcess(cpu_percent=p.cpu_percent(interval=None), rss_bytes=p.memory_info().rss)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest MILO-Dashboard/tests/test_services.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: dashboard services collector (systemd/journal/hardware/top procs)"
```

---

### Task 6: Widgets and formatters

**Files:**
- Create: `MILO-Dashboard/milo_dashboard/widgets.py`
- Test: `MILO-Dashboard/tests/test_widgets.py`

**Interfaces:**
- Consumes: all snapshot dataclasses from Tasks 2-5.
- Produces: pure formatters `bar(percent, width=20) -> str`, `fmt_bytes(n) -> str`, `fmt_rate(bps) -> str`, `fmt_duration(seconds) -> str`, `temp_markup(temp_c) -> str`, `throttle_markup(flags) -> str`, `NA = "[dim]n/a[/]"`; panel widgets `SystemPanel` (`update_fast(static, fast)`, `update_throttle(flags)`), `NetworkPanel` (`update_fast(fast)`, `update_slow(slow)`), `StoragePanel` (`update_storage(snap)`), `ServicesPanel` (`update_services(snap)`, `update_procs(procs)`), `JournalPanel` (`update_journal(tail)`).

- [ ] **Step 1: Write the failing tests (formatters only — panels are render-only)**

`MILO-Dashboard/tests/test_widgets.py`:

```python
from milo_dashboard.collectors.system import decode_throttled
from milo_dashboard.widgets import bar, fmt_bytes, fmt_duration, fmt_rate, throttle_markup


def test_bar_clamps_and_colors():
    assert "[green]" in bar(10.0)
    assert "[yellow]" in bar(70.0)
    assert "[red]" in bar(95.0)
    assert "100.0%" in bar(250.0)  # clamped
    assert "  0.0%" in bar(-5.0)


def test_fmt_bytes():
    assert fmt_bytes(None) == "n/a"
    assert fmt_bytes(512) == "512 B"
    assert fmt_bytes(2048) == "2.0 KiB"
    assert fmt_bytes(3 * 1024**3) == "3.0 GiB"


def test_fmt_rate():
    assert fmt_rate(None) == "n/a"
    assert fmt_rate(2048.0) == "2.0 KiB/s"


def test_fmt_duration():
    assert fmt_duration(None) == "n/a"
    assert fmt_duration(59) == "0m 59s"
    assert fmt_duration(3660) == "1h 1m"
    assert fmt_duration(90061) == "1d 1h 1m"


def test_throttle_markup():
    assert "[green]OK[/]" in throttle_markup(decode_throttled("0x0"))
    bad = throttle_markup(decode_throttled("0x50005"))
    assert "UNDER-VOLTAGE" in bad and "THROTTLED" in bad
    assert "past:" in bad
    assert throttle_markup(None) == "[dim]n/a[/]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest MILO-Dashboard/tests/test_widgets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_dashboard.widgets'`

- [ ] **Step 3: Write implementation**

`MILO-Dashboard/milo_dashboard/widgets.py`:

```python
"""Dumb panel widgets and pure formatters. No widget shells out or reads files."""

from __future__ import annotations

from rich.markup import escape
from textual.containers import VerticalScroll
from textual.widgets import Static

from .collectors.network import NetworkFast, NetworkSlow
from .collectors.services import ProcessInfo, ServicesSnapshot, UnitStatus
from .collectors.storage import StorageSnapshot
from .collectors.system import SystemFast, SystemStatic, ThrottleFlags

NA = "[dim]n/a[/]"


def bar(percent: float, width: int = 20) -> str:
    pct = max(0.0, min(100.0, percent))
    filled = round(pct / 100 * width)
    color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
    return f"[{color}]{'█' * filled}[/]{'░' * (width - filled)} {pct:5.1f}%"


def fmt_bytes(n: int | float | None) -> str:
    if n is None:
        return "n/a"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def fmt_rate(bps: float | None) -> str:
    return "n/a" if bps is None else f"{fmt_bytes(bps)}/s"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def temp_markup(temp_c: float | None) -> str:
    if temp_c is None:
        return NA
    color = "green" if temp_c < 60 else "yellow" if temp_c < 70 else "red"
    return f"[{color}]{temp_c:.1f} °C[/]"


def throttle_markup(flags: ThrottleFlags | None) -> str:
    if flags is None:
        return NA
    problems = []
    if flags.under_voltage:
        problems.append("[red]UNDER-VOLTAGE[/]")
    if flags.throttled:
        problems.append("[red]THROTTLED[/]")
    if flags.freq_capped:
        problems.append("[yellow]FREQ-CAPPED[/]")
    if flags.soft_temp_limit:
        problems.append("[yellow]SOFT-TEMP-LIMIT[/]")
    past = [
        label
        for flag, label in (
            (flags.past_under_voltage, "under-voltage"),
            (flags.past_throttled, "throttled"),
            (flags.past_freq_capped, "freq-capped"),
            (flags.past_soft_temp_limit, "soft-temp-limit"),
        )
        if flag
    ]
    out = " ".join(problems) if problems else "[green]OK[/]"
    if past:
        out += f"  [dim](past: {', '.join(past)})[/]"
    return out


def _state_markup(unit: UnitStatus | None, name: str) -> str:
    if unit is None:
        return f"{name:<14}{NA}"
    color = {"active": "green", "failed": "red"}.get(unit.active_state, "yellow")
    return f"{name:<14}[{color}]{unit.active_state}[/] ({unit.sub_state})"


class DashPanel(Static):
    """Static panel with a border title; subclasses only render snapshots."""

    TITLE = ""

    def on_mount(self) -> None:
        self.border_title = self.TITLE


class SystemPanel(DashPanel):
    TITLE = "SYSTEM"

    def __init__(self) -> None:
        super().__init__("collecting…")
        self._static: SystemStatic | None = None
        self._fast: SystemFast | None = None
        self._throttle: ThrottleFlags | None = None

    def update_fast(self, static: SystemStatic, fast: SystemFast) -> None:
        self._static, self._fast = static, fast
        self._render_panel()

    def update_throttle(self, flags: ThrottleFlags | None) -> None:
        self._throttle = flags
        self._render_panel()

    def _render_panel(self) -> None:
        st, f = self._static, self._fast
        if st is None or f is None:
            return
        lines = [f"CPU    {bar(f.cpu_percent)}"]
        lines += [f" core{i} {bar(c, 14)}" for i, c in enumerate(f.per_core)]
        load = (
            f"{f.load_avg[0]:.2f} {f.load_avg[1]:.2f} {f.load_avg[2]:.2f}"
            if f.load_avg
            else "n/a"
        )
        lines += [
            f"Load   {load}  ({st.core_count} cores)",
            f"Freq   {f.freq_mhz:.0f} MHz" if f.freq_mhz else f"Freq   {NA}",
            f"Temp   {temp_markup(f.temp_c)}",
            f"Power  {throttle_markup(self._throttle)}",
            f"RAM    {bar(f.mem_percent)}  {fmt_bytes(f.mem_used)} / {fmt_bytes(f.mem_total)}",
            f"Swap   {bar(f.swap_percent)}  {fmt_bytes(f.swap_used)} / {fmt_bytes(f.swap_total)}",
            "",
            f"Model  {escape(st.model) if st.model else 'n/a'}",
            f"OS     {escape(st.os_name or 'n/a')}",
            f"Kernel {escape(st.kernel or 'n/a')}",
        ]
        self.update("\n".join(lines))


class NetworkPanel(DashPanel):
    TITLE = "NETWORK"

    def __init__(self) -> None:
        super().__init__("collecting…")
        self._fast: NetworkFast | None = None
        self._slow: NetworkSlow | None = None

    def update_fast(self, fast: NetworkFast) -> None:
        self._fast = fast
        self._render_panel()

    def update_slow(self, slow: NetworkSlow) -> None:
        self._slow = slow
        self._render_panel()

    def _render_panel(self) -> None:
        f = self._fast
        if f is None:
            return
        lines = []
        for iface in f.interfaces:
            dot = "[green]●[/]" if iface.is_up else "[red]○[/]"
            lines.append(
                f"{dot} {escape(iface.name):<8} {iface.ipv4 or NA}"
                f"  [dim]{iface.mac or ''}[/]"
            )
        s = self._slow
        if s is not None:
            if s.wifi:
                sig = f"{s.wifi.signal_dbm} dBm" if s.wifi.signal_dbm is not None else "n/a"
                rate = (
                    f"{s.wifi.bitrate_mbps:.0f} Mbit/s"
                    if s.wifi.bitrate_mbps is not None
                    else "n/a"
                )
                lines.append(f"Wi-Fi  {escape(s.wifi.ssid)}  {sig}  {rate}")
            else:
                lines.append(f"Wi-Fi  {NA}")
            lines.append(f"GW     {s.gateway or 'n/a'}")
            inet = (
                NA
                if s.internet_ok is None
                else ("[green]reachable[/]" if s.internet_ok else "[red]unreachable[/]")
            )
            lines.append(f"Net    {inet}")
        lines += [
            "",
            f"RX     {fmt_rate(f.rx_rate_bps):<12} total {fmt_bytes(f.rx_total)}",
            f"TX     {fmt_rate(f.tx_rate_bps):<12} total {fmt_bytes(f.tx_total)}",
        ]
        self.update("\n".join(lines))


class StoragePanel(DashPanel):
    TITLE = "STORAGE"

    def update_storage(self, snap: StorageSnapshot) -> None:
        lines = []
        for d in snap.disks:
            lines.append(f"{escape(d.mount)}")
            lines.append(
                f"  {bar(d.percent, 16)}  {fmt_bytes(d.used)} / {fmt_bytes(d.total)}"
            )
        m = snap.milo
        lines += [
            "",
            "MILO data",
            f"  ~/.milo      {fmt_bytes(m.milo_dir_bytes)}",
            f"  graph.db     {fmt_bytes(m.graph_db_bytes)}",
            f"  policy.onnx  {fmt_bytes(m.policy_bytes) if m.policy_bytes is not None else '[yellow]missing[/]'}",
        ]
        self.update("\n".join(lines))


class ServicesPanel(DashPanel):
    TITLE = "SERVICES & ROBOT"

    def __init__(self) -> None:
        super().__init__("collecting…")
        self._snap: ServicesSnapshot | None = None
        self._procs: tuple[ProcessInfo, ...] = ()

    def update_services(self, snap: ServicesSnapshot) -> None:
        self._snap = snap
        self._render_panel()

    def update_procs(self, procs: tuple[ProcessInfo, ...]) -> None:
        self._procs = procs
        self._render_panel()

    def _render_panel(self) -> None:
        lines = []
        s = self._snap
        if s is not None:
            lines.append(_state_markup(s.bridge, "milo-bridge"))
            if s.bridge:
                pid = s.bridge.pid or "-"
                restarts = s.bridge.n_restarts if s.bridge.n_restarts is not None else "-"
                lines.append(f"  pid {pid}  restarts {restarts}")
                if s.bridge.since:
                    lines.append(f"  since {escape(s.bridge.since)}")
            if s.bridge_proc:
                lines.append(
                    f"  cpu {s.bridge_proc.cpu_percent:.1f}%  rss {fmt_bytes(s.bridge_proc.rss_bytes)}"
                )
            for unit in s.extras:
                if unit is not None:
                    lines.append(_state_markup(unit, unit.name.removesuffix(".service")))
            hw = s.hardware
            def yn(ok: bool) -> str:
                return "[green]yes[/]" if ok else "[red]no[/]"
            lines += [
                "",
                f"i2c-1 {yn(hw.i2c)}   camera {yn(hw.camera)}   voicehat {yn(hw.voicehat)}",
            ]
        if self._procs:
            lines += ["", "Top processes (cpu)"]
            for p in self._procs:
                lines.append(
                    f"  {p.pid:>6} {escape(p.name)[:18]:<18} {p.cpu_percent:5.1f}%  {p.mem_percent:4.1f}%"
                )
        if lines:
            self.update("\n".join(lines))


class JournalPanel(VerticalScroll):
    def compose(self):
        yield Static("collecting…", id="journal-text")

    def on_mount(self) -> None:
        self.border_title = "MILO-BRIDGE LOG"

    def update_journal(self, tail) -> None:
        body = self.query_one("#journal-text", Static)
        if tail.error:
            body.update(f"[yellow]{escape(tail.error)}[/]")
        elif tail.lines:
            body.update("\n".join(escape(line) for line in tail.lines))
        else:
            body.update("[dim]no journal lines[/]")
        self.scroll_end(animate=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest MILO-Dashboard/tests/test_widgets.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: dashboard panels and formatters"
```

---

### Task 7: App, entry points, --check mode, README

**Files:**
- Create: `MILO-Dashboard/milo_dashboard/app.py`
- Create: `MILO-Dashboard/milo_dashboard/check.py`
- Create: `MILO-Dashboard/milo_dashboard/__main__.py`
- Create: `MILO-Dashboard/README.md`
- Test: `MILO-Dashboard/tests/test_check.py`

**Interfaces:**
- Consumes: all collectors (Tasks 2-5) and panels (Task 6).
- Produces: `MiloDashApp` (Textual App), `main()` console entry (handles `--check`), `check.render_report() -> str`.

- [ ] **Step 1: Write the failing test for the --check report**

`MILO-Dashboard/tests/test_check.py`:

```python
from milo_dashboard.check import render_report


def test_render_report_contains_all_sections():
    report = render_report()
    for heading in ("SYSTEM", "NETWORK", "STORAGE", "SERVICES"):
        assert heading in report
    assert "CPU" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest MILO-Dashboard/tests/test_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_dashboard.check'`

- [ ] **Step 3: Write implementation**

`MILO-Dashboard/milo_dashboard/check.py`:

```python
"""--check mode: collect everything once and print a plain-text report.

Useful over a bare SSH session and for verifying collectors on any machine.
"""

from __future__ import annotations

from .collectors import network, services, storage, system
from .widgets import fmt_bytes, fmt_duration, fmt_rate


def _opt(value, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def render_report() -> str:
    st = system.collect_static()
    fast = system.collect_fast()
    throttle = system.collect_throttle()
    net_fast = network.collect_fast(network.RateTracker())
    net_slow = network.collect_slow()
    stor = storage.collect()
    svc = services.collect_slow()
    procs = services.top_processes()

    lines = [
        f"== SYSTEM ({st.hostname}) ==",
        f"model: {_opt(st.model)} | os: {_opt(st.os_name)} | kernel: {_opt(st.kernel)}",
        f"CPU {fast.cpu_percent:.1f}% | load {_opt(fast.load_avg)} | freq {_opt(fast.freq_mhz, ' MHz')}",
        f"temp {_opt(fast.temp_c, ' C')} | throttle {'n/a' if throttle is None else throttle}",
        f"mem {fmt_bytes(fast.mem_used)}/{fmt_bytes(fast.mem_total)} ({fast.mem_percent:.0f}%)"
        f" | swap {fast.swap_percent:.0f}% | up {fmt_duration(fast.uptime_s)}",
        "",
        "== NETWORK ==",
    ]
    for i in net_fast.interfaces:
        lines.append(f"{'UP ' if i.is_up else 'DOWN'} {i.name}: {_opt(i.ipv4)} [{_opt(i.mac)}]")
    lines += [
        f"wifi: {net_slow.wifi} | gw: {_opt(net_slow.gateway)} | internet: {_opt(net_slow.internet_ok)}",
        f"rx {fmt_rate(net_fast.rx_rate_bps)} (total {fmt_bytes(net_fast.rx_total)})"
        f" | tx {fmt_rate(net_fast.tx_rate_bps)} (total {fmt_bytes(net_fast.tx_total)})",
        "",
        "== STORAGE ==",
    ]
    for d in stor.disks:
        lines.append(f"{d.mount}: {fmt_bytes(d.used)}/{fmt_bytes(d.total)} ({d.percent:.0f}%)")
    m = stor.milo
    lines += [
        f"~/.milo {fmt_bytes(m.milo_dir_bytes)} | graph.db {fmt_bytes(m.graph_db_bytes)}"
        f" | policy.onnx {fmt_bytes(m.policy_bytes)}",
        "",
        "== SERVICES ==",
        f"milo-bridge: {svc.bridge}",
        f"bridge proc: {svc.bridge_proc}",
    ]
    for unit in svc.extras:
        lines.append(f"unit: {unit}")
    hw = svc.hardware
    lines += [
        f"hardware: i2c={hw.i2c} camera={hw.camera} voicehat={hw.voicehat}",
        f"journal: {svc.journal.error or f'{len(svc.journal.lines)} lines'}",
        "top procs: " + ", ".join(f"{p.name}({p.cpu_percent:.0f}%)" for p in procs),
    ]
    return "\n".join(lines)


def run() -> None:
    print(render_report())
```

`MILO-Dashboard/milo_dashboard/app.py`:

```python
"""MILO Dashboard Textual app: layout, refresh timers, keybindings."""

from __future__ import annotations

import sys
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static

from .collectors import network, services, storage, system
from .widgets import (
    JournalPanel,
    NetworkPanel,
    ServicesPanel,
    StoragePanel,
    SystemPanel,
    fmt_duration,
)

FAST_INTERVAL_S = 2.0
SLOW_INTERVAL_S = 10.0


class TopBar(Static):
    def update_bar(self, hostname: str, uptime_s: float) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.update(
            f"[b]MILO DASHBOARD[/b]  ·  {hostname}  ·  up {fmt_duration(uptime_s)}  ·  {now}"
        )


class MiloDashApp(App):
    TITLE = "MILO Dashboard"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]
    CSS = """
    TopBar {
        dock: top;
        height: 1;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
    }
    #row-top { height: auto; }
    #row-bottom { height: 1fr; }
    SystemPanel, NetworkPanel, StoragePanel {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    ServicesPanel, JournalPanel {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.static_info = system.collect_static()
        self.rate_tracker = network.RateTracker()

    def compose(self) -> ComposeResult:
        yield TopBar()
        with Vertical():
            with Horizontal(id="row-top"):
                yield SystemPanel()
                yield NetworkPanel()
                yield StoragePanel()
            with Horizontal(id="row-bottom"):
                yield ServicesPanel()
                yield JournalPanel()
        yield Footer()

    def on_mount(self) -> None:
        self._tick_fast()
        self._tick_slow()
        self.set_interval(FAST_INTERVAL_S, self._tick_fast)
        self.set_interval(SLOW_INTERVAL_S, self._tick_slow)

    def _tick_fast(self) -> None:
        self.run_worker(self._collect_fast, thread=True, exclusive=True, group="fast")

    def _tick_slow(self) -> None:
        self.run_worker(self._collect_slow, thread=True, exclusive=True, group="slow")

    def _collect_fast(self) -> None:
        sys_fast = system.collect_fast()
        net_fast = network.collect_fast(self.rate_tracker)
        procs = services.top_processes()
        self.call_from_thread(self._apply_fast, sys_fast, net_fast, procs)

    def _apply_fast(self, sys_fast, net_fast, procs) -> None:
        self.query_one(TopBar).update_bar(self.static_info.hostname, sys_fast.uptime_s)
        self.query_one(SystemPanel).update_fast(self.static_info, sys_fast)
        self.query_one(NetworkPanel).update_fast(net_fast)
        self.query_one(ServicesPanel).update_procs(procs)

    def _collect_slow(self) -> None:
        throttle = system.collect_throttle()
        net_slow = network.collect_slow()
        stor = storage.collect()
        svc = services.collect_slow()
        self.call_from_thread(self._apply_slow, throttle, net_slow, stor, svc)

    def _apply_slow(self, throttle, net_slow, stor, svc) -> None:
        self.query_one(SystemPanel).update_throttle(throttle)
        self.query_one(NetworkPanel).update_slow(net_slow)
        self.query_one(StoragePanel).update_storage(stor)
        self.query_one(ServicesPanel).update_services(svc)
        self.query_one(JournalPanel).update_journal(svc.journal)

    def action_refresh(self) -> None:
        self._tick_fast()
        self._tick_slow()


def main() -> None:
    if "--check" in sys.argv:
        from . import check

        check.run()
        return
    MiloDashApp().run()
```

`MILO-Dashboard/milo_dashboard/__main__.py`:

```python
from .app import main

if __name__ == "__main__":
    main()
```

`MILO-Dashboard/README.md`:

```markdown
# MILO-Dashboard — Live TUI System Dashboard

One full-screen, auto-refreshing dashboard for Milo's Pi: system load,
temperature and throttling, network, storage, and `milo-bridge` service
health — everything visible at once over a plain SSH session.

## Install (on the Pi)

    cd ~/MILO-Robot
    source ~/.venvs/milo/bin/activate     # or any venv
    pip install -e MILO-Dashboard

## Run

    cd ~/MILO-Robot/MILO-Dashboard
    milo-dash                 # full live dashboard
    milo-dash --check         # one-shot plain-text report (no TUI)
    python -m milo_dashboard  # same as milo-dash

## Keybindings

| Key | Action |
| --- | ------ |
| `q` | Quit |
| `r` | Force refresh of every panel |

## Panels

- **SYSTEM** — CPU total + per-core bars, load average, CPU frequency, SoC
  temperature (green < 60 °C, yellow < 70 °C, red ≥ 70 °C), decoded
  `vcgencmd get_throttled` flags (current and past under-voltage /
  freq-capped / throttled / soft-temp-limit), RAM/swap bars, Pi model, OS,
  kernel.
- **NETWORK** — every interface with state/IP/MAC, Wi-Fi SSID + signal +
  bitrate, default gateway, internet reachability, live RX/TX rates and
  lifetime totals.
- **STORAGE** — each real filesystem with a fullness bar, plus MILO data:
  `~/.milo` size, `graph.db` size, `policy.onnx` presence.
- **SERVICES & ROBOT** — `milo-bridge.service` state/PID/restarts and live
  CPU/RSS, ssh + avahi status, hardware presence (`/dev/i2c-1`,
  `/dev/video*`, voicehat sound card), top processes by CPU.
- **MILO-BRIDGE LOG** — scrollable tail of `journalctl -u milo-bridge`.

Refresh cadence: cheap stats every 2 s; subprocess-based stats
(vcgencmd, iw, systemctl, journalctl) every 10 s. Collection runs in a
worker thread so the UI never freezes.

## Off-Pi behaviour

Every collector degrades gracefully: on a dev machine (Windows/macOS) or a
stripped Pi image, missing commands and files render as `n/a` instead of
crashing. If the journal panel shows an error, add your user to the
`systemd-journal` group: `sudo usermod -aG systemd-journal $USER`.

## Tests

    python -m pytest MILO-Dashboard/tests -v
```

- [ ] **Step 4: Run all tests and the --check smoke**

Run: `python -m pytest MILO-Dashboard/tests -v`
Expected: all pass (seams + system + network + storage + services + widgets + check)

Run: `python -m milo_dashboard --check`
Expected: plain-text report prints SYSTEM/NETWORK/STORAGE/SERVICES sections with real values on this machine and `n/a`/`None` for Pi-only data; exit code 0.

- [ ] **Step 5: Commit**

```bash
git add MILO-Dashboard
git commit -m "feat: MILO-Dashboard TUI app with --check mode and README"
```

---

## Self-review notes

- Spec coverage: System panel (Task 2+6), Network (3+6), Storage (4+6), Services & Robot + journal (5+6), layout/cadence/keybindings (7), off-Pi degradation (seams in 1, Optional fields throughout), parser tests with canned fixtures (2-5), README (7). `--check` is an addition beyond the spec for SSH-friendly one-shot output and test-driven verification of the composition.
- Deviation from spec: fixture text lives as constants inside test modules rather than a `tests/fixtures/` directory — same canned data, less plumbing.
- Type consistency: panel method names used in `app.py` (`update_fast`, `update_slow`, `update_storage`, `update_services`, `update_procs`, `update_journal`, `update_throttle`, `update_bar`) match Task 6 definitions; collector signatures in Tasks 2-5 match their uses in Tasks 6-7.
