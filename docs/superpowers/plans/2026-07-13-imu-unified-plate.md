# Unified, Calibrated, Live IMU Plate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two "Pitch / Roll" + "Gyro" tiles with one calibrated, live-updating "IMU" tile; wire up gyro calibration on the real robot; make yaw persist server-side; add a fast dedicated WebSocket channel so the plate visibly tracks real motion instead of a slow 2-second cadence.

**Architecture:** `Mpu6050` gains server-side yaw accumulation (gyro-z integration, persists for the process lifetime) and gets its existing-but-unused `calibrate_gyro()` wired into `main.py`'s startup sequence. `telemetry.py` factors its IMU-dict construction into a reusable `imu_snapshot()`, reused by both the existing 2s general telemetry loop and a new ~10Hz `_imu_loop` in `ws.py` that broadcasts a dedicated `{"t": "imu", ...}` message. The frontend drops all client-side integration/animation-frame physics and just renders whatever the server last sent.

**Tech Stack:** Python (aiohttp backend, pytest), vanilla ES modules + hand-written CSS (frontend, no framework/build).

## Global Constraints

- The MPU6050 has no magnetometer: pitch/roll stay accurate/absolute (existing complementary filter, unchanged); yaw is and remains a relative, drift-prone gyro-integration estimate. Don't imply otherwise anywhere (UI copy, comments, tests).
- Yaw accumulates unbounded (not wrapped to ±180/360) to avoid a CSS transition snapping the plate the "long way around" on wraparound.
- No change to the general telemetry loop's existing cadence/fields (`TELEMETRY_S = 2.0` stays as-is for CPU/RAM/temp) — the new IMU channel is additive.
- No new test harness for `bridge/milo_bridge/main.py`'s composition root — the calibration call is verified by code review + the existing suite, per the spec's non-goals.
- Commit after each task, no `Co-Authored-By` trailer, push at the end and open a draft PR (per repo convention).
- This repo currently requires `PYTHONPATH` pointed at this worktree's `bridge/` directory when running pytest, because the global editable install of `milo-bridge` resolves to a different worktree. Every test command below assumes:
  ```bash
  export PYTHONPATH="D:/Github/MILO-Robot/.claude/worktrees/sensors-imu-3d-plate/bridge"
  ```

---

### Task 1: Server-side yaw accumulation on `Mpu6050`

**Files:**
- Modify: `bridge/milo_bridge/drivers/imu.py` (`ImuState` dataclass, `Mpu6050.__init__`, `Mpu6050.calibrate_gyro`, `Mpu6050.update`)
- Test: `bridge/tests/test_imu.py`

**Interfaces:**
- Produces: `ImuState.yaw: float` (degrees, cumulative since last calibration/process start, unbounded). Consumed by Task 3 (`telemetry.py`) and Task 6 (frontend).

- [ ] **Step 1: Write the failing tests**

Update `test_update_returns_state` in `bridge/tests/test_imu.py` (add one assertion):

```python
def test_update_returns_state():
    times = iter([0.0, 0.01, 0.02])
    bus = FakeBus([block()] * 5)
    imu = Mpu6050(bus, clock=lambda: next(times))
    state = imu.update()
    assert isinstance(state, ImuState)
    assert state.gyro == (0.0, 0.0, 0.0)
    assert math.isclose(state.roll, 0.0)
    assert state.accel == (0.0, 0.0, 1.0)
    assert math.isclose(state.yaw, 0.0)
```

Add a new test, anywhere after `test_update_returns_state`:

```python
def test_yaw_accumulates_from_gyro_z():
    times = iter([0.0, 0.5])
    bus = FakeBus([block(gz=131)] * 5)  # 131 raw -> 1.0 deg/s (GYRO_LSB_PER_DPS)
    imu = Mpu6050(bus, clock=lambda: next(times))
    imu.update()          # first call: dt defaults to 0.01 -> yaw = 0.01
    state = imu.update()  # second call: dt = 0.5 - 0.0 = 0.5 -> yaw = 0.01 + 0.5 = 0.51
    assert math.isclose(state.yaw, 0.51)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/test_imu.py -v`
Expected: `test_update_returns_state` and `test_yaw_accumulates_from_gyro_z` both FAIL — `TypeError: ImuState.__init__() missing 1 required positional argument: 'yaw'` (or `AttributeError` once the dataclass part below is done but before `update()` sets it — either way, both new assertions currently have nothing to reference).

- [ ] **Step 3: Write minimal implementation**

In `bridge/milo_bridge/drivers/imu.py`, update the dataclass:

```python
@dataclass(frozen=True)
class ImuState:
    roll: float          # degrees
    pitch: float         # degrees
    yaw: float            # degrees, cumulative since calibration (relative — no magnetometer)
    gyro: tuple[float, float, float]  # deg/s (x, y, z)
    accel: tuple[float, float, float]  # g (x, y, z)
```

Add yaw state in `__init__` (next to the existing `self._gyro_bias` line):

```python
        self._filter = ComplementaryFilter()
        self._gyro_bias = (0.0, 0.0, 0.0)
        self._yaw = 0.0
        self._last_t: float | None = None
```

Reset yaw's zero-reference alongside the gyro bias in `calibrate_gyro`:

```python
    def calibrate_gyro(self, samples: int = 200) -> None:
        """Average gyro at rest to find bias. Robot must be still (~2 s at 100 Hz)."""
        self._gyro_bias = (0.0, 0.0, 0.0)
        self._yaw = 0.0
        total = [0.0, 0.0, 0.0]
        for _ in range(samples):
            _, gyro = self.read_raw()
            for i in range(3):
                total[i] += gyro[i]
        self._gyro_bias = tuple(t / samples for t in total)  # type: ignore[assignment]
```

Accumulate yaw and include it in the returned state in `update`:

```python
    def update(self) -> ImuState:
        now = self._clock()
        dt = (now - self._last_t) if self._last_t is not None else 0.01
        self._last_t = now
        accel, gyro = self.read_raw()
        roll, pitch = self._filter.update(accel, gyro, dt)
        self._yaw += gyro[2] * dt
        return ImuState(roll=roll, pitch=pitch, yaw=self._yaw, gyro=gyro, accel=accel)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/test_imu.py -v`
Expected: all PASS (this will also break collection of anything else constructing `ImuState` without `yaw` — the only other site is `bridge/tests/webapp/fakes.py`, handled in Task 3).

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/drivers/imu.py bridge/tests/test_imu.py
git commit -m "feat(bridge): accumulate yaw server-side on Mpu6050"
```

---

### Task 2: Wire gyro calibration into bridge startup

**Files:**
- Modify: `bridge/milo_bridge/main.py:54-56`

**Interfaces:**
- Consumes: `Mpu6050.calibrate_gyro()` (existing, tested method — unchanged interface other than the new yaw reset from Task 1).

There is no test harness for `main.py` (see plan's Global Constraints / spec's non-goals) — this task is verified by code review and by the full suite continuing to pass, not a new automated test.

- [ ] **Step 1: Add the calibration call**

In `bridge/milo_bridge/main.py`, change:

```python
    # Optional hardware/components.
    imu = _optional(Mpu6050.from_hardware, "IMU")
    camera = _optional(lambda: CameraStreamer.from_hardware(fps=cfg.video_fps), "camera")
    audio = _optional(AudioIO, "audio")
```

to:

```python
    # Optional hardware/components.
    imu = _optional(Mpu6050.from_hardware, "IMU")
    if imu is not None:
        log.info("calibrating IMU gyro bias — keep the robot still")
        await asyncio.to_thread(imu.calibrate_gyro)
        log.info("IMU gyro calibration complete")
    camera = _optional(lambda: CameraStreamer.from_hardware(fps=cfg.video_fps), "camera")
    audio = _optional(AudioIO, "audio")
```

This runs before `runner.run("rest")` (first servo motion, line ~93) and before the gait/web tasks are created, so the robot is naturally still. `asyncio.to_thread` is already imported via the top-level `import asyncio`.

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `python -m pytest bridge/tests -q`
Expected: all PASS (this file has no direct test coverage, so this is a regression check on everything else, not a check on this specific change).

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/main.py
git commit -m "feat(bridge): calibrate IMU gyro bias at startup"
```

---

### Task 3: Factor `imu_snapshot()`, add `yaw` to its payload

**Files:**
- Modify: `bridge/milo_bridge/webapp/telemetry.py:55-73`
- Modify: `bridge/tests/webapp/fakes.py:93-94` (`FakeImu.update`)
- Test: `bridge/tests/webapp/test_status.py:24-40`

**Interfaces:**
- Consumes: `ImuState.yaw` (Task 1).
- Produces: `imu_snapshot(deps) -> dict | None` — `{"pitch", "roll", "yaw", "gyro", "accel"}` or `None`. Consumed by `collect_telemetry` (this task) and Task 4's new `_imu_loop`.

- [ ] **Step 1: Write the failing test**

Update `FakeImu.update()` in `bridge/tests/webapp/fakes.py`:

```python
    def update(self) -> ImuState:
        return ImuState(pitch=1.0, roll=-2.0, yaw=15.0, gyro=(0.1, 0.2, 0.5), accel=(0.01, -0.02, 0.98))
```

Update the expected dict in `bridge/tests/webapp/test_status.py`'s
`test_status_reports_real_imu_state_as_json_serializable_dict`:

```python
        assert data["imu"] == {
            "pitch": 1.0, "roll": -2.0, "yaw": 15.0,
            "gyro": [0.1, 0.2, 0.5], "accel": [0.01, -0.02, 0.98],
        }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest bridge/tests/webapp/test_status.py::test_status_reports_real_imu_state_as_json_serializable_dict -v`
Expected: FAIL — `TypeError` from `FakeImu.update()`'s `ImuState(...)` call missing `yaw` until the fixture edit lands, then an `AssertionError` (missing `"yaw"` key) until `telemetry.py` is updated.

- [ ] **Step 3: Write minimal implementation**

In `bridge/milo_bridge/webapp/telemetry.py`, replace the inline imu-dict block inside
`collect_telemetry` with a standalone helper:

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


def collect_telemetry(deps) -> dict:
    return {
        "t": "telemetry",
        "cpu_percent": _cpu_percent(),
        "temp_c": _cpu_temp_c(),
        "mem_percent": _mem_percent(),
        "uptime_s": round(time.monotonic() - _START, 1),
        "link": deps.get_link_state(),
        "owner": deps.broker.owner if deps.broker else "none",
        "gait_backend": getattr(deps.gait, "backend", None),
        "imu": imu_snapshot(deps),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_status.py bridge/tests/test_imu.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/telemetry.py bridge/tests/webapp/fakes.py bridge/tests/webapp/test_status.py
git commit -m "refactor(bridge): factor imu_snapshot() out of collect_telemetry, add yaw"
```

---

### Task 4: Dedicated ~10Hz IMU WebSocket channel

**Files:**
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Consumes: `imu_snapshot(deps)` (Task 3), `broadcast_json(app, payload)` (existing, `ws.py:30-33`).
- Produces: a `{"t": "imu", "pitch", "roll", "yaw", "gyro", "accel"}` WS message broadcast every `IMU_S` seconds to all connected clients whenever `deps.imu is not None`. Consumed by Task 6 (frontend).

- [ ] **Step 1: Write the failing test**

Add to `bridge/tests/webapp/test_ws.py` (near `test_telemetry_pushed`):

```python
async def test_imu_pushed():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        data = await _recv_json_until(ws, "imu", tries=30, timeout=1.0)
        assert data["pitch"] == 1.0
        assert data["roll"] == -2.0
        assert data["yaw"] == 15.0
        assert data["gyro"] == [0.1, 0.2, 0.5]
        assert data["accel"] == [0.01, -0.02, 0.98]
    finally:
        await client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest bridge/tests/webapp/test_ws.py::test_imu_pushed -v`
Expected: FAIL — `AssertionError: no 'imu' message` (no such broadcast exists yet).

- [ ] **Step 3: Write minimal implementation**

In `bridge/milo_bridge/webapp/ws.py`:

Update the import (line 13) to also bring in the new helper:

```python
from .telemetry import collect_telemetry, imu_snapshot
```

Add the new interval constant next to the existing one (line 17):

```python
TELEMETRY_S = 2.0
IMU_S = 0.1
```

Add a new loop function next to `_telemetry_loop`:

```python
async def _imu_loop(app: web.Application) -> None:
    while True:
        await asyncio.sleep(IMU_S)
        deps = app["deps"]
        if deps.imu is not None and app["ws_clients"]:
            snap = imu_snapshot(deps)
            if snap is not None:
                broadcast_json(app, {"t": "imu", **snap})
```

Register it in `_on_startup`:

```python
async def _on_startup(app: web.Application) -> None:
    app["motion"].start()
    app["bg_tasks"] = [
        asyncio.ensure_future(_telemetry_loop(app)),
        asyncio.ensure_future(_imu_loop(app)),
        asyncio.ensure_future(_expiry_loop(app)),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_ws.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/test_ws.py
git commit -m "feat(webapp): broadcast a dedicated ~10Hz IMU WebSocket channel"
```

---

### Task 5: Run the full backend suite before touching the frontend

**Files:** none (verification checkpoint)

- [ ] **Step 1: Run the full bridge test suite**

Run: `python -m pytest bridge/tests -q`
Expected: all tests PASS.

No commit — this is a checkpoint, not a change.

---

### Task 6: Merge the two plates into one full-width "IMU" tile (CSS)

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/css/console.css` (the "sensors panel" and "IMU 3D plate" blocks added by the previous PR)

**Interfaces:**
- Produces: `.imu-tile` (full-width tile modifier), a single `.imu-plate` with its own `transition` (no more `#plate-attitude`-specific rule). Consumed by Task 7's markup.

- [ ] **Step 1: Add the full-width tile modifier**

In `bridge/milo_bridge/webapp/static/css/console.css`, next to the existing `.hw-tile` rule:

```css
.hw-tile { grid-column: 1 / -1; }
.imu-tile { grid-column: 1 / -1; }
```

- [ ] **Step 2: Replace the plate rules**

Replace this block (added by the previous PR):

```css
/* IMU 3D plate — a thin rectangular prism representing the flat-mounted
   IMU board. Pitch/Roll tiles set --pitch/--roll/--ax/--ay from live
   telemetry; the Gyro tile integrates raw angular velocity into
   --pitch/--roll/--yaw itself (see sensors.js). All default to 0 (flat,
   centered) until the first update. */
.imu-plate-wrap {
  height: 64px; margin-top: 4px; display: flex; align-items: center; justify-content: center;
  perspective: 220px;
}
.imu-plate {
  position: relative; width: 60px; height: 36px; transform-style: preserve-3d;
  transform:
    translateX(calc(var(--ax, 0) * 60px))
    translateY(calc(var(--ay, 0) * -60px))
    rotateX(-55deg)
    rotateX(calc(var(--pitch, 0) * 1deg))
    rotateY(calc(var(--roll, 0) * -1deg))
    rotateZ(calc(var(--yaw, 0) * 1deg));
}
#plate-attitude { transition: transform 0.12s linear; }
```

with:

```css
/* IMU 3D plate — a thin rectangular prism representing the flat-mounted
   IMU board. sensors.js sets --pitch/--roll/--ax/--ay/--yaw directly from
   the server's live "imu" WS messages (~10Hz) — pitch/roll are the fused,
   calibrated, absolute tilt; yaw is a relative gyro-integrated estimate
   (no magnetometer on this sensor); ax/ay nudge the plate from raw
   accelerometer x/y. All default to 0 (flat, centered) until the first
   message arrives. */
.imu-plate-wrap {
  height: 64px; margin-top: 4px; display: flex; align-items: center; justify-content: center;
  perspective: 220px;
}
.imu-plate {
  position: relative; width: 60px; height: 36px; transform-style: preserve-3d;
  transition: transform 0.15s linear;
  transform:
    translateX(calc(var(--ax, 0) * 60px))
    translateY(calc(var(--ay, 0) * -60px))
    rotateX(-55deg)
    rotateX(calc(var(--pitch, 0) * 1deg))
    rotateY(calc(var(--roll, 0) * -1deg))
    rotateZ(calc(var(--yaw, 0) * 1deg));
}
```

(The `.imu-face*` and `.imu-plate.hot*` rules below this block are unchanged — leave them as-is.)

- [ ] **Step 2: Verify no other file references broke**

Run: `python -m pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/css/console.css
git commit -m "feat(webapp): merge IMU plate CSS into one full-width tile"
```

---

### Task 7: Rewrite `sensors.js` around the single live IMU channel

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/sensors.js` (full rewrite)

**Interfaces:**
- Consumes: `.imu-tile`/`.imu-plate`/`.imu-face*` CSS (Task 6), the `bus.on("imu", ...)` topic (Task 4), the existing `bus.on("telemetry", ...)` topic (unchanged fields other than no longer needing `m.imu`).
- Produces: `#plate-imu` DOM node, updated directly from each `imu` WS message — no client-side integration or `requestAnimationFrame` loop.

- [ ] **Step 1: Replace the whole file**

Replace `bridge/milo_bridge/webapp/static/js/panels/sensors.js` in full:

```js
// Sensors panel: live tiles for everything the robot actually reports
// (a single fused IMU 3D plate — accel+gyro, pitch/roll absolute, yaw
// relative — plus SoC temp, CPU%, RAM%, hardware presence), plus a
// Details toggle with a rolling system-history sparkline.
const HISTORY_LEN = 120;
const GYRO_HOT_DPS = 90; // deg/s magnitude before the IMU plate glows

export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="sensor-tiles">
        <div class="sensor-tile imu-tile">
          <div class="label">IMU</div>
          <div class="imu-plate-wrap"><div class="imu-plate" id="plate-imu">
            <div class="imu-face top"></div>
            <div class="imu-face front"></div>
            <div class="imu-face back"></div>
            <div class="imu-face left"></div>
            <div class="imu-face right"></div>
          </div></div>
        </div>
        <div class="sensor-tile"><div class="label">SoC Temp</div><div class="value" id="tile-temp">—</div></div>
        <div class="sensor-tile"><div class="label">CPU</div><div class="value" id="tile-cpu">—</div></div>
        <div class="sensor-tile"><div class="label">RAM</div><div class="value" id="tile-ram">—</div></div>
        <div class="sensor-tile hw-tile"><div class="label">Hardware</div><div class="value" id="tile-hw">—</div></div>
      </div>
      <button class="btn ghost" id="sensor-details-btn" style="margin-top:10px">Details ▾</button>
      <div class="sensor-details hidden" id="sensor-details">
        <div class="spark-label">System — CPU % / RAM % / Temp °C</div>
        <canvas id="spark-system" width="360" height="50"></canvas>
      </div>`;

    const systemHist = [];
    const cvS = el.querySelector("#spark-system"), gS = cvS.getContext("2d");

    function drawTraces(ctx, canvas, hist, range) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!hist.length) return;
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      const ok = getComputedStyle(document.documentElement).getPropertyValue("--ok");
      const colors = [ink, muted, ok];
      const series = hist[0].length;
      for (let k = 0; k < series; k++) {
        ctx.strokeStyle = colors[k % colors.length];
        ctx.beginPath();
        hist.forEach((row, i) => {
          const y = canvas.height - 5 - ((row[k] - range[0]) / (range[1] - range[0])) * (canvas.height - 10);
          i ? ctx.lineTo(i * 3, y) : ctx.moveTo(0, y);
        });
        ctx.stroke();
      }
    }

    const plate = el.querySelector("#plate-imu");
    const offImu = bus.on("imu", (m) => {
      plate.style.setProperty("--pitch", (m.pitch ?? 0).toFixed(2));
      plate.style.setProperty("--roll", (m.roll ?? 0).toFixed(2));
      plate.style.setProperty("--yaw", (m.yaw ?? 0).toFixed(2));
      plate.style.setProperty("--ax", (m.accel?.[0] ?? 0).toFixed(3));
      plate.style.setProperty("--ay", (m.accel?.[1] ?? 0).toFixed(3));
      const mag = Math.hypot(...(m.gyro ?? [0, 0, 0]));
      plate.classList.toggle("hot", mag >= GYRO_HOT_DPS);
    });

    const offT = bus.on("telemetry", (m) => {
      el.querySelector("#tile-temp").textContent = m.temp_c == null ? "n/a" : `${m.temp_c.toFixed(1)}°C`;
      el.querySelector("#tile-cpu").textContent = m.cpu_percent == null ? "n/a" : `${m.cpu_percent}%`;
      el.querySelector("#tile-ram").textContent = m.mem_percent == null ? "n/a" : `${m.mem_percent}%`;

      systemHist.push([m.cpu_percent || 0, m.mem_percent || 0, m.temp_c || 0]);
      if (systemHist.length > HISTORY_LEN) systemHist.shift();
      drawTraces(gS, cvS, systemHist, [0, 100]);
    });

    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#tile-hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `
          <div class="hw-row">
            <span class="hw-name">${k}</span>
            <span class="hw-state" style="color:${ok ? "var(--ok)" : "var(--danger)"}">${ok ? "Connected" : "Not connected"}</span>
          </div>`).join("");
    });

    const details = el.querySelector("#sensor-details");
    const detailsBtn = el.querySelector("#sensor-details-btn");
    detailsBtn.onclick = () => {
      const nowHidden = details.classList.toggle("hidden");
      detailsBtn.textContent = nowHidden ? "Details ▾" : "Details ▴";
    };

    return () => {
      offImu();
      offT();
    };
  },
};
```

- [ ] **Step 2: Syntax-check the file**

Run: `node --check bridge/milo_bridge/webapp/static/js/panels/sensors.js`
Expected: no output (clean exit).

- [ ] **Step 3: Verify referenced files still resolve**

Run: `python -m pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/sensors.js
git commit -m "feat(webapp): drive the Sensors panel from one live IMU channel"
```

---

### Task 8: Manual verification in a browser

**Files:** none (verification checkpoint)

- [ ] **Step 1: Launch the dev dashboard**

```bash
export PYTHONPATH="D:/Github/MILO-Robot/.claude/worktrees/sensors-imu-3d-plate/bridge"
python bridge/tools/webdev.py &
```

Wait for `http://localhost:8080` to respond (poll, don't sleep-guess).

- [ ] **Step 2: Drive it with Playwright (or equivalent) and confirm:**

- Exactly one `#plate-imu` element exists (no leftover `#plate-attitude`/`#plate-gyro`).
- It sits in a full-width tile labeled "IMU" at the top of the sensor tiles.
- After login and a short wait, `--pitch`/`--roll`/`--yaw`/`--ax`/`--ay` on `#plate-imu` reflect `FakeImu`'s fixed values (`pitch=1.0, roll=-2.0, yaw=15.0, accel=(0.01,-0.02,0.98)`) — and land within ~200ms of connecting, not ~2s (confirms the new fast channel, not the old slow one, is driving it).
- Note: `FakeImu` returns a *constant* state each call, so unlike real hardware, `--yaw` will not visibly accumulate in this dev/fake environment — that's expected, not a bug (see spec §7).
- Driving a sustained synthetic high gyro rate through the WS (as in the previous iteration's verification) still toggles `.hot` and the glow is visible.
- The Details section is unchanged (System sparkline only).
- No console errors.

- [ ] **Step 3: Stop the dev server**

```bash
kill %1   # or the PID captured when launching
```

No commit — this is a verification checkpoint. If any issue is found, fix it in the relevant task's file and amend with a small follow-up commit, then re-verify.

---

### Task 9: Final full-suite check, push, and draft PR

**Files:** none (verification + ship checkpoint)

- [ ] **Step 1: Run the full bridge suite one more time**

Run: `python -m pytest bridge/tests -q`
Expected: all PASS

- [ ] **Step 2: Push the branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 3: Open a draft PR**

```bash
gh pr create --draft --title "webapp: unified, calibrated, live IMU plate" --body "$(cat <<'EOF'
## Summary
- Wire up `Mpu6050.calibrate_gyro()` (existed, was never called) into bridge startup, before any servo motion.
- Accumulate yaw server-side on the long-lived `Mpu6050` instance so it persists for the bridge process's lifetime, not a browser tab.
- Add a dedicated ~10Hz `"imu"` WebSocket channel (existing general telemetry stays at 2s for CPU/RAM/temp) so the plate visibly tracks real motion.
- Merge the "Pitch / Roll" and "Gyro" tiles into one full-width "IMU" tile/plate; delete all client-side integration/rAF physics — the browser just renders the server's current state.

## Test plan
- [x] `python -m pytest bridge/tests -q`
- [ ] Manual browser verification per docs/superpowers/plans/2026-07-13-imu-unified-plate.md Task 8
EOF
)"
```
