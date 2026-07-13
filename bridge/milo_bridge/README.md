# milo_bridge — the Pi-side robot service

`milo_bridge` is the process that runs on Milo's Raspberry Pi as the
`milo-bridge` systemd service: it owns the hardware drivers, the gait
engine, the on-robot knowledge graph, discovery/pairing with a Milo Brain,
and — as of 2026-07-13 — a full LAN web dashboard at `http://milo.local`.
This file covers pulling updates and (re)installing this package on the
robot. For first-time setup from a blank Pi, see
[`docs/SOFTWARE-SETUP.md`](../../docs/SOFTWARE-SETUP.md) and
[`docs/GETTING-STARTED.md`](../../docs/GETTING-STARTED.md) at the repo
root. For the dashboard's own feature tour, see
[`docs/WEB-DASHBOARD.md`](../../docs/WEB-DASHBOARD.md).

## Pulling updates on the Pi

Because the bridge is installed editable (`pip install -e`), a plain
`git pull` is enough for pure code changes — no reinstall needed. Reinstall
only when `bridge/pyproject.toml`'s dependencies change (as they did for
the web dashboard, which added `aiohttp`).

```bash
cd ~/MILO-Robot
git pull
source ~/.venvs/milo/bin/activate
pip install -e ./common -e "./bridge[pi]"
```

### Optional: text-to-speech for the dashboard's Voice card

The web dashboard's **Say** button (text-to-speech) shells out to
`espeak-ng`. Skip this if you don't need it — the dashboard degrades
gracefully and the Voice card just reports `tts-unavailable`.

```bash
sudo apt update
sudo apt install -y espeak-ng
```

### Symlink the systemd unit (do this once, first deploy)

`/etc/systemd/system/milo-bridge.service` should be a **symlink** into the
repo, not a copy. A copy silently goes stale on every `git pull` that
touches `bridge/systemd/milo-bridge.service` — this is exactly what caused
a `status=217/USER` crash-loop once (the repo's `User=`/`ExecStart=` paths
were fixed and pulled, but the deployed copy under `/etc/systemd/system/`
never updated, since nothing re-copies it automatically). A symlink makes
that class of bug impossible: there's only one file, so a `git pull` and a
`daemon-reload` are always enough.

Set it up once:

```bash
sudo rm -f /etc/systemd/system/milo-bridge.service
sudo ln -s ~/MILO-Robot/bridge/systemd/milo-bridge.service /etc/systemd/system/milo-bridge.service
sudo systemctl daemon-reload
```

Verify it's really a symlink (not a copy): `ls -la /etc/systemd/system/milo-bridge.service`
should show `-> /home/<you>/MILO-Robot/bridge/systemd/milo-bridge.service`.

From then on, whenever the unit file changes upstream, this is enough —
no `cp` step, ever:

```bash
sudo systemctl daemon-reload
sudo systemctl restart milo-bridge
```

(`daemon-reload` is still required after any unit-file change — systemd
caches the parsed file — but with the symlink in place there's nothing
left to forget.)

### One-time only: set the `milo.local` hostname

Only needed once per Pi, and only if you haven't already set this up:

```bash
sudo raspi-config nonint do_hostname milo
systemctl is-active avahi-daemon   # should print "active" — ships by default on Pi OS
sudo reboot
```

### Quick reference — the whole update in one block

```bash
cd ~/MILO-Robot && git pull
source ~/.venvs/milo/bin/activate
pip install -e ./common -e "./bridge[pi]"
sudo apt install -y espeak-ng   # optional, for TTS
sudo cp bridge/systemd/milo-bridge.service /etc/systemd/system/milo-bridge.service
sudo systemctl daemon-reload
sudo systemctl restart milo-bridge
sudo systemctl status milo-bridge
```

## Verifying it's running

```bash
sudo systemctl status milo-bridge     # active (running), no crash loop
sudo journalctl -u milo-bridge -n 30  # look for "web dashboard on http://0.0.0.0:80 (milo.local)"
```

Then from any device on the same Wi-Fi, open `http://milo.local` (falls
back automatically to `http://milo.local:8080` if port 80 is ever
unavailable, and to the robot's raw LAN IP if mDNS doesn't resolve).

## Package layout

```
milo_bridge/
  main.py           composition root: builds drivers, gait engine, graph,
                     sleep controller, boots to rest, starts the session
                     manager and the web dashboard
  config.py         BridgeConfig, loaded from ~/.milo/config.json
  cli.py            robot CLI (manual pose/servo/graph commands, service stopped)
  poses.py          scripted poses ported from the Sesame firmware
  sleep.py          idle/sleep controller, loud-sound perk-up
  drivers/          servos, display, camera, audio, IMU — one file per sensor
  gait/             CPG trot fallback + ONNX-policy gait engine
  graph/            on-robot SQLite knowledge graph (store + wire API)
  net/              discovery, PIN-pairing/HMAC session, brain media streams
  webapp/           the LAN web dashboard (aiohttp app) — see below
```

## The web dashboard (`webapp/`)

`webapp/` is an `aiohttp` application embedded in this same process (robot
hardware is process-exclusive, so the dashboard has to live here rather
than as a separate service). It serves `http://milo.local`: live camera,
live mic audio, push-to-talk + text-to-speech through the robot's speaker,
joystick/pose/servo control, a searchable view of the knowledge graph, live
telemetry, and a log tail — all as a no-build vanilla-JS drag-and-resize
card dashboard.

```
webapp/
  __init__.py       aiohttp app factory (create_app), JSON-error middleware
  deps.py           WebDeps — the dependency bundle every handler receives
  server.py         start_web(): binds port 80, falls back to 8080
  control.py        ControlBroker — exclusive motion control (web vs. brain)
  media_hub.py      MediaHub/Fanout — single-reader camera/mic fanout to N browsers
  motion.py         MotionService — clamped gait/pose/face/servo + STOP
  telemetry.py      collect_telemetry() — the periodic status snapshot
  logbuf.py         RingBufferLogHandler — feeds the live log card
  ws.py             /ws — WebSocket JSON dispatch, heartbeat, binary audio
  api/              REST endpoints: status, media (MJPEG), speak (TTS),
                     graph (search + passthrough), motion_meta (poses/faces), logs
  static/           the dashboard frontend — see docs/WEB-DASHBOARD.md §5
                     for how to add a new card
```

Full feature tour, the control/safety model, and instructions for adding a
new card or API route live in
[`docs/WEB-DASHBOARD.md`](../../docs/WEB-DASHBOARD.md) — read that before
touching `webapp/`.

## Tests

```bash
python -m pytest bridge/tests -q
```

Webapp tests live under `bridge/tests/webapp/` and run entirely off
hardware against fake drivers (`bridge/tests/webapp/fakes.py`), so they
pass on a dev machine with no Pi attached.

## Off-Pi frontend development

```bash
python bridge/tools/webdev.py
```

Starts the real dashboard wired to fake drivers instead of real hardware —
open `http://localhost:8080` to iterate on the frontend without a robot.
See `docs/WEB-DASHBOARD.md` §7 for what does and doesn't work off-Pi.
