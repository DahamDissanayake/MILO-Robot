# bridge — the Pi-side robot service

`milo-bridge` is the package that runs on Milo's Raspberry Pi Zero 2W as the
`milo-bridge` systemd service. It owns the hardware drivers, the gait engine,
the on-robot knowledge graph, mDNS discovery/pairing with a Milo Brain, and a
full LAN web dashboard at `http://milo.local`.

## What's in here

```
bridge/
  milo_bridge/   the package itself — drivers, gait, graph, net, webapp
                 (see milo_bridge/README.md for the full package layout,
                 install/update steps, and systemd deployment notes)
  assets/faces/  face bitmap PNGs converted from the Sesame firmware
  systemd/       the milo-bridge.service unit file
  tools/         convert_faces.py, servo_sweep.py, webdev.py (off-Pi
                 frontend dev server) — see milo_bridge/README.md
  tests/         off-hardware test suite (fake drivers, no Pi required)
```

## Install

```bash
pip install -e ./common
pip install -e "./bridge[pi]"     # on the robot — installs hardware drivers
pip install -e ./bridge           # off-robot dev — no hardware extras
```

## Full documentation

- [`bridge/milo_bridge/README.md`](milo_bridge/README.md) — package layout,
  pulling updates on the Pi, systemd deployment, the web dashboard, tests,
  and off-Pi frontend development
- [`docs/SOFTWARE-SETUP.md`](../docs/SOFTWARE-SETUP.md) /
  [`docs/GETTING-STARTED.md`](../docs/GETTING-STARTED.md) — first-time setup
  from a blank Pi
- [`docs/WEB-DASHBOARD.md`](../docs/WEB-DASHBOARD.md) — the web dashboard's
  feature tour and how to add a new card

## Tests

```bash
python -m pytest bridge/tests -q
```
