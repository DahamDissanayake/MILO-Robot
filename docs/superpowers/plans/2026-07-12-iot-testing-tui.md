# IOT-Testing Unified TUI Sensor Tester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `milo-iot-tester`, a Textual TUI app installed alongside `common`/`bridge`/`brain`/`training`, that lets a tester on the Pi (over SSH) validate every sensor/actuator — servos, OLED display, IMU, camera, microphones, speaker — plus an I2C bus scan and an in-app wiring reference, recording PASS/FAIL results to a session log.

**Architecture:** One Textual `App` (`iot_tester/app.py`) with a main-menu `Screen` that pushes one `Screen` per sensor. Every sensor screen drives the real `milo_bridge` driver (`ServoDriver`, `FaceDisplay`, `Mpu6050`, `CameraStreamer`, `AudioIO`) via its `from_hardware()` constructor — never reimplemented. Hardware-specific imports (`board`, `busio`, `smbus2`, `luma.oled`, `sounddevice`, `picamera2`) only happen inside those `from_hardware()` calls (already true of every `milo_bridge` driver), so every screen degrades gracefully — and is genuinely testable — on a machine with no Pi hardware attached: the `try/except` around each hardware-open call catches the resulting `ImportError`/`OSError` and shows a friendly in-app message instead of crashing. This is what makes a real `textual` `Pilot`-driven integration test possible in Task 10 without any hardware.

Tester interaction (PASS/FAIL + optional note) is unified behind one small widget, `PassFailPrompt` (Task 1), built on an `asyncio.Future`: a screen's test coroutine does `passed, note = await ask_pass_fail(container, "...")` and reads like straight-line code, no manual state machine.

**Tech Stack:** Python 3.11+, `textual>=0.60` (TUI framework), `numpy` (tone generation, stereo channel deinterleaving — already a `milo-bridge` dependency), `pytest` + `pytest-asyncio` (dev). Depends on `milo-bridge` (editable, local monorepo package) for all hardware drivers.

## Global Constraints

- Reuse `milo_bridge` drivers as-is (`bridge/milo_bridge/drivers/{servos,display,imu,audio,camera}.py`) — do not modify them, do not reimplement their logic.
- I2C addresses: PCA9685 `0x40`, SSD1306 `0x3C`, MPU6050 `0x68`. Servo channel map: `R1=0, R2=1, L1=2, L2=3, R4=4, R3=5, L3=6, L4=7`.
- Servos are powered ONLY from the battery/5A buck rail, never the Pi's 5V — this must appear as an on-screen warning on the Servos screen before testing starts.
- Package name `milo-iot-tester`, console script `milo-iot-tester = iot_tester.app:main`, installed the same way as the repo's other packages: `pip install -e ./IOT-Testing` (see `common`/`bridge` install pattern in `README.md`).
- Session results go to `IOT-Testing/results/session-<UTC timestamp>.log` (plain text), auto-flushed after every recorded result so nothing is lost if the app exits abnormally. Camera/mic captures also land in `IOT-Testing/results/`.
- Every hardware-open call (`X.from_hardware(...)`, `SMBus(1)`, first use of `sounddevice`) must be wrapped in `try/except Exception` that shows a friendly message — this is both a UX requirement and what makes the app testable off-Pi.
- No modifications to `bridge/`, `common/`, `brain/`, or `training/`.

---

### Task 1: Package scaffold, ResultRecorder, and the shared PASS/FAIL widget

**Files:**
- Create: `IOT-Testing/pyproject.toml`
- Create: `IOT-Testing/iot_tester/__init__.py`
- Create: `IOT-Testing/iot_tester/results_log.py`
- Create: `IOT-Testing/iot_tester/widgets.py`
- Create: `IOT-Testing/iot_tester/screens/__init__.py`
- Create: `IOT-Testing/results/.gitkeep`
- Modify: `.gitignore`
- Test: `IOT-Testing/tests/test_results_log.py`
- Test: `IOT-Testing/tests/test_widgets.py`

**Interfaces:**
- Produces: `ResultRecorder(results_dir: Path, run_started: datetime)` with `.record(component: str, case: str, passed: bool, note: str = "") -> None`, `.all_results() -> list[TestResult]`, `.summary() -> tuple[int, int]` (passed, total), `.flush() -> Path`. `TestResult` is a frozen dataclass with fields `component: str, case: str, passed: bool, note: str = ""`.
- Produces: `PassFailPrompt(question: str)` (a `textual.widget.Widget`) and `async def ask_pass_fail(container: Widget, question: str) -> tuple[bool, str]` — every later screen task uses `ask_pass_fail` exactly this way.
- Produces: the `IOT-Testing/` installable package itself (`pip install -e ./IOT-Testing[dev]`), which every later task's tests depend on.

- [ ] **Step 1: Write `IOT-Testing/pyproject.toml`**

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
    "numpy>=1.26",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[project.scripts]
milo-iot-tester = "iot_tester.app:main"

[tool.setuptools.packages.find]
include = ["iot_tester*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `IOT-Testing/iot_tester/__init__.py` and `IOT-Testing/iot_tester/screens/__init__.py`**

Both empty files (mark the directories as packages).

- [ ] **Step 3: Create `IOT-Testing/results/.gitkeep`**

Empty file.

- [ ] **Step 4: Update `.gitignore`**

Add a new section at the end of `.gitignore`:

```gitignore

# IOT-Testing session artifacts (directory tracked via .gitkeep, contents are not)
IOT-Testing/results/*
!IOT-Testing/results/.gitkeep
```

- [ ] **Step 5: Write the failing tests for `ResultRecorder`**

Create `IOT-Testing/tests/test_results_log.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

from iot_tester.results_log import ResultRecorder


def test_record_and_summary(tmp_path: Path) -> None:
    recorder = ResultRecorder(tmp_path, datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc))
    recorder.record("Servo R1", "TC1 Full range sweep", True)
    recorder.record("Servo R1", "TC2 Return to zero", False, note="jitters at 180")
    assert recorder.summary() == (1, 2)


def test_all_results_preserves_order(tmp_path: Path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    recorder.record("IMU", "Gyro calibration", True)
    recorder.record("IMU", "Live tracking", True)
    results = recorder.all_results()
    assert [r.case for r in results] == ["Gyro calibration", "Live tracking"]


def test_flush_writes_log_file(tmp_path: Path) -> None:
    run_started = datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc)
    recorder = ResultRecorder(tmp_path, run_started)
    recorder.record("Servo R1", "TC1 Full range sweep", True)
    recorder.record("Servo R2", "TC1 Full range sweep", False, note="no movement")
    log_path = recorder.flush()
    assert log_path.parent == tmp_path
    assert log_path.name == "session-20260712T100000Z.log"
    text = log_path.read_text(encoding="utf-8")
    assert "Servo R1" in text
    assert "Servo R2" in text
    assert "PASS" in text
    assert "FAIL" in text
    assert "no movement" in text
    assert "1/2 test cases passed" in text


def test_flush_creates_results_dir(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    recorder = ResultRecorder(results_dir, datetime.now(timezone.utc))
    recorder.record("OLED", "Face: idle", True)
    log_path = recorder.flush()
    assert log_path.exists()
    assert results_dir.is_dir()
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `pip install -e ./common && pip install -e ./bridge && pip install -e "./IOT-Testing[dev]"` (from the repo root; installs the whole local dependency chain editable), then `pytest IOT-Testing/tests/test_results_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.results_log'`

- [ ] **Step 7: Implement `IOT-Testing/iot_tester/results_log.py`**

```python
"""Shared PASS/FAIL result capture for every IOT-Testing screen.

Every screen records through one ResultRecorder instance, constructed once
in app.py and passed down. flush() is called after every record() so the
session log on disk is always current, even if the app exits abnormally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TestResult:
    component: str
    case: str
    passed: bool
    note: str = ""


class ResultRecorder:
    def __init__(self, results_dir: Path, run_started: datetime) -> None:
        self.results_dir = Path(results_dir)
        self.run_started = run_started
        self._results: list[TestResult] = []

    def record(self, component: str, case: str, passed: bool, note: str = "") -> None:
        self._results.append(TestResult(component, case, passed, note))

    def all_results(self) -> list[TestResult]:
        return list(self._results)

    def summary(self) -> tuple[int, int]:
        total = len(self._results)
        passed = sum(1 for r in self._results if r.passed)
        return passed, total

    def flush(self) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.run_started.strftime("%Y%m%dT%H%M%SZ")
        log_path = self.results_dir / f"session-{timestamp}.log"
        passed, total = self.summary()
        lines = [
            "MILO IOT-Testing -- Session Log",
            f"Run: {self.run_started.isoformat()}",
            "",
        ]
        current_component = None
        for r in self._results:
            if r.component != current_component:
                lines.append(r.component)
                current_component = r.component
            status = "PASS" if r.passed else "FAIL"
            note = f"   note: {r.note}" if r.note else ""
            lines.append(f"  {r.case:<28} {status}{note}")
        lines.append("")
        lines.append(f"Summary: {passed}/{total} test cases passed")
        failures = [r for r in self._results if not r.passed]
        if failures:
            lines.append(
                "Failed: " + ", ".join(f"{r.component} {r.case.split()[0]}" for r in failures)
            )
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_results_log.py -v`
Expected: 4 passed

- [ ] **Step 9: Write the failing tests for `PassFailPrompt`**

Create `IOT-Testing/tests/test_widgets.py`:

```python
from iot_tester.widgets import PassFailPrompt


async def test_pass_fail_prompt_composes_three_widgets() -> None:
    prompt = PassFailPrompt("Did it work?")
    widgets = list(prompt.compose())
    assert len(widgets) == 3


async def test_pass_fail_prompt_resolves_pass() -> None:
    prompt = PassFailPrompt("Did it work?")
    prompt._answer.set_result((True, ""))
    assert await prompt.wait_for_answer() == (True, "")


async def test_pass_fail_prompt_resolves_fail_with_note() -> None:
    prompt = PassFailPrompt("Did it work?")
    prompt._answer.set_result((False, "jitters at 180"))
    assert await prompt.wait_for_answer() == (False, "jitters at 180")
```

- [ ] **Step 10: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_widgets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.widgets'`

- [ ] **Step 11: Implement `IOT-Testing/iot_tester/widgets.py`**

```python
"""Shared PASS/FAIL capture widget used by every interactive test screen.

A screen's test coroutine calls ``passed, note = await ask_pass_fail(container,
question)``: it mounts a PassFailPrompt, waits for the tester to click PASS or
type a note and submit after FAIL, then removes the prompt and returns.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, Input, Static


class PassFailPrompt(Widget):
    """A question with PASS/FAIL buttons; FAIL reveals a note Input before resolving."""

    DEFAULT_CSS = """
    PassFailPrompt Input.hidden {
        display: none;
    }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question
        self._answer: asyncio.Future[tuple[bool, str]] = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        yield Static(self._question, classes="prompt-question")
        with Horizontal(classes="prompt-buttons"):
            yield Button("PASS", id="pass-btn", variant="success")
            yield Button("FAIL", id="fail-btn", variant="error")
        yield Input(
            placeholder="What went wrong? (Enter to submit)", id="note-input", classes="hidden"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "pass-btn":
            if not self._answer.done():
                self._answer.set_result((True, ""))
        elif event.button.id == "fail-btn":
            note_input = self.query_one("#note-input", Input)
            note_input.remove_class("hidden")
            note_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if not self._answer.done():
            self._answer.set_result((False, event.value))

    async def wait_for_answer(self) -> tuple[bool, str]:
        return await self._answer


async def ask_pass_fail(container: Widget, question: str) -> tuple[bool, str]:
    prompt = PassFailPrompt(question)
    await container.mount(prompt)
    result = await prompt.wait_for_answer()
    await prompt.remove()
    return result
```

- [ ] **Step 12: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/ -v`
Expected: 7 passed (4 from Step 8 + 3 from this step)

- [ ] **Step 13: Commit**

```bash
git add IOT-Testing/pyproject.toml IOT-Testing/iot_tester/__init__.py \
        IOT-Testing/iot_tester/results_log.py IOT-Testing/iot_tester/widgets.py \
        IOT-Testing/iot_tester/screens/__init__.py IOT-Testing/results/.gitkeep \
        IOT-Testing/tests/test_results_log.py IOT-Testing/tests/test_widgets.py \
        .gitignore
git commit -m "feat: scaffold milo-iot-tester package, ResultRecorder, PassFailPrompt"
```

---

### Task 2: Wiring reference — `PINOUT.md` and `WiringScreen`

**Files:**
- Create: `IOT-Testing/PINOUT.md`
- Create: `IOT-Testing/iot_tester/screens/wiring.py`
- Test: `IOT-Testing/tests/test_wiring.py`

**Interfaces:**
- Consumes: nothing from Task 1's runtime code (this screen has no PASS/FAIL cases, no `ResultRecorder` dependency).
- Produces: `WiringScreen` (a `textual.screen.Screen`, zero-argument constructor) — Task 10 imports and pushes it with no arguments.

- [ ] **Step 1: Write `IOT-Testing/PINOUT.md`**

```markdown
# Milo Wiring Reference

Source of truth: `docs/ARCHITECTURE.md` §5. This is a condensed, on-Pi-readable
copy of the same facts, for reference during hardware testing.

## Safety rule (read this first)

Servos draw from Buck 2 (5A rail) via the PCA9685's **V+** terminal ONLY —
never from the Pi's own 5V. PCA9685 logic **VCC** comes from the Pi's 3.3V, a
DIFFERENT pin from V+. Set both bucks to 5.1V with a multimeter before
connecting any load.

## Pi Zero 2W 40-pin header

```
                 3V3  [ 1] [ 2]  5V   <-- Buck 1 (logic rail)
   I2C SDA -->  GPIO2 [ 3] [ 4]  5V
   I2C SCL -->  GPIO3 [ 5] [ 6]  GND  <-- common ground
                      [ 7] [ 8]
                 GND  [ 9] [10]
                      [11] [12]  GPIO18 --> I2S BCLK
                      [13] [14]  GND
                      [15] [16]
                 3V3 [17] [18]        <-- 3V3 -> mic VDD, PCA9685 VCC, Mic B L/R pin
                      [19] [20]  GND
                      [21] [22]
                      [23] [24]
                 GND [25] [26]
                      [27] [28]
                      [29] [30]  GND
                      [31] [32]
                      [33] [34]  GND
   I2S LRCLK <- GPIO19[35] [36]
                      [37] [38]  GPIO20 --> I2S DATA IN (from mics)
                 GND [39] [40]  GPIO21 --> I2S DATA OUT (to amp)

   CSI connector (board edge): IMX219 camera via 15->22-pin Zero ribbon
```

| GPIO | Pin | Function | Connects to |
|---|---|---|---|
| GPIO 2 | 3 | I2C1 SDA | PCA9685 SDA + SSD1306 SDA + MPU6050 SDA |
| GPIO 3 | 5 | I2C1 SCL | PCA9685 SCL + SSD1306 SCL + MPU6050 SCL |
| GPIO 18 | 12 | I2S BCLK | INMP441 x2 SCK and MAX98357A BCLK |
| GPIO 19 | 35 | I2S LRCLK | INMP441 x2 WS and MAX98357A LRC |
| GPIO 20 | 38 | I2S DATA IN | INMP441 x2 SD (one shared line) |
| GPIO 21 | 40 | I2S DATA OUT | MAX98357A DIN |
| 5V | 2/4 | Power in | Buck 1 output |
| 3V3 | 1/17 | Logic ref | mic VDD, PCA9685 VCC, Mic B channel-select |
| GND | 6,9,14,... | Ground | common ground |
| CSI | -- | Camera | IMX219, 15->22-pin ribbon |

## I2C bus (3 devices, one bus)

- PCA9685 @ `0x40` — servo driver (VCC=3V3, V+=Buck 2, never the Pi's 5V)
- SSD1306 @ `0x3C` — OLED face
- MPU6050 @ `0x68` — IMU (mount RIGID near body center — screws/standoffs, never foam)

Bring-up check: `i2cdetect -y 1` must show `0x3c`, `0x40`, `0x68`.

## I2S bus (2 mics in, 1 amp out, shared clocks)

- GPIO18 BCLK → Mic A SCK, Mic B SCK, MAX98357A BCLK
- GPIO19 LRCLK → Mic A WS, Mic B WS, MAX98357A LRC
- GPIO20 ← Mic A SD, Mic B SD (shared data-in line)
- GPIO21 → MAX98357A DIN

Mic A: L/R pin → GND (LEFT channel), mounted LEFT side of head.
Mic B: L/R pin → 3V3 (RIGHT channel), mounted RIGHT side of head.
Target 10–15 cm mic separation.

## Servo channel map (PCA9685) — matches Sesame firmware naming

| Channel | Servo | Position |
|---|---|---|
| 0 | R1 | front-right hip |
| 1 | R2 | front-right knee |
| 2 | L1 | front-left hip |
| 3 | L2 | front-left knee |
| 4 | R4 | rear-right knee |
| 5 | R3 | rear-right hip |
| 6 | L3 | rear-left hip |
| 7 | L4 | rear-left knee |
```

- [ ] **Step 2: Write the failing tests**

Create `IOT-Testing/tests/test_wiring.py`:

```python
from iot_tester.screens.wiring import (
    GPIO_TABLE,
    I2C_BUS,
    I2S_BUS,
    PIN_HEADER_DIAGRAM,
    POWER_RULE,
    SERVO_MAP,
    WiringScreen,
)


def test_i2c_bus_lists_all_three_addresses() -> None:
    assert "0x40" in I2C_BUS
    assert "0x3C" in I2C_BUS
    assert "0x68" in I2C_BUS


def test_servo_map_lists_all_eight_channels() -> None:
    for name, channel in [
        ("R1", "0"), ("R2", "1"), ("L1", "2"), ("L2", "3"),
        ("R4", "4"), ("R3", "5"), ("L3", "6"), ("L4", "7"),
    ]:
        assert name in SERVO_MAP
        assert f"ch{channel}" in SERVO_MAP


def test_power_rule_warns_about_pi_5v() -> None:
    assert "5V" in POWER_RULE
    assert "never" in POWER_RULE.lower()


def test_i2s_bus_documents_mic_channel_select() -> None:
    assert "GND" in I2S_BUS
    assert "3V3" in I2S_BUS


def test_pin_header_diagram_shows_i2c_pins() -> None:
    assert "GPIO2" in PIN_HEADER_DIAGRAM
    assert "GPIO3" in PIN_HEADER_DIAGRAM


def test_gpio_table_lists_i2s_pins() -> None:
    assert "GPIO 18" in GPIO_TABLE
    assert "GPIO 21" in GPIO_TABLE


def test_wiring_screen_composes_without_error() -> None:
    screen = WiringScreen()
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_wiring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.wiring'`

- [ ] **Step 4: Implement `IOT-Testing/iot_tester/screens/wiring.py`**

```python
"""Wiring Reference screen -- the same facts as ../../PINOUT.md, for in-app lookup."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

POWER_RULE = """\
SAFETY: servos draw from Buck 2 (5A rail) via PCA9685 V+ ONLY -- never the
Pi's own 5V. PCA9685 logic VCC comes from the Pi's 3V3, a DIFFERENT pin
from V+. Set both bucks to 5.1V with a multimeter before connecting any load.
"""

PIN_HEADER_DIAGRAM = """\
                       Raspberry Pi Zero 2W (40-pin header, top view)
                 3V3  [ 1] [ 2]  5V   <-- Buck 1 (logic rail)
   I2C SDA -->  GPIO2 [ 3] [ 4]  5V
   I2C SCL -->  GPIO3 [ 5] [ 6]  GND  <-- common ground
                      [ 7] [ 8]
                 GND  [ 9] [10]
                      [11] [12]  GPIO18 --> I2S BCLK
                      [13] [14]  GND
                      [15] [16]
                 3V3 [17] [18]        <-- 3V3 -> mic VDD, PCA9685 VCC, Mic B L/R pin
                      [19] [20]  GND
                      [21] [22]
                      [23] [24]
                 GND [25] [26]
                      [27] [28]
                      [29] [30]  GND
                      [31] [32]
                      [33] [34]  GND
   I2S LRCLK <- GPIO19[35] [36]
                      [37] [38]  GPIO20 --> I2S DATA IN (from mics)
                 GND [39] [40]  GPIO21 --> I2S DATA OUT (to amp)

   CSI connector (board edge): IMX219 camera via 15->22-pin Zero ribbon
"""

GPIO_TABLE = """\
GPIO      Pin    Function        Connects to
GPIO 2    3      I2C1 SDA        PCA9685 SDA + SSD1306 SDA + MPU6050 SDA
GPIO 3    5      I2C1 SCL        PCA9685 SCL + SSD1306 SCL + MPU6050 SCL
GPIO 18   12     I2S BCLK        INMP441 x2 SCK and MAX98357A BCLK
GPIO 19   35     I2S LRCLK       INMP441 x2 WS and MAX98357A LRC
GPIO 20   38     I2S DATA IN     INMP441 x2 SD (one shared line)
GPIO 21   40     I2S DATA OUT    MAX98357A DIN
5V        2/4    Power in        Buck 1 output
3V3       1/17   Logic ref       mic VDD, PCA9685 VCC, Mic B channel-select
GND       6,9,14,...  Ground     common ground
CSI       --     Camera          IMX219, 15->22-pin ribbon
"""

I2C_BUS = """\
I2C bus 1 -- 3 devices, one bus (GPIO2 SDA / GPIO3 SCL):
  PCA9685 @ 0x40   servo driver   (VCC=3V3, V+=Buck 2, never the Pi's 5V)
  SSD1306 @ 0x3C   OLED face
  MPU6050 @ 0x68   IMU (mount RIGID near body center -- screws/standoffs, never foam)

Bring-up check: i2cdetect -y 1 must show 0x3c, 0x40, 0x68.
"""

I2S_BUS = """\
I2S bus -- 2 mics in + 1 amp out, shared clocks:
  GPIO18 BCLK  --> Mic A SCK, Mic B SCK, MAX98357A BCLK
  GPIO19 LRCLK --> Mic A WS,  Mic B WS,  MAX98357A LRC
  GPIO20       <-- Mic A SD,  Mic B SD   (shared data-in)
  GPIO21       --> MAX98357A DIN

  Mic A: L/R pin -> GND  (LEFT channel)  -- mounted LEFT side of head
  Mic B: L/R pin -> 3V3  (RIGHT channel) -- mounted RIGHT side of head
  Target 10-15 cm mic separation.
"""

SERVO_MAP = """\
Servo channel map (PCA9685) -- matches Sesame firmware naming:
  ch0 = R1  front-right hip      ch4 = R4  rear-right knee
  ch1 = R2  front-right knee     ch5 = R3  rear-right hip
  ch2 = L1  front-left hip       ch6 = L3  rear-left hip
  ch3 = L2  front-left knee      ch7 = L4  rear-left knee
"""


class WiringScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(POWER_RULE, classes="wiring-block warning")
            yield Static(PIN_HEADER_DIAGRAM, classes="wiring-block")
            yield Static(GPIO_TABLE, classes="wiring-block")
            yield Static(I2C_BUS, classes="wiring-block")
            yield Static(I2S_BUS, classes="wiring-block")
            yield Static(SERVO_MAP, classes="wiring-block")
        yield Footer()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_wiring.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add IOT-Testing/PINOUT.md IOT-Testing/iot_tester/screens/wiring.py IOT-Testing/tests/test_wiring.py
git commit -m "feat: add PINOUT.md and the in-app Wiring Reference screen"
```

---

### Task 3: I2C Bus Scan screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/i2c_scan.py`
- Test: `IOT-Testing/tests/test_i2c_scan.py`

**Interfaces:**
- Consumes: `ResultRecorder` from Task 1 (`iot_tester.results_log`).
- Produces: `I2cScanScreen(recorder: ResultRecorder)`, `scan_i2c_bus(bus, addresses=range(0x03, 0x78)) -> dict[int, bool]`, `EXPECTED_DEVICES: dict[int, str]` — Task 10 imports `I2cScanScreen` and constructs it with `app.recorder`.

- [ ] **Step 1: Write the failing tests**

Create `IOT-Testing/tests/test_i2c_scan.py`:

```python
from datetime import datetime, timezone

from iot_tester.screens.i2c_scan import EXPECTED_DEVICES, I2cScanScreen, scan_i2c_bus
from iot_tester.results_log import ResultRecorder


class FakeBus:
    def __init__(self, present: set[int]) -> None:
        self._present = present

    def read_byte(self, address: int) -> int:
        if address not in self._present:
            raise OSError(f"no device at 0x{address:02X}")
        return 0


def test_scan_reports_present_and_absent_addresses() -> None:
    bus = FakeBus({0x3C, 0x40, 0x68})
    found = scan_i2c_bus(bus, addresses=range(0x3A, 0x42))
    assert found[0x3C] is True
    assert found[0x40] is True
    assert found[0x3B] is False


def test_expected_devices_match_documented_addresses() -> None:
    assert EXPECTED_DEVICES == {
        0x3C: "SSD1306 OLED",
        0x40: "PCA9685 servo driver",
        0x68: "MPU6050 IMU",
    }


def test_i2c_scan_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = I2cScanScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_i2c_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.i2c_scan'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/i2c_scan.py`**

```python
"""I2C Bus Scan screen: quick health check for the 3 expected devices."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from iot_tester.results_log import ResultRecorder

EXPECTED_DEVICES: dict[int, str] = {
    0x3C: "SSD1306 OLED",
    0x40: "PCA9685 servo driver",
    0x68: "MPU6050 IMU",
}


def scan_i2c_bus(bus, addresses: range = range(0x03, 0x78)) -> dict[int, bool]:
    """bus must expose read_byte(address), raising OSError when nothing responds."""
    found: dict[int, bool] = {}
    for address in addresses:
        try:
            bus.read_byte(address)
            found[address] = True
        except OSError:
            found[address] = False
    return found


class I2cScanScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("Scanning I2C bus 1...", id="scan-status")
            yield DataTable(id="scan-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#scan-table", DataTable)
        table.add_columns("Address", "Device", "Found")
        self.run_scan()

    @work()
    async def run_scan(self) -> None:
        status = self.query_one("#scan-status", Static)
        table = self.query_one("#scan-table", DataTable)
        try:
            from smbus2 import SMBus

            bus = await asyncio.to_thread(SMBus, 1)
        except Exception as exc:
            status.update(f"Could not open I2C bus 1: {exc}")
            for name in EXPECTED_DEVICES.values():
                self.recorder.record("I2C Bus Scan", name, False, note=str(exc))
            self.recorder.flush()
            return

        found = await asyncio.to_thread(scan_i2c_bus, bus)
        for address, name in EXPECTED_DEVICES.items():
            present = found.get(address, False)
            table.add_row(f"0x{address:02X}", name, "yes" if present else "NO")
            self.recorder.record("I2C Bus Scan", name, present)
        extra = sorted(addr for addr, ok in found.items() if ok and addr not in EXPECTED_DEVICES)
        for address in extra:
            table.add_row(f"0x{address:02X}", "(unexpected device)", "yes")
        self.recorder.flush()
        status.update("Scan complete.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_i2c_scan.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/i2c_scan.py IOT-Testing/tests/test_i2c_scan.py
git commit -m "feat: add I2C Bus Scan screen"
```

---

### Task 4: Servos screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/servos.py`
- Test: `IOT-Testing/tests/test_servos_screen.py`

**Interfaces:**
- Consumes: `ResultRecorder` (Task 1), `ask_pass_fail` (Task 1), `milo_bridge.drivers.servos.{SERVO_CHANNELS, ServoDriver}` (existing, unmodified).
- Produces: `ServoScreen(recorder: ResultRecorder)`, `run_sweep(driver, servo, angles, step_delay_s=STEP_DELAY_S)`, constants `SWEEP_UP_ANGLES = (0, 45, 90, 135, 180)`, `SWEEP_DOWN_ANGLES = (180, 90, 0)`.

- [ ] **Step 1: Write the failing tests**

Create `IOT-Testing/tests/test_servos_screen.py`:

```python
import asyncio
from datetime import datetime, timezone

from milo_bridge.drivers.servos import ServoDriver

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.servos import SWEEP_UP_ANGLES, ServoScreen, run_sweep


class FakeChannel:
    def __init__(self) -> None:
        self.duty_cycle = 0


class FakePca:
    def __init__(self) -> None:
        self.channels = [FakeChannel() for _ in range(16)]


def test_run_sweep_moves_through_every_angle() -> None:
    driver = ServoDriver(FakePca(), stagger_ms=0)
    asyncio.run(run_sweep(driver, "R1", SWEEP_UP_ANGLES, step_delay_s=0))
    assert driver.last_angle("R1") == 180


def test_run_sweep_ends_at_last_angle_in_sequence() -> None:
    driver = ServoDriver(FakePca(), stagger_ms=0)
    asyncio.run(run_sweep(driver, "L3", (10, 20, 30), step_delay_s=0))
    assert driver.last_angle("L3") == 30


def test_servo_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = ServoScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_servos_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.servos'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/servos.py`**

```python
"""Servos screen: TC1 full-range sweep / TC2 return-to-zero, per servo."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from milo_bridge.drivers.servos import SERVO_CHANNELS, ServoDriver

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

SWEEP_UP_ANGLES = (0, 45, 90, 135, 180)
SWEEP_DOWN_ANGLES = (180, 90, 0)
STEP_DELAY_S = 0.5


async def run_sweep(
    driver: ServoDriver, servo: str, angles: tuple[int, ...], step_delay_s: float = STEP_DELAY_S
) -> None:
    for angle in angles:
        driver.set_angle(servo, angle)
        await asyncio.sleep(step_delay_s)


class ServoScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Servos must be powered from the battery/5A rail, NEVER the Pi's 5V. "
            "Keep the robot on a stand, clear of obstructions.",
            classes="warning",
        )
        yield Button("Start Servo Tests", id="start-btn", variant="primary")
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-btn":
            event.button.disabled = True
            self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            driver = ServoDriver.from_hardware()
        except Exception as exc:
            await container.mount(Static(f"Could not open the PCA9685: {exc}"))
            return

        for name in SERVO_CHANNELS:
            await run_sweep(driver, name, SWEEP_UP_ANGLES)
            passed, note = await ask_pass_fail(
                container, f"{name}: did it sweep smoothly through its full range?"
            )
            self.recorder.record(f"Servo {name}", "TC1 Full range sweep", passed, note)
            self.recorder.flush()

            await run_sweep(driver, name, SWEEP_DOWN_ANGLES)
            passed, note = await ask_pass_fail(
                container, f"{name}: did it return cleanly to 0 degrees?"
            )
            self.recorder.record(f"Servo {name}", "TC2 Return to zero", passed, note)
            self.recorder.flush()

        driver.relax()
        await container.mount(Static("Servo tests complete. Press Escape to return to menu."))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_servos_screen.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/servos.py IOT-Testing/tests/test_servos_screen.py
git commit -m "feat: add Servos test screen"
```

---

### Task 5: Display screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/display.py`
- Test: `IOT-Testing/tests/test_display_screen.py`

**Interfaces:**
- Consumes: `ResultRecorder`, `ask_pass_fail` (Task 1), `milo_bridge.drivers.display.{AnimMode, FaceDisplay}` (existing, unmodified).
- Produces: `DisplayScreen(recorder: ResultRecorder)`, `discover_face_names(assets_dir: Path) -> list[str]`, `ASSETS_DIR: Path` (resolves to `bridge/assets/faces` relative to the repo root).

- [ ] **Step 1: Write the failing tests**

Create `IOT-Testing/tests/test_display_screen.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.display import ASSETS_DIR, DisplayScreen, discover_face_names


def test_discover_face_names_groups_numbered_frames(tmp_path: Path) -> None:
    for name in ["angry.png", "dance_1.png", "dance_2.png", "idle_blink_1.png", "idle_blink_2.png"]:
        (tmp_path / name).write_bytes(b"")
    names = discover_face_names(tmp_path)
    assert names == ["angry", "dance", "idle_blink"]


def test_discover_face_names_on_real_assets_dir() -> None:
    names = discover_face_names(ASSETS_DIR)
    assert "idle" in names
    assert "happy" in names
    assert "walk" in names


def test_display_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = DisplayScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_display_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.display'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/display.py`**

```python
"""Display screen: cycles every face asset on the OLED via FaceDisplay."""

from __future__ import annotations

import re
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.display import AnimMode, FaceDisplay

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

ASSETS_DIR = Path(__file__).resolve().parents[3] / "bridge" / "assets" / "faces"

_FRAME_SUFFIX = re.compile(r"^(.+)_(\d+)$")


def discover_face_names(assets_dir: Path) -> list[str]:
    """Distinct face names in assets_dir, grouping <name>_<n>.png sequences by stem."""
    names: set[str] = set()
    for path in sorted(Path(assets_dir).glob("*.png")):
        stem = path.stem
        match = _FRAME_SUFFIX.match(stem)
        names.add(match.group(1) if match else stem)
    return sorted(names)


class DisplayScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            display = FaceDisplay.from_hardware(ASSETS_DIR)
        except Exception as exc:
            await container.mount(Static(f"Could not open the OLED display: {exc}"))
            return

        for name in discover_face_names(ASSETS_DIR):
            await container.mount(Static(f"Showing face: {name}"))
            await display.set_face(name, AnimMode.ONCE)
            passed, note = await ask_pass_fail(
                container, f"Did '{name}' render correctly on the OLED?"
            )
            self.recorder.record("Display", f"Face: {name}", passed, note)
            self.recorder.flush()

        await container.mount(Static("Showing pairing PIN screen"))
        await display.show_pin("123456")
        passed, note = await ask_pass_fail(container, "Did the pairing-PIN screen render legibly?")
        self.recorder.record("Display", "Pairing PIN render", passed, note)
        self.recorder.flush()

        await container.mount(Static("Display tests complete. Press Escape to return to menu."))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_display_screen.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/display.py IOT-Testing/tests/test_display_screen.py
git commit -m "feat: add Display test screen"
```

---

### Task 6: IMU screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/imu.py`
- Test: `IOT-Testing/tests/test_imu_screen.py`

**Interfaces:**
- Consumes: `ResultRecorder`, `ask_pass_fail` (Task 1), `milo_bridge.drivers.imu.Mpu6050` (existing, unmodified).
- Produces: `ImuScreen(recorder: ResultRecorder)`, `format_readout(roll: float, pitch: float, gyro: tuple[float, float, float]) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `IOT-Testing/tests/test_imu_screen.py`:

```python
from datetime import datetime, timezone

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.imu import ImuScreen, format_readout


def test_format_readout_includes_all_values() -> None:
    text = format_readout(1.5, -2.5, (0.1, 0.2, 0.3))
    assert "1.5" in text
    assert "-2.5" in text
    assert "0.1" in text
    assert "0.2" in text
    assert "0.3" in text


def test_imu_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = ImuScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_imu_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.imu'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/imu.py`**

```python
"""IMU screen: gyro calibration + live roll/pitch/gyro tracking via Mpu6050."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.imu import Mpu6050

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

LIVE_UPDATE_INTERVAL_S = 0.1


def format_readout(roll: float, pitch: float, gyro: tuple[float, float, float]) -> str:
    return (
        f"roll={roll:6.1f} deg  pitch={pitch:6.1f} deg  "
        f"gyro={gyro[0]:6.1f},{gyro[1]:6.1f},{gyro[2]:6.1f} deg/s"
    )


class ImuScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="imu-readout")
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        readout = self.query_one("#imu-readout", Static)
        try:
            imu = await asyncio.to_thread(Mpu6050.from_hardware)
        except Exception as exc:
            await container.mount(Static(f"Could not open the IMU: {exc}"))
            return

        await container.mount(Static("Calibrating gyro -- keep the robot still..."))
        try:
            await asyncio.to_thread(imu.calibrate_gyro)
            self.recorder.record("IMU", "Gyro calibration", True)
        except Exception as exc:
            self.recorder.record("IMU", "Gyro calibration", False, note=str(exc))
        self.recorder.flush()

        await container.mount(
            Static("Live tracking -- tilt the robot forward/back/side-to-side")
        )
        stop_event = asyncio.Event()

        async def update_loop() -> None:
            while not stop_event.is_set():
                state = await asyncio.to_thread(imu.update)
                readout.update(format_readout(state.roll, state.pitch, state.gyro))
                await asyncio.sleep(LIVE_UPDATE_INTERVAL_S)

        updater = asyncio.create_task(update_loop())
        passed, note = await ask_pass_fail(
            container, "Did roll/pitch respond correctly as you tilted the robot?"
        )
        stop_event.set()
        await updater
        self.recorder.record("IMU", "Live tracking", passed, note)
        self.recorder.flush()

        await container.mount(Static("IMU tests complete. Press Escape to return to menu."))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_imu_screen.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/imu.py IOT-Testing/tests/test_imu_screen.py
git commit -m "feat: add IMU test screen"
```

---

### Task 7: Camera screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/camera.py`
- Test: `IOT-Testing/tests/test_camera_screen.py`

**Interfaces:**
- Consumes: `ResultRecorder` (Task 1), `milo_bridge.drivers.camera.CameraStreamer` (existing, unmodified). Does not use `ask_pass_fail` — capture success/failure is determined automatically from whether frames were captured without error (per spec), not by tester judgment.
- Produces: `CameraScreen(recorder: ResultRecorder)`.

This screen has no non-trivial pure logic to extract beyond driver orchestration
(the pass/fail condition is a one-line boolean), so this task adds only the
`compose()` smoke test — consistent with Tasks 1–6, which each test genuinely
extractable logic, not logic that doesn't exist.

- [ ] **Step 1: Write the failing test**

Create `IOT-Testing/tests/test_camera_screen.py`:

```python
from datetime import datetime, timezone

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.camera import CameraScreen


def test_camera_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = CameraScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest IOT-Testing/tests/test_camera_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.camera'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/camera.py`**

```python
"""Camera screen: captures frames via CameraStreamer, saves a snapshot for inspection.

A headless Pi can't preview a JPEG in-terminal, so PASS/FAIL here is scoped to
what the screen can verify automatically: did FRAME_COUNT frames capture
without error. The README tells the tester to scp the saved snapshot down to
check framing/focus/content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.camera import CameraStreamer

from iot_tester.results_log import ResultRecorder

FRAME_COUNT = 3
RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


class CameraScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            camera = CameraStreamer.from_hardware()
        except Exception as exc:
            await container.mount(Static(f"Could not open the camera: {exc}"))
            self.recorder.record("Camera", "Frame capture", False, note=str(exc))
            self.recorder.flush()
            return

        await container.mount(Static(f"Capturing {FRAME_COUNT} frames..."))
        last_frame = b""
        captured = 0
        error_note = ""
        try:
            async for frame in camera.frames():
                if not frame:
                    raise ValueError("captured an empty frame")
                last_frame = frame
                captured += 1
                if captured >= FRAME_COUNT:
                    break
        except Exception as exc:
            error_note = str(exc)

        passed = captured >= FRAME_COUNT and not error_note
        if passed:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            snapshot_path = RESULTS_DIR / f"camera-test-{timestamp}.jpg"
            snapshot_path.write_bytes(last_frame)
            await container.mount(
                Static(
                    f"Captured {captured}/{FRAME_COUNT} frames. Saved {snapshot_path.name} "
                    "-- scp it down to check framing/focus."
                )
            )
        else:
            await container.mount(
                Static(f"Capture failed after {captured}/{FRAME_COUNT} frames: {error_note}")
            )
        self.recorder.record("Camera", "Frame capture", passed, note=error_note)
        self.recorder.flush()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest IOT-Testing/tests/test_camera_screen.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/camera.py IOT-Testing/tests/test_camera_screen.py
git commit -m "feat: add Camera test screen"
```

---

### Task 8: Microphones screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/microphones.py`
- Test: `IOT-Testing/tests/test_microphones_screen.py`

**Interfaces:**
- Consumes: `ResultRecorder`, `ask_pass_fail` (Task 1), `milo_bridge.drivers.audio.{SAMPLE_RATE, AudioIO, rms}` (existing, unmodified).
- Produces: `MicScreen(recorder: ResultRecorder)`, `split_channels(pcm: bytes) -> tuple[bytes, bytes]`, `level_bar(level: float, max_level: float = 4000.0, width: int = 30) -> str`, `save_wav(path: Path, pcm: bytes, channels: int = 2, sample_rate: int = SAMPLE_RATE) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `IOT-Testing/tests/test_microphones_screen.py`:

```python
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.microphones import MicScreen, level_bar, save_wav, split_channels


def test_split_channels_deinterleaves_stereo_pcm() -> None:
    # L=100, R=200, L=101, R=201 (interleaved int16)
    samples = np.array([100, 200, 101, 201], dtype=np.int16)
    left, right = split_channels(samples.tobytes())
    assert np.frombuffer(left, dtype=np.int16).tolist() == [100, 101]
    assert np.frombuffer(right, dtype=np.int16).tolist() == [200, 201]


def test_level_bar_scales_between_0_and_width() -> None:
    assert level_bar(0.0, max_level=4000.0, width=30) == "-" * 30
    assert level_bar(4000.0, max_level=4000.0, width=30) == "#" * 30
    assert level_bar(8000.0, max_level=4000.0, width=30) == "#" * 30  # clamped


def test_save_wav_writes_readable_file(tmp_path: Path) -> None:
    pcm = np.array([0, 100, -100, 200], dtype=np.int16).tobytes()
    wav_path = tmp_path / "test.wav"
    save_wav(wav_path, pcm, channels=2, sample_rate=16_000)
    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == pcm


def test_mic_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = MicScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_microphones_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.microphones'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/microphones.py`**

```python
"""Microphones screen: records via AudioIO, live L/R RMS meter, saves a WAV."""

from __future__ import annotations

import asyncio
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.audio import SAMPLE_RATE, AudioIO, rms

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

RECORD_SECONDS = 3.0
RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


def split_channels(pcm: bytes) -> tuple[bytes, bytes]:
    """Deinterleave stereo s16le PCM into (left, right) mono byte strings."""
    samples = np.frombuffer(pcm, dtype=np.int16)
    left = samples[0::2].tobytes()
    right = samples[1::2].tobytes()
    return left, right


def level_bar(level: float, max_level: float = 4000.0, width: int = 30) -> str:
    """ASCII bar for an RMS level, clamped to [0, width]."""
    filled = min(width, max(0, int(level / max_level * width)))
    return "#" * filled + "-" * (width - filled)


def save_wav(path: Path, pcm: bytes, channels: int = 2, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)


class MicScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="mic-levels")
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        levels = self.query_one("#mic-levels", Static)
        audio = AudioIO()

        await container.mount(
            Static(f"Recording {RECORD_SECONDS:.0f}s -- speak or clap near each mic...")
        )
        chunks: list[bytes] = []
        deadline = asyncio.get_running_loop().time() + RECORD_SECONDS
        try:
            async for frame in audio.capture_frames():
                chunks.append(frame)
                left, right = split_channels(frame)
                levels.update(f"L [{level_bar(rms(left))}]\nR [{level_bar(rms(right))}]")
                if asyncio.get_running_loop().time() >= deadline:
                    break
        except Exception as exc:
            await container.mount(Static(f"Recording failed: {exc}"))
            self.recorder.record("Microphones", "Recording", False, note=str(exc))
            self.recorder.flush()
            return

        pcm = b"".join(chunks)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        wav_path = RESULTS_DIR / f"mic-test-{timestamp}.wav"
        save_wav(wav_path, pcm)
        await container.mount(Static(f"Saved recording to {wav_path.name}"))

        passed, note = await ask_pass_fail(
            container, "Did the level meter respond when you spoke/clapped near each mic?"
        )
        self.recorder.record("Microphones", "Recording", passed, note)
        self.recorder.flush()

        await container.mount(Static("Microphone tests complete. Press Escape to return to menu."))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_microphones_screen.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/microphones.py IOT-Testing/tests/test_microphones_screen.py
git commit -m "feat: add Microphones test screen"
```

---

### Task 9: Speaker screen

**Files:**
- Create: `IOT-Testing/iot_tester/screens/speaker.py`
- Test: `IOT-Testing/tests/test_speaker_screen.py`

**Interfaces:**
- Consumes: `ResultRecorder`, `ask_pass_fail` (Task 1), `milo_bridge.drivers.audio.{SAMPLE_RATE, AudioIO}` (existing, unmodified). No dependency on `microphones.py`.
- Produces: `SpeakerScreen(recorder: ResultRecorder)`, `generate_tone(frequency_hz=440.0, duration_s=1.0, sample_rate=SAMPLE_RATE) -> bytes`.

- [ ] **Step 1: Write the failing tests**

Create `IOT-Testing/tests/test_speaker_screen.py`:

```python
from datetime import datetime, timezone

import numpy as np

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.speaker import SpeakerScreen, generate_tone


def test_generate_tone_has_correct_length() -> None:
    pcm = generate_tone(frequency_hz=440.0, duration_s=1.0, sample_rate=16_000)
    assert len(pcm) == 16_000 * 2  # int16 = 2 bytes/sample, mono


def test_generate_tone_is_not_silent() -> None:
    pcm = generate_tone(frequency_hz=440.0, duration_s=0.1, sample_rate=16_000)
    samples = np.frombuffer(pcm, dtype=np.int16)
    assert samples.max() > 10_000


def test_speaker_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = SpeakerScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_speaker_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.speaker'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/speaker.py`**

```python
"""Speaker screen: plays a generated tone via AudioIO, no dependency on the mic screen."""

from __future__ import annotations

import asyncio

import numpy as np
from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.audio import SAMPLE_RATE, AudioIO

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

TONE_HZ = 440.0
TONE_DURATION_S = 1.0


def generate_tone(
    frequency_hz: float = TONE_HZ, duration_s: float = TONE_DURATION_S, sample_rate: int = SAMPLE_RATE
) -> bytes:
    """Mono s16le PCM sine tone."""
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    tone = np.sin(2 * np.pi * frequency_hz * t)
    pcm = (tone * 32767 * 0.8).astype(np.int16)
    return pcm.tobytes()


class SpeakerScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            audio = AudioIO()
            await container.mount(Static(f"Playing a {TONE_HZ:.0f} Hz test tone..."))
            await asyncio.to_thread(audio.play_pcm, generate_tone())
            audio.close()
        except Exception as exc:
            await container.mount(Static(f"Could not play audio: {exc}"))
            self.recorder.record("Speaker", "Tone playback", False, note=str(exc))
            self.recorder.flush()
            return

        passed, note = await ask_pass_fail(container, "Did you hear a clear tone?")
        self.recorder.record("Speaker", "Tone playback", passed, note)
        self.recorder.flush()

        await container.mount(Static("Speaker test complete. Press Escape to return to menu."))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_speaker_screen.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/speaker.py IOT-Testing/tests/test_speaker_screen.py
git commit -m "feat: add Speaker test screen"
```

---

### Task 10: Results screen, main menu, app entry point, and end-to-end integration test

**Files:**
- Create: `IOT-Testing/iot_tester/screens/results.py`
- Create: `IOT-Testing/iot_tester/app.py`
- Test: `IOT-Testing/tests/test_results_screen.py`
- Test: `IOT-Testing/tests/test_app_integration.py`

**Interfaces:**
- Consumes: every screen class from Tasks 2–9 (`WiringScreen`, `I2cScanScreen`, `ServoScreen`, `DisplayScreen`, `ImuScreen`, `CameraScreen`, `MicScreen`, `SpeakerScreen`), `ResultRecorder` (Task 1).
- Produces: `ResultsScreen(recorder: ResultRecorder)`, `MainMenu` (a `Screen`), `IotTesterApp` (the `textual.app.App` subclass, with a `.recorder: ResultRecorder` attribute set in `__init__`), `main() -> None` (the console-script entry point).

This is the integration point: every screen built in Tasks 2–9 gets wired into
one navigable app, and Step 5 below is the one true `textual` `Pilot`
end-to-end test in this plan — it boots the real app and pushes every real
screen, relying on each screen's `try/except` around its hardware-open call
(established in Tasks 3–9) to fail gracefully on a machine with no Pi
hardware, proving the whole app doesn't crash on any screen.

- [ ] **Step 1: Write the failing test for `ResultsScreen`**

Create `IOT-Testing/tests/test_results_screen.py`:

```python
from datetime import datetime, timezone

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.results import ResultsScreen


def test_results_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    recorder.record("Servo R1", "TC1 Full range sweep", True)
    screen = ResultsScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest IOT-Testing/tests/test_results_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'iot_tester.screens.results'`

- [ ] **Step 3: Implement `IOT-Testing/iot_tester/screens/results.py`**

```python
"""Results screen: view the session's accumulated PASS/FAIL results."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from iot_tester.results_log import ResultRecorder


class ResultsScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield DataTable(id="results-table")
            yield Static("", id="results-summary")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns("Component", "Case", "Result", "Note")
        for result in self.recorder.all_results():
            table.add_row(
                result.component, result.case, "PASS" if result.passed else "FAIL", result.note
            )
        passed, total = self.recorder.summary()
        summary_text = f"{passed}/{total} test cases passed"
        if self.recorder.all_results():
            log_path = self.recorder.flush()
            summary_text += f"\nLog: {log_path}"
        self.query_one("#results-summary", Static).update(summary_text)
```

- [ ] **Step 4: Implement `IOT-Testing/iot_tester/app.py`**

```python
"""Milo IOT-Testing: TUI hardware tester. Entry point: milo-iot-tester."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.camera import CameraScreen
from iot_tester.screens.display import DisplayScreen
from iot_tester.screens.i2c_scan import I2cScanScreen
from iot_tester.screens.imu import ImuScreen
from iot_tester.screens.microphones import MicScreen
from iot_tester.screens.results import ResultsScreen
from iot_tester.screens.servos import ServoScreen
from iot_tester.screens.speaker import SpeakerScreen
from iot_tester.screens.wiring import WiringScreen

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

MENU_ITEMS = [
    ("wiring", "Wiring Reference"),
    ("i2c", "I2C Bus Scan"),
    ("servos", "Servos"),
    ("display", "Display"),
    ("imu", "IMU"),
    ("camera", "Camera"),
    ("mics", "Microphones"),
    ("speaker", "Speaker"),
    ("results", "Results"),
    ("quit", "Quit"),
]


class MainMenu(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(
            *[ListItem(Label(label), id=f"menu-{key}") for key, label in MENU_ITEMS],
            id="main-menu",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        assert event.item.id is not None
        key = event.item.id.removeprefix("menu-")
        app = self.app
        assert isinstance(app, IotTesterApp)
        if key == "wiring":
            app.push_screen(WiringScreen())
        elif key == "i2c":
            app.push_screen(I2cScanScreen(app.recorder))
        elif key == "servos":
            app.push_screen(ServoScreen(app.recorder))
        elif key == "display":
            app.push_screen(DisplayScreen(app.recorder))
        elif key == "imu":
            app.push_screen(ImuScreen(app.recorder))
        elif key == "camera":
            app.push_screen(CameraScreen(app.recorder))
        elif key == "mics":
            app.push_screen(MicScreen(app.recorder))
        elif key == "speaker":
            app.push_screen(SpeakerScreen(app.recorder))
        elif key == "results":
            app.push_screen(ResultsScreen(app.recorder))
        elif key == "quit":
            app.exit()


class IotTesterApp(App):
    TITLE = "MILO IOT-Testing"

    def __init__(self) -> None:
        super().__init__()
        self.recorder = ResultRecorder(RESULTS_DIR, datetime.now(timezone.utc))

    def on_mount(self) -> None:
        self.push_screen(MainMenu())


def main() -> None:
    IotTesterApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Write the end-to-end integration test**

Create `IOT-Testing/tests/test_app_integration.py`:

```python
from iot_tester.app import IotTesterApp, MainMenu
from iot_tester.screens.camera import CameraScreen
from iot_tester.screens.display import DisplayScreen
from iot_tester.screens.i2c_scan import I2cScanScreen
from iot_tester.screens.imu import ImuScreen
from iot_tester.screens.microphones import MicScreen
from iot_tester.screens.results import ResultsScreen
from iot_tester.screens.servos import ServoScreen
from iot_tester.screens.speaker import SpeakerScreen
from iot_tester.screens.wiring import WiringScreen


async def test_app_boots_to_main_menu() -> None:
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainMenu)


async def test_every_screen_pushes_and_pops_without_crashing() -> None:
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screens = [
            WiringScreen(),
            I2cScanScreen(app.recorder),
            ServoScreen(app.recorder),
            DisplayScreen(app.recorder),
            ImuScreen(app.recorder),
            CameraScreen(app.recorder),
            MicScreen(app.recorder),
            SpeakerScreen(app.recorder),
            ResultsScreen(app.recorder),
        ]
        for screen in screens:
            app.push_screen(screen)
            await pilot.pause()
            assert app.screen is screen
            app.pop_screen()
            await pilot.pause()
        assert isinstance(app.screen, MainMenu)
```

Note: on this development machine (no Pi hardware attached), pushing
`ServoScreen`/`DisplayScreen`/`ImuScreen`/`CameraScreen`/`MicScreen`/
`SpeakerScreen` triggers their `run_tests()` worker, which immediately hits
the `try/except` around its `from_hardware()` (or first hardware call) and
shows a friendly error `Static` — this is expected and is exactly the
graceful-degradation behavior Tasks 3–9 built. The test only asserts the
screen mounted and the app didn't crash, not that hardware actually worked.

- [ ] **Step 6: Run all tests to verify they pass**

Run: `pytest IOT-Testing/tests/ -v`
Expected: all tests across every task pass (roughly 33 tests total)

- [ ] **Step 7: Commit**

```bash
git add IOT-Testing/iot_tester/screens/results.py IOT-Testing/iot_tester/app.py \
        IOT-Testing/tests/test_results_screen.py IOT-Testing/tests/test_app_integration.py
git commit -m "feat: wire all screens into the main menu app; add end-to-end integration test"
```

---

### Task 11: A-Z README

**Files:**
- Create: `IOT-Testing/README.md`

**Interfaces:**
- Consumes: nothing (documentation only) — but must describe the actual `milo-iot-tester` CLI, menu items, and file layout exactly as built in Tasks 1–10 (verified in Step 2 below).

- [ ] **Step 1: Write `IOT-Testing/README.md`**

```markdown
# IOT-Testing — Milo Hardware Tester

A Textual-based TUI for validating every sensor and actuator on Milo — servos,
OLED display, IMU, camera, microphones, speaker — over SSH, plus an I2C bus
scan and an in-app wiring reference. It drives the real `milo_bridge` hardware
drivers, so a passing run here reflects the same code path the robot uses in
production.

Wiring reference: [`PINOUT.md`](PINOUT.md) (same facts, also available inside
the app via the "Wiring Reference" menu item).

## Prerequisites

- [ ] All sensors wired per [`PINOUT.md`](PINOUT.md) / `docs/ARCHITECTURE.md` §5.
- [ ] Servo power (PCA9685 V+) comes from the battery/5A buck rail — **never**
      the Pi's own 5V.
- [ ] The robot is on a stand or otherwise supported so its legs can move
      through a full sweep without hitting anything.
- [ ] Raspberry Pi OS is flashed with SSH enabled (see
      `docs/GETTING-STARTED.md` if you haven't done this yet).

## A-Z: running the tester

### 1. Enable I2C and I2S on the Pi

```bash
sudo raspi-config nonint do_i2c 0
```

Edit `/boot/firmware/config.txt`, add:

```ini
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Reboot.

### 2. Install system packages

```bash
sudo apt update
sudo apt install -y i2c-tools python3-venv python3-picamera2 git
```

`python3-picamera2` must come from `apt` (not pip) — this is why the venv in
the next step uses `--system-site-packages`.

### 3. Verify the I2C devices are detected

```bash
i2cdetect -y 1
```

Expect `3c`, `40`, and `68` in the grid (OLED, PCA9685, MPU6050). If any are
missing, check wiring before going further — the tester's I2C Bus Scan screen
will show the same gaps.

### 4. SSH into the Pi and get the code

```bash
ssh <your-pi-username>@milo.local
cd MILO-Robot   # or: git clone <repo-url> MILO-Robot && cd MILO-Robot
git pull
```

### 5. Set up the environment

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e ./common
pip install -e "./bridge[pi]"
pip install -e ./IOT-Testing
```

### 6. Launch the tester

```bash
milo-iot-tester
```

### 7. Navigate the menu

Arrow keys + Enter (or click) to select: **Wiring Reference**, **I2C Bus
Scan**, **Servos**, **Display**, **IMU**, **Camera**, **Microphones**,
**Speaker**, **Results**, **Quit**. `Escape` returns to the menu from any
screen. Interactive screens (Servos, Display, IMU, Microphones, Speaker) show
PASS/FAIL buttons after each test case — click FAIL to reveal a note field for
what went wrong.

- **Servos**: click "Start Servo Tests" after reading the safety banner. Each
  of the 8 servos runs a full 0→180° sweep (TC1) then returns to 0° (TC2).
- **Display**: cycles every face asset automatically; confirm each renders,
  plus the pairing-PIN screen.
- **IMU**: calibrates the gyro automatically (keep the robot still), then
  shows a live roll/pitch/gyro readout — tilt the robot and confirm it
  tracks before answering PASS/FAIL.
- **Camera**: captures 3 frames automatically; PASS/FAIL is automatic
  (did capture succeed) — the screen tells you the saved snapshot's filename.
- **Microphones**: records ~3 seconds with a live L/R level meter; speak or
  clap near each mic, then confirm the meter responded.
- **Speaker**: plays a 440 Hz test tone automatically; confirm you heard it.

### 8. Check results

Every result is appended to `IOT-Testing/results/session-<timestamp>.log` as
you go (so nothing is lost if you exit early), and summarized in the app's
**Results** screen. Camera snapshots (`camera-test-*.jpg`) and mic recordings
(`mic-test-*.wav`) also land in `IOT-Testing/results/` — `scp` them down to a
machine with a screen/speakers to verify content (a headless Pi terminal
can't preview images or play audio you can hear remotely).

```bash
scp <pi-username>@milo.local:MILO-Robot/IOT-Testing/results/camera-test-*.jpg .
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A screen immediately shows "Could not open the ..." | The relevant device isn't wired/powered, or `pip install -e "./bridge[pi]"` didn't complete — re-run `i2cdetect -y 1` / `arecord -l` and check the wiring in `PINOUT.md`. |
| Servo doesn't move at all | Check its signal wire is in the channel the screen says it's testing; check PCA9685 V+ has power. |
| Servo jitters or resets mid-sweep | Usually the 5A rail sagging — confirm servos are on battery power, not Pi 5V, and check battery charge. |
| Wrong leg moves for a given servo name | Channel wiring doesn't match the map in `PINOUT.md`. Re-seat the connector on the correct PCA9685 channel. |
| No sound from mics or speaker | Confirm `dtoverlay=googlevoicehat-soundcard` is in `/boot/firmware/config.txt` and you rebooted; `arecord -l` should list a capture device. |
| `ModuleNotFoundError: No module named 'picamera2'` | Recreate the venv with `--system-site-packages` (Step 5) — `picamera2` is apt-only. |
| Camera screen fails immediately | `rpicam-hello --list-cameras` should show the IMX219; check the CSI ribbon is fully seated (15→22-pin adapter). |

## Safety reminders

- Servos draw from the battery/5A buck rail only. Powering them from the Pi's
  5V rail can brown out the Pi mid-test.
- Keep the robot supported on a stand during servo tests — legs move through
  their entire range.
- Common ground between every rail, the Pi, and every breakout (see
  `PINOUT.md`).
```

- [ ] **Step 2: Verify the README matches what was actually built**

Read through `IOT-Testing/README.md` against `IOT-Testing/iot_tester/app.py`'s
`MENU_ITEMS` list and confirm every menu item name in the README ("Wiring
Reference", "I2C Bus Scan", "Servos", "Display", "IMU", "Camera",
"Microphones", "Speaker", "Results", "Quit") matches exactly. Confirm the
install commands (`pip install -e ./common`, `pip install -e "./bridge[pi]"`,
`pip install -e ./IOT-Testing`) match `IOT-Testing/pyproject.toml`'s
`dependencies` and the launch command `milo-iot-tester` matches
`[project.scripts]`.

- [ ] **Step 3: Commit**

```bash
git add IOT-Testing/README.md
git commit -m "docs: add A-Z README for milo-iot-tester"
```

---

### Task 12: Final verification and push

**Files:** none (verification + push only)

**Interfaces:** none — confirms Tasks 1–11's outputs are consistent and publishes them.

- [ ] **Step 1: Confirm the full file tree**

Run (from repo root): `find IOT-Testing -type f | sort`

Expected (paths, order may vary by tool but all must be present):
```
IOT-Testing/PINOUT.md
IOT-Testing/README.md
IOT-Testing/iot_tester/__init__.py
IOT-Testing/iot_tester/app.py
IOT-Testing/iot_tester/results_log.py
IOT-Testing/iot_tester/screens/__init__.py
IOT-Testing/iot_tester/screens/camera.py
IOT-Testing/iot_tester/screens/display.py
IOT-Testing/iot_tester/screens/i2c_scan.py
IOT-Testing/iot_tester/screens/imu.py
IOT-Testing/iot_tester/screens/microphones.py
IOT-Testing/iot_tester/screens/results.py
IOT-Testing/iot_tester/screens/servos.py
IOT-Testing/iot_tester/screens/speaker.py
IOT-Testing/iot_tester/screens/wiring.py
IOT-Testing/iot_tester/widgets.py
IOT-Testing/pyproject.toml
IOT-Testing/results/.gitkeep
IOT-Testing/tests/test_app_integration.py
IOT-Testing/tests/test_camera_screen.py
IOT-Testing/tests/test_display_screen.py
IOT-Testing/tests/test_i2c_scan.py
IOT-Testing/tests/test_imu_screen.py
IOT-Testing/tests/test_microphones_screen.py
IOT-Testing/tests/test_results_log.py
IOT-Testing/tests/test_results_screen.py
IOT-Testing/tests/test_servos_screen.py
IOT-Testing/tests/test_speaker_screen.py
IOT-Testing/tests/test_widgets.py
IOT-Testing/tests/test_wiring.py
```

- [ ] **Step 2: Run the full test suite one more time as a sanity pass**

Run: `pytest IOT-Testing/tests/ -v`
Expected: all tests pass, 0 failures.

- [ ] **Step 3: Confirm `milo-iot-tester` is importable and its entry point resolves**

Run: `python -c "from iot_tester.app import main; print(main)"`
Expected: prints `<function main at 0x...>`, no import errors (confirms every
screen module imports cleanly without requiring any Pi-only hardware library
at import time).

- [ ] **Step 4: Confirm git status is clean and the log is correct**

Run: `git status` (expect clean tree, all commits from Tasks 1–11 present) and
`git log --oneline -15`.

- [ ] **Step 5: Push to the remote**

```bash
git push -u origin worktree-iot-testing-servo-group
```

Expected: push succeeds. This branch is what gets pulled onto the robot's Pi
over SSH for the actual hardware run — merging to `main` is a separate,
later step once the tester has been run against real hardware.
