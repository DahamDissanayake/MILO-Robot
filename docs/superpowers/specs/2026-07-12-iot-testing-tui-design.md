# IOT-Testing — Unified TUI Sensor Tester

**Date:** 2026-07-12
**Status:** Approved
**Supersedes:** `2026-07-12-iot-testing-servo-group-design.md` (the numbered-groups-of-scripts approach). That spec was approved on `main` but never implemented; this spec replaces it entirely with a single TUI application covering every sensor, not just servos.

## Purpose

Milo's hardware needs a single, interactive, SSH-runnable tool to validate every
sensor and actuator — servos, OLED display, IMU, camera, microphones, speaker —
against its documented wiring, with results captured as the tester works through
each one. `IOT-Testing/` becomes a fifth installable package in this repo
(alongside `common/`, `bridge/`, `brain/`, `training/`), built as a
[Textual](https://textual.textualize.io/) TUI app that reuses the real
`milo_bridge` hardware drivers rather than reimplementing hardware access.

## Scope

In scope: one TUI application covering all six sensor groups (servos, OLED
display, IMU, camera, microphones, speaker) plus an I2C bus-scan utility, a
wiring-reference screen, and a results/session-log screen. A `PINOUT.md`
reference doc and an A-Z `README.md`.

Out of scope: modifying any `milo_bridge` driver (they are consumed as-is);
building new gait/pose/graph functionality; anything not reachable from the
Pi's already-documented wiring (`docs/ARCHITECTURE.md` §5).

## Why reuse `milo_bridge` drivers

`bridge/milo_bridge/drivers/` already has a mature, hardware-injectable driver
for every sensor, each unit-tested off-hardware and each exposing a
`from_hardware()` classmethod for the real device:

- `servos.py` → `ServoDriver` (PCA9685, 8 channels, trims, staggered writes)
- `display.py` → `FaceDisplay` (SSD1306, face asset animation, idle blink, PIN render)
- `imu.py` → `Mpu6050` (complementary filter, gyro calibration)
- `audio.py` → `AudioIO` (I2S stereo capture, mono playback, `rms()`)
- `camera.py` → `CameraStreamer` (picamera2, paced JPEG frames)

Reimplementing hardware access for the TUI would duplicate nontrivial logic
(IMU sensor fusion, face frame grouping/animation, I2S framing) that already
exists, is tested, and is what production actually runs. The TUI package
depends on `milo-bridge` and drives these classes directly.

## Package layout

```
IOT-Testing/
  README.md                  — A-Z guide: install, run, navigate, troubleshoot
  PINOUT.md                  — wiring reference (content below), sourced from docs/ARCHITECTURE.md §5
  pyproject.toml             — milo-iot-tester package; deps: milo-bridge, textual
  iot_tester/
    __init__.py
    app.py                   — Textual App, main menu screen (MainMenu)
    results_log.py           — ResultRecorder: shared PASS/FAIL/note capture + session log writer
    screens/
      __init__.py
      wiring.py               — WiringScreen: renders the PINOUT.md tables in-app
      i2c_scan.py             — I2cScanScreen: bus scan, reports 0x3C/0x40/0x68 presence
      servos.py                — ServoScreen: TC1/TC2 per servo via ServoDriver
      display.py               — DisplayScreen: cycles face assets via FaceDisplay
      imu.py                    — ImuScreen: live roll/pitch/gyro via Mpu6050, calibration test
      camera.py                 — CameraScreen: frame capture via CameraStreamer, saves snapshot
      microphones.py            — MicScreen: record + live L/R level meter via AudioIO
      speaker.py                — SpeakerScreen: generated-tone playback via AudioIO
      results.py                — ResultsScreen: view/export the session's accumulated results
  results/.gitkeep
```

## Shared components

### `ResultRecorder` (`iot_tester/results_log.py`)

```python
@dataclass
class TestResult:
    component: str   # e.g. "Servo R1", "IMU", "Camera"
    case: str         # e.g. "TC1 Full range sweep", "Frame capture"
    passed: bool
    note: str = ""

class ResultRecorder:
    def __init__(self, results_dir: Path, run_started: datetime): ...
    def record(self, component: str, case: str, passed: bool, note: str = "") -> None: ...
    def all_results(self) -> list[TestResult]: ...
    def summary(self) -> tuple[int, int]: ...   # (passed, total)
    def flush(self) -> Path: ...                # (re)writes results/session-<ts>.log, returns its path
```

Every screen takes a `ResultRecorder` instance (constructed once in `app.py`,
passed to each screen) and calls `record()` after each PASS/FAIL capture, then
`flush()` so the log file is current even if the app is killed mid-session.
The log format matches the plain-text style from the original servo spec:
grouped by component, one line per case, trailing summary + failure list.

### Wiring content (`PINOUT.md` + `WiringScreen`)

Both are generated from the same source facts — copied verbatim from
`docs/ARCHITECTURE.md` §5 (already the project's single source of truth for
wiring), not re-derived:

- **I2C bus** (one bus, 3 devices): GPIO2/pin3 = SDA, GPIO3/pin5 = SCL →
  PCA9685 `0x40`, SSD1306 `0x3C`, MPU6050 `0x68`.
- **I2S bus** (2 mics in, 1 amp out, shared clocks): GPIO18/pin12 = BCLK,
  GPIO19/pin35 = LRCLK, GPIO20/pin38 = DATA IN (mics, shared line),
  GPIO21/pin40 = DATA OUT (amp). Mic A: L/R→GND (left, mounted left).
  Mic B: L/R→3V3 (right, mounted right).
- **CSI**: IMX219 camera via 15→22-pin Zero ribbon (board edge connector, not
  a GPIO pin).
- **Servo channel map** (PCA9685, matches Sesame firmware naming):
  ch0=R1 (front-right hip), ch1=R2 (front-right knee), ch2=L1 (front-left hip),
  ch3=L2 (front-left knee), ch4=R4 (rear-right knee), ch5=R3 (rear-right hip),
  ch6=L3 (rear-left hip), ch7=L4 (rear-left knee).
- **Power rule** (safety-critical, must appear in both `PINOUT.md` and
  wherever the servo/speaker screens warn the tester): servos draw from Buck 2
  (5A rail) via PCA9685 V+ only, never the Pi's own 5V; PCA9685 logic VCC comes
  from the Pi's 3V3, a different pin from V+.

`PINOUT.md` presents this as the full tables/diagrams (ASCII pin-header
diagram + GPIO table + bus-detail tables, in the same shape as
`docs/ARCHITECTURE.md` §5.2–§5.5). `WiringScreen` renders the same tables as
Textual `DataTable`/`Static` widgets, navigable without leaving the TUI.

## Screens

### Main menu (`app.py`)

A Textual `Screen` listing: Wiring Reference, I2C Bus Scan, Servos, Display,
IMU, Camera, Microphones, Speaker, Results, Quit. Selecting an item pushes the
corresponding screen; each screen has a "back to menu" binding.

### I2C Bus Scan (`screens/i2c_scan.py`)

Scans I2C bus 1 (via `smbus2`, addresses 0x03–0x77) and reports which of the
three expected addresses (`0x3C` OLED, `0x40` PCA9685, `0x68` MPU6050)
responded, plus any unexpected addresses found. Informational only — auto
pass/fail per expected address, no tester prompt needed (device either
responds or doesn't).

### Servos (`screens/servos.py`)

Unchanged from the original approved servo design, ported into a Textual
screen: for each of the 8 servos in channel order (R1, R2, L1, L2, R4, R3, L3,
L4), two cases:
- **TC1 Full range sweep**: `ServoDriver.set_angle()` steps 0→45→90→135→180,
  pausing briefly per step; PASS/FAIL buttons: "did it sweep smoothly?"
- **TC2 Return to zero**: steps 180→90→0; PASS/FAIL buttons: "did it return
  cleanly to 0°?"

On FAIL, a text input captures a one-line note. Uses
`ServoDriver.from_hardware()`; a startup banner (matching the original
design) warns that servos must be on the battery/5A rail, never the Pi's 5V,
and the robot must be on a stand.

### Display (`screens/display.py`)

Discovers face names by scanning `bridge/assets/faces/` (grouping
`<name>_<n>.png` sequences and bare `<name>.png` files by stem — the same
grouping `FaceDisplay.load_face_frames()` already does internally; the screen
lists distinct stems by directory scan rather than hardcoding the ~35 face
names, so new face assets are picked up automatically). For each face: call
`FaceDisplay.set_face(name)`, PASS/FAIL "did this face render correctly on
the OLED?". One additional fixed case: `FaceDisplay.show_pin("123456")`,
PASS/FAIL "did the pairing-PIN screen render legibly?". Uses
`FaceDisplay.from_hardware(assets_dir)`.

### IMU (`screens/imu.py`)

Two parts:
1. **Calibration case**: prompts "keep the robot still," runs
   `Mpu6050.calibrate_gyro()`, PASS/FAIL based on whether it completed without
   raising.
2. **Live tracking case**: polls `Mpu6050.update()` in a timer (10 Hz) and
   renders roll/pitch/gyro numbers live in the screen; tester tilts the robot
   forward/back/side-to-side, then PASS/FAIL: "did roll/pitch respond
   correctly to tilting?" Uses `Mpu6050.from_hardware()`.

### Camera (`screens/camera.py`)

Captures 3 frames through `CameraStreamer.frames()` (via
`CameraStreamer.from_hardware()`), timing each. Automatic FAIL if any
exception is raised or a frame is empty. On success, saves the last frame as
`results/camera-test-<timestamp>.jpg`. Because a headless Pi terminal can't
preview a JPEG, the screen's PASS/FAIL question is scoped to what it can
verify: "did all 3 frames capture without error?" — the README's
troubleshooting section tells the tester to `scp` the saved file down to
check actual framing/focus/content.

### Microphones (`screens/microphones.py`)

Records ~3 seconds via `AudioIO.capture_frames()` (`from_hardware` not
defined on `AudioIO` — it's constructed directly, no device argument needed
for the default I2S capture device). Deinterleaves the stereo `int16` PCM
(`samples[0::2]` = left, `samples[1::2]` = right) and renders a live L/R bar
meter using `rms()` from `milo_bridge.drivers.audio` on each channel's slice.
Saves the recording as `results/mic-test-<timestamp>.wav` (via the stdlib
`wave` module — no new audio dependency). PASS/FAIL: "did the level meter
respond when you spoke/clapped near each mic?"

### Speaker (`screens/speaker.py`)

Generates a 1-second 440 Hz sine tone with `numpy` (already a transitive
dependency via `milo-bridge`), plays it with `AudioIO.play_pcm()`. PASS/FAIL:
"did you hear a clear tone?" No dependency on the microphone screen.

### Results (`screens/results.py`)

Renders `ResultRecorder.all_results()` as a table (component, case, PASS/FAIL,
note), the running `summary()` count, and the path of the flushed log file.

## `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "milo-iot-tester"
version = "0.1.0"
description = "Project Milo IOT-Testing: TUI hardware tester for every sensor/actuator"
requires-python = ">=3.11"
license = { text = "Apache-2.0" }
dependencies = [
    "milo-bridge",
    "textual>=0.60",
]

[project.scripts]
milo-iot-tester = "iot_tester.app:main"

[tool.setuptools.packages.find]
include = ["iot_tester*"]
```

`milo-bridge` must be installed with its `pi` extra
(`pip install -e ../bridge[pi]`) for the hardware drivers' dependencies
(adafruit-blinka, adafruit-circuitpython-pca9685, luma.oled, smbus2,
sounddevice) to be present; `picamera2` comes from `apt`
(`python3-picamera2`), consistent with how `bridge/pyproject.toml` already
documents it.

## `README.md` — A-Z guide contents

Prerequisites (full wiring per `PINOUT.md`/`docs/ARCHITECTURE.md`, battery
rail for servos, robot on a stand) → enable I2C (`raspi-config nonint
do_i2c 0`) → verify `i2cdetect -y 1` shows all three addresses → enable I2S
(`dtparam=i2s=on` + `dtoverlay=googlevoicehat-soundcard` in
`/boot/firmware/config.txt`, reboot) → `apt install python3-picamera2
i2c-tools python3-venv` → SSH in → `git pull` → `python3 -m venv --system-site-packages .venv && source .venv/bin/activate` (`--system-site-packages` so the apt-installed `picamera2` is importable) → `pip install -e bridge[pi] -e IOT-Testing` →
`milo-iot-tester` → navigating the menu → where results/snapshots/recordings
land → troubleshooting table (per-sensor, mirroring the original servo
troubleshooting entries plus new ones for I2S/camera) → safety reminders
(battery-only servo power, stand support, common ground).

## Testing

Like the original servo-only spec, this is fundamentally a manual hardware
tool — verification is running it against real hardware on the Pi. The
`ResultRecorder` class (pure Python, no hardware) is simple enough that it
does not need its own unit test beyond what the implementation task exercises
manually; no `milo_bridge` driver code changes, so no changes to
`bridge/tests/`.
