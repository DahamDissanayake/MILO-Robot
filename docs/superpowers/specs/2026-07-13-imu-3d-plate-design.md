# Sensors Panel: 3D Plate Visuals for Pitch/Roll and Gyro

Status: approved
Author: Claude (with Daham Dissanayake)
Date: 2026-07-13

## 1. Summary

Replace the numeric text in the Sensors panel's "Pitch / Roll" and "Gyro"
tiles (`bridge/milo_bridge/webapp/static/js/panels/sensors.js`) with a
CSS-only 3D rectangular plate visual representing the physically
flat-mounted IMU board. The Pitch/Roll plate tilts live to the fused
orientation and nudges sideways with raw accelerometer x/y. The Gyro plate
spins continuously, integrating live angular velocity, and glows when the
rate is high. Both default to flat/still (0) on page load and hold their
last transform across telemetry gaps rather than resetting.

## 2. Motivation

The Pitch/Roll and Gyro tiles currently show plain numbers (`12.3° / -4.1°`,
`0.5°/s`), which don't give an at-a-glance sense of the robot's orientation
or motion the way a physical tilt/spin visual would.

## 3. Non-goals

- No new sensor hardware or telemetry beyond exposing the accelerometer
  x/y/z that the IMU driver already reads internally.
- No 3D library (three.js, etc.) — stays hand-rolled CSS/vanilla JS,
  consistent with the rest of the no-build webapp.
- No change to the complementary filter, control/safety model, or any
  other panel.
- Does not attempt true real-time absolute orientation from the gyro
  plate (integrating angular velocity client-side drifts over time) — it's
  an indicative "is it moving and how fast" visual, not a precision
  instrument. Pitch/Roll (the filtered, drift-corrected values) remains the
  source of truth for absolute orientation.

## 4. Backend changes

`bridge/milo_bridge/drivers/imu.py`:
- `ImuState` gains `accel: tuple[float, float, float]` (g units, x/y/z,
  bias-uncorrected — same values `read_raw()` already returns and that
  `Mpu6050.update()` currently discards after feeding them into the
  complementary filter).
- `Mpu6050.update()` returns `ImuState(roll=roll, pitch=pitch, gyro=gyro,
  accel=accel)`.

`bridge/milo_bridge/webapp/telemetry.py`:
- `collect_telemetry()`'s `imu` dict gains `"accel": list(state.accel)`.

Test fallout (all in `bridge/tests/`):
- `test_imu.py::test_update_returns_state` — assert the new `state.accel`
  value (flat/still fixture block → `(0.0, 0.0, 1.0)`).
- `webapp/fakes.py::FakeImu.update()` — add a realistic `accel` tuple, e.g.
  `(0.01, -0.02, 0.98)`.
- `webapp/test_status.py::test_status_reports_real_imu_state_as_json_serializable_dict`
  — update the expected `data["imu"]` dict to include `"accel"`.

## 5. Frontend changes

`bridge/milo_bridge/webapp/static/js/panels/sensors.js`:

- Both tiles' `<div class="value">` are replaced with a plate widget:
  a `.imu-plate-wrap` (perspective container) around `.imu-plate`
  (`transform-style: preserve-3d`) built from one `.imu-face.top` and four
  thin `.imu-face` side faces, plus a small edge marker denoting "front" so
  tilt direction is legible. Markup is identical for both tiles; only the
  driving state differs.
- Pitch/Roll plate (`#plate-attitude`):
  - Module-scope state `attitude = { pitch: 0, roll: 0, ax: 0, ay: 0 }`,
    initialized once at mount.
  - On each `telemetry` message where `m.imu` is present, update all four
    fields from `m.imu.pitch`, `m.imu.roll`, `m.imu.accel?.[0]`,
    `m.imu.accel?.[1]`, then write CSS custom properties
    (`--pitch`, `--roll`, `--ax`, `--ay`) onto the plate element. The CSS
    transform is `rotateX(pitch) rotateY(roll) translateX(ax) translateY(ay)`
    (scaled/clamped in CSS via `calc()`).
  - If `m.imu` is `null` (sensor read failed that tick), skip the update —
    the plate keeps its last transform instead of snapping flat.
- Gyro plate (`#plate-gyro`):
  - Module-scope state `gyroRate = { x: 0, y: 0, z: 0 }` (last telemetry
    sample, held across gaps the same way as attitude) and
    `gyroAngle = { x: 0, y: 0, z: 0 }` (accumulated visual rotation,
    starts at 0, never reset except at mount).
  - A `requestAnimationFrame` loop, started at mount and cancelled in the
    panel's cleanup function, integrates
    `gyroAngle[k] += gyroRate[k] * dtSeconds` every frame and writes the
    result to CSS custom properties driving the plate's transform. When
    `gyroRate` is all-zero the plate simply stops changing — no special
    pause logic needed.
  - Magnitude `hypot(gyroRate.x, gyroRate.y, gyroRate.z)` past a threshold
    (mirroring `comm.js`'s `HOT_THRESHOLD` pattern) toggles a `.hot` class
    that swaps the plate's border/glow color from `--ok` to `--danger`.
- Details section: drop the "Attitude — pitch / roll" sparkline canvas and
  its `attitudeHist` bookkeeping (now redundant with the live plate); keep
  the CPU/RAM/Temp sparkline canvas and its `systemHist` bookkeeping as-is.

`bridge/milo_bridge/webapp/static/css/console.css`:
- New rules for `.imu-plate-wrap`, `.imu-plate`, `.imu-face` (and its
  `.top/.front/.back/.left/.right` face variants) building a thin
  rectangular prism via `perspective` + per-face `rotateX/translateZ`,
  sized to fit the existing `.sensor-tile` footprint, using existing theme
  tokens (`--surface`, `--line`, `--ok`, `--danger`) so it matches
  light/dark theming automatically. `.imu-plate.hot` variant for the gyro
  glow.

## 6. Persistence / "0 at start, held until power off"

There is no way for the browser to observe the robot's actual power state.
A page load is the practical proxy for "start": all plate state
initializes to 0/flat in module-scope JS variables at `mount()`. From then
on, values only change in response to new telemetry (or, for the gyro
plate, continuous integration of the last-known rate) — a momentary
`m.imu === null` (dropped WS message, transient sensor read failure) holds
the last transform rather than resetting to flat, so brief hiccups don't
visually jump the plate.

## 7. Testing

No JS test runner exists in this repo — the webapp's static JS is
untested by any framework; only Python backend tests cover it (e.g.
`test_static_integrity.py` checks referenced files exist). Verification
plan:
- Update/extend the Python IMU + telemetry tests listed in §4.
- Run the full bridge suite (`python -m pytest bridge/tests`).
- Manually exercise the Sensors panel in a browser (via the `run`/`verify`
  skill) to confirm both plates render, tilt/spin in response to
  telemetry, hold state through a simulated gap, and restore to flat only
  on reload.
