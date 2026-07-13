# Sensors Panel: Unified, Calibrated, Live IMU Plate

Status: approved
Author: Claude (with Daham Dissanayake)
Date: 2026-07-13

Supersedes the "Pitch/Roll" + "Gyro" two-tile design from
`2026-07-13-imu-3d-plate-design.md` (PR #12, merged). This spec corrects
three gaps that surfaced after shipping that version: (1) gyro bias
calibration was implemented but never wired into the real robot's startup,
(2) the visual was split across two separately-labeled tiles instead of one
true attitude indicator, and (3) the 2-second general telemetry cadence and
client-side-only yaw integration meant the "live" motion was partly a
client-side illusion rather than the sensor's real, persistent state.

## 1. Summary

Replace the two IMU tiles ("Pitch / Roll", "Gyro") with a single tile
labeled **IMU**, driven by one 3D plate that reflects the MPU6050's full
6-axis reading (accel x/y/z + gyro x/y/z — this chip has no magnetometer).
Wire up gyro calibration at bridge startup. Move yaw integration
server-side so it persists for the life of the bridge process, not a
browser tab. Add a dedicated ~10 Hz WebSocket channel for IMU data so the
plate visibly tracks real motion instead of extrapolating between slow
2-second ticks.

## 2. Motivation

- `Mpu6050.calibrate_gyro()` exists and is unit-tested but nothing in
  `bridge/milo_bridge/main.py` calls it — on real hardware the gyro bias is
  never corrected.
- Two separately-labeled tiles for what is one physical sensor reads as
  two unrelated numbers rather than one coherent orientation.
- The previous design's Gyro plate integrated angular velocity into a
  spin **entirely in browser-tab JS state**, which both (a) resets to 0 on
  every page reload — quietly violating the original "0 at start, held
  until power off" requirement — and (b) diverges between two open tabs,
  since each tab does its own independent integration of the same raw
  rate.
- The general telemetry WebSocket broadcast is intentionally slow
  (`TELEMETRY_S = 2.0`, fine for CPU/RAM/temp) — that cadence cannot look
  "live" for an orientation indicator no matter how it's animated
  client-side.

## 3. Non-goals

- No magnetometer, no true absolute compass heading. Pitch/roll stay
  accurate and absolute (gravity-referenced via the existing
  accel+gyro complementary filter). Yaw is, and will remain, a
  relative, drift-prone estimate from integrating gyro-z only — this is a
  hardware limitation of the MPU6050, not a software gap. The UI does not
  claim otherwise.
- No change to the complementary filter's roll/pitch math (already
  correct and tested) — only its yaw counterpart (new) and its calibration
  wiring (new) change.
- No change to the general telemetry loop's existing fields (CPU/RAM/temp
  stay on the 2-second cadence, unaffected) — a new, separate channel is
  added alongside it, not a replacement.
- No full test harness for `bridge/milo_bridge/main.py`'s async
  composition root (constructing every hardware driver, running forever).
  None exists today; adding one is a much larger, separate effort than
  wiring in one already-tested calibration call at a well-understood
  point in the existing startup sequence.

## 4. Backend changes

### 4.1 Gyro calibration at startup

`bridge/milo_bridge/main.py`, immediately after line 55
(`imu = _optional(Mpu6050.from_hardware, "IMU")`), before any servo motion
(`runner.run("rest")`) or task creation:

```python
if imu is not None:
    log.info("calibrating IMU gyro bias — keep the robot still")
    await asyncio.to_thread(imu.calibrate_gyro)
    log.info("IMU gyro calibration complete")
```

`asyncio.to_thread` keeps the ~2s blocking calibration loop (200 samples at
100Hz through blocking I2C calls) off the event loop, matching how the
IOT-Testing TUI already calls this same method
(`IOT-Testing/iot_tester/screens/imu.py:56`).

### 4.2 Server-side yaw accumulation

`bridge/milo_bridge/drivers/imu.py`:
- `ImuState` gains `yaw: float` (degrees, cumulative since calibration —
  relative, not absolute; unbounded, not wrapped to ±180/360, to avoid a
  visual snap-back artifact in the frontend on wraparound).
- `Mpu6050.__init__` adds `self._yaw = 0.0`.
- `Mpu6050.update()` adds `self._yaw += gyro[2] * dt` (after existing
  bias-corrected gyro is read) and passes `yaw=self._yaw` into the
  returned `ImuState`.

This makes yaw persist for the lifetime of the `Mpu6050` instance — shared
by `GaitEngine` and the web telemetry/IMU-broadcast paths (confirmed one
instance, not per-client copies) — so a browser reconnect or a second tab
sees the robot's actual current accumulated yaw, not a reset-to-0 value.
It only resets when the bridge process restarts, which is the closest
software proxy to "power off" there is.

### 4.3 Dedicated fast IMU broadcast channel

`bridge/milo_bridge/webapp/telemetry.py`:
- Factor the imu-dict construction out of `collect_telemetry` into a
  reusable `imu_snapshot(deps) -> dict | None`:
  ```python
  def imu_snapshot(deps) -> dict | None:
      if deps.imu is None:
          return None
      try:
          state = deps.imu.update()
          return {
              "pitch": state.pitch, "roll": state.roll, "yaw": state.yaw,
              "gyro": list(state.gyro), "accel": list(state.accel),
          }
      except Exception:
          return None
  ```
- `collect_telemetry` calls `imu["imu"] = imu_snapshot(deps)` in place of
  its current inline block — same shape as before plus the new `yaw`
  field. `/api/status` and the existing 2-second broadcast are otherwise
  unaffected.

`bridge/milo_bridge/webapp/ws.py`:
- New constant `IMU_S = 0.1` (10 Hz) alongside the existing
  `TELEMETRY_S = 2.0`.
- New `_imu_loop(app)`, mirroring `_telemetry_loop`: every `IMU_S`
  seconds, if `app["deps"].imu is not None` and there are connected
  clients, `broadcast_json(app, {"t": "imu", **imu_snapshot(deps)})` (only
  when `imu_snapshot` returns non-`None`, i.e. the sensor read succeeded).
  Registered in `_on_startup` alongside the existing loops.
- The general `_telemetry_loop`'s payload keeps its own `imu` field too
  (via the same `imu_snapshot` helper) — cheap, and `/api/status` still
  wants a one-shot snapshot independent of the live channel.

## 5. Frontend changes

`bridge/milo_bridge/webapp/static/js/panels/sensors.js`:
- The two `.imu-plate-wrap` blocks (`#plate-attitude`, `#plate-gyro`)
  collapse into one, labeled **IMU**, `id="plate-imu"`, promoted to a
  full-width tile (`grid-column: 1 / -1`) at the top of `.sensor-tiles`
  since it now represents the panel's primary reading.
- All client-side physics is deleted: no more `requestAnimationFrame`
  spin loop, no more local `gyroAngle` integration. The panel subscribes
  to the new `bus.on("imu", ...)` topic and, on every message, sets
  `--pitch`, `--roll`, `--yaw`, `--ax`, `--ay` directly from the payload
  (`m.pitch`, `m.roll`, `m.yaw`, `m.accel[0]`, `m.accel[1]`) — the server
  is now the sole source of truth for all five values. `.hot` is toggled
  from `Math.hypot(...m.gyro) >= GYRO_HOT_DPS` on the same message.
- The existing `bus.on("telemetry", ...)` subscription keeps handling
  SoC Temp/CPU/RAM/Hardware/the System sparkline exactly as before —
  unaffected by this change other than no longer reading `m.imu` itself
  (that data still exists on the `telemetry` message but the panel now
  gets its live IMU data from the faster `imu` topic instead).

`bridge/milo_bridge/webapp/static/css/console.css`:
- `#plate-attitude`'s dedicated transition rule is replaced with a
  generic `.imu-plate { transition: transform 0.15s linear; }` (now there
  is only ever one plate, and at a 10 Hz update rate a short transition
  smooths the visible steps between ticks instead of a hard jump).
- No other structural change to the box-face rules (`.imu-face.*`) — same
  thin rectangular prism, same face-color language, same `.hot` glow.

## 6. Persistence semantics (corrected)

- **Pitch/roll**: always accurate and absolute (gravity fusion), held in
  the long-lived `ComplementaryFilter` inside the `Mpu6050` instance.
- **Yaw**: relative to wherever it was when the bridge process last
  started, accumulated in that same long-lived instance. Genuinely "0 at
  start, held until power off" now — power-off of the physical robot
  necessarily terminates the bridge process, which is the only event that
  resets it.
- **Accel nudge**: always the current instantaneous reading — nothing to
  persist.
- A browser tab reconnecting, or a second tab opening, immediately shows
  the server's current true state on its next `imu` message (within
  ~100ms), not a reset-to-flat default.

## 7. Testing

- `bridge/tests/test_imu.py`: update `test_update_returns_state`'s
  `ImuState` construction/assertions for the new `yaw` field; add a new
  test asserting yaw accumulates across repeated `update()` calls with a
  constant nonzero gyro-z fixture.
- `bridge/tests/webapp/fakes.py`: `FakeImu.update()` gains a fixed `yaw`
  value.
- `bridge/tests/webapp/test_status.py`: update the expected `imu` dict to
  include `"yaw"`.
- `bridge/tests/webapp/test_ws.py`: add `test_imu_pushed`, mirroring the
  existing `test_telemetry_pushed` pattern but waiting for `t == "imu"`
  (should resolve fast, within `IMU_S`, unlike the 2-second telemetry
  wait).
- No new test harness for `main.py` (see §3 non-goals) — the calibration
  call is a one-line, already-tested method invocation at a documented
  point in an existing, working startup sequence; verified by code review
  and the full existing suite continuing to pass.
- Manual verification: re-run the same Playwright-driven check against
  `bridge/tools/webdev.py` used for the previous iteration, updated for
  the single `#plate-imu` element and the new `imu` WS topic — confirm
  the plate updates roughly every 100ms (not 2s), the fake driver's fixed
  yaw doesn't change (`FakeImu` returns a constant, unlike real hardware,
  so no accumulation is visible in dev mode — expected), and the hot glow
  still triggers correctly under a sustained high synthetic gyro rate.
