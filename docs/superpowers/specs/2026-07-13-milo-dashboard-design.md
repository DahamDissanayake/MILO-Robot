# MILO-Dashboard — Live TUI System Dashboard

**Date:** 2026-07-13
**Status:** Approved

## Purpose

Once Milo's Pi is on the LAN, the operator needs a single place to see the
robot's overall health after SSH-ing in: system load, temperature and
throttling, network state, storage, and whether the `milo-bridge` backend is
actually running. `MILO-Dashboard/` becomes a sixth installable package in
this repo (alongside `common/`, `bridge/`, `brain/`, `training/`,
`IOT-Testing/`), built as a [Textual](https://textual.textualize.io/) TUI:
one full screen, every panel visible at once, auto-refreshing live. The
operator logs into the Pi, `cd MILO-Robot/MILO-Dashboard`, and runs
`milo-dash` (or `python -m milo_dashboard`).

## Scope

In scope: a single-screen live dashboard with four panel groups (System,
Network, Storage, Services & Robot), a `milo-dash` console script, graceful
off-Pi degradation so the app runs (with `n/a` values) on the Windows dev
machine, and unit tests for every parser using canned fixture text.

Out of scope: controlling the robot (that is IOT-Testing's and the brain's
job); live I2C probing (`i2cdetect` can disturb devices the running bridge is
talking to); a web UI; auto-launch on login (it is run manually).

## Why standalone (no `milo-bridge` dependency)

The dashboard's job includes diagnosing a *broken* robot. If the bridge venv
is wedged or `milo_bridge` fails to import, the dashboard must still start.
So it depends only on `textual` and `psutil`, and observes the bridge from
the outside: systemd unit state, process stats, journal tail, and file/device
presence. Robot facts it needs (unit name, data paths, expected devices) are
small constants duplicated here deliberately.

## Package layout

```
MILO-Dashboard/
  README.md                    — install, run, keybindings, what each panel shows
  pyproject.toml               — milo-dashboard package; deps: textual>=0.60, psutil>=5.9
  milo_dashboard/
    __init__.py
    __main__.py                — python -m milo_dashboard
    app.py                     — Textual App: layout, refresh timers, keybindings
    widgets.py                 — Panel, BarGauge, KeyValue rows, JournalLog
    collectors/
      __init__.py              — Snapshot dataclasses shared by app and tests
      system.py                — CPU, load, freq, temp, throttle flags, memory, uptime, model, OS
      network.py               — interfaces, Wi-Fi link, gateway, RX/TX rates, reachability
      storage.py               — mounted filesystems, ~/.milo data sizes
      services.py              — systemd units, bridge process stats, journal tail, top processes, hardware presence
  tests/
    fixtures/                  — canned command outputs (iw link, get_throttled, systemctl show, asound cards)
    test_system.py
    test_network.py
    test_storage.py
    test_services.py
```

## Data displayed

**SYSTEM** — CPU total % and per-core bars; load average 1/5/15 (vs 4 cores);
CPU frequency current/max; SoC temperature (`/sys/class/thermal/...` with
`vcgencmd measure_temp` fallback) with color thresholds (green <60 °C, yellow
<70 °C, red ≥70 °C); `vcgencmd get_throttled` decoded into current and past
flags (under-voltage, ARM frequency capped, throttled, soft temp limit); RAM
and swap used/total bars; Pi model (`/proc/device-tree/model`); OS
(`/etc/os-release` PRETTY_NAME) + kernel; uptime; local time.

**NETWORK** — hostname; every non-loopback interface with state (up/down),
IPv4, MAC; for `wlan0`: SSID, signal dBm, TX bitrate (parsed from
`iw dev wlan0 link`); default gateway (`ip route`); live RX/TX rates
(computed from `psutil.net_io_counters` deltas between ticks) plus lifetime
totals; internet reachability shown as OK/– (a non-blocking check that a TCP
connect to 1.1.1.1:53 succeeds, run on the slow cadence).

**STORAGE** — each real mounted filesystem (`/`, `/boot/firmware`; loop/tmpfs
excluded) with used/free bar and percentage colored by fullness; MILO data
row: total size of `~/.milo/`, the knowledge-graph DB file size, and
`~/.milo/policy.onnx` present/size (path constants match the bridge's
conventions).

**SERVICES & ROBOT** — `milo-bridge.service`: ActiveState/SubState, PID,
uptime since start, restart count (`NRestarts`), and the live CPU%/RSS of
that PID via psutil; one-line status for `ssh` and `avahi-daemon`; hardware
presence checks: `/dev/i2c-1`, a camera node (any `/dev/video*`), and the
`googlevoicehat` sound card in `/proc/asound/cards`; top 5 processes by CPU; scrollable tail of the
last 15 `journalctl -u milo-bridge` lines.

## Architecture

**Collectors are pure functions returning frozen dataclasses.** Each
`collectors/*.py` module exposes `collect() -> XxxSnapshot`. Anything that
shells out or reads a file goes through a small seam (`_run(cmd) -> str | None`,
`_read(path) -> str | None`) that returns `None` on any failure —
missing binary, non-Pi platform, permission error. Parsers are separate
top-level functions (`parse_iw_link(text)`, `decode_throttled(hex_str)`,
`parse_systemctl_show(text)`, ...) taking strings and returning dataclasses,
so tests feed them fixture text directly and never need a Pi. Every snapshot
field is `Optional`; the widgets render `None` as a dim `n/a`.

**The app owns two refresh cadences.** A fast timer (2 s) re-collects cheap
psutil-based data (CPU, memory, network rates, process table). A slow timer
(10 s) re-collects subprocess-based data (vcgencmd, iw, systemctl show,
journalctl, reachability). Both run the collection in a worker thread
(`run_worker(..., thread=True)`) so a slow subprocess never freezes the UI,
then post the fresh snapshot back to the widgets. `r` forces both
immediately.

**Widgets are dumb.** `Panel` subclasses receive a snapshot object and
re-render; no widget shells out or reads files. `BarGauge` renders a
labelled block-character bar with the standard green/yellow/red thresholds.

**Layout** — Textual CSS grid; panels keep a minimum width and the grid
reflows to fewer columns on narrow terminals:

```
┌ MILO DASHBOARD ────────── milo · up 2d 4h · 21:14:05 ┐
│ ┌ SYSTEM ──────────┐ ┌ NETWORK ─────────┐ ┌ STORAGE ┐ │
│ │ cpu/temp/mem/... │ │ ifaces/wifi/rates│ │ disks   │ │
│ └──────────────────┘ └──────────────────┘ └─────────┘ │
│ ┌ SERVICES & ROBOT ────────┐ ┌ MILO-BRIDGE LOG ─────┐ │
│ │ units/hardware/top procs │ │ journal tail (scroll)│ │
│ └──────────────────────────┘ └──────────────────────┘ │
│  q Quit   r Refresh                                    │
└────────────────────────────────────────────────────────┘
```

Keybindings: `q` quit, `r` force refresh. Header shows hostname, uptime, and
a live clock.

## Error handling

- Any collector failure yields `None` fields, rendered as `n/a` — the app
  never crashes because a command is missing (Windows dev, or a stripped Pi
  image).
- `journalctl` may need the user in the `systemd-journal` group; if the call
  fails the log panel shows the error hint instead of lines.
- Subprocess calls get a 2 s timeout so a hung `vcgencmd` cannot stall the
  slow tick (the worker thread keeps the UI live regardless).

## Testing

`MILO-Dashboard/tests/` mirrors the repo convention (pytest, off-hardware):
parser unit tests with canned fixtures — `decode_throttled` (0x0, 0x50005,
under-voltage-only), `parse_iw_link` (connected and "Not connected."),
`parse_systemctl_show` (active, failed, unit-not-found), `parse_asound_cards`,
gateway parsing, storage filtering, and rate computation from two counter
snapshots. Widgets stay thin enough that collector tests cover the logic;
no Textual pilot tests required.
