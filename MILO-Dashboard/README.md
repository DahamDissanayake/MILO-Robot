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
