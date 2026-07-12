# IOT-Testing Manual Control Panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Servos and Display screens' automated-sequence-plus-PASS/FAIL flow with manual control panels: per-servo angle-jog buttons (0/45/90/135/180°) for all 8 MG90S servos, and a curated emote-button panel for the OLED display — no verdicts, no session-log entries from either screen.

**Architecture:** Both screens keep the existing `Connect` → `try/except Exception` around the driver's `from_hardware()` call pattern already established elsewhere in this codebase, but replace the scripted `run_tests()` worker with a button grid built once after a successful connect. Button presses call the driver directly (`set_angle`/`relax` for servos, `set_face`/`show_pin` for display) with no `ResultRecorder`/`ask_pass_fail` involved.

**Tech Stack:** Python 3.11+, Textual (already a dependency), the existing `milo_bridge.drivers.servos.ServoDriver` and `milo_bridge.drivers.display.FaceDisplay` (unmodified).

## Global Constraints

- `ServoScreen()` and `DisplayScreen()` become zero-argument constructors (no more `recorder: ResultRecorder` parameter) — every call site must be updated to match.
- Servo channel map unchanged: `R1=0, R2=1, L1=2, L2=3, R4=4, R3=5, L3=6, L4=7`. Angle buttons: `0°, 45°, 90°, 135°, 180°` per servo.
- Curated emote list: `idle, happy, angry, sad, excited, sleepy, wave, dance` — all verified to have real assets in `bridge/assets/faces/`. Plus a `Show Pairing PIN` button.
- Every hardware-open call (`ServoDriver.from_hardware()`, `FaceDisplay.from_hardware(ASSETS_DIR)`) stays wrapped in `try/except Exception` with a friendly message, exactly as established elsewhere — this is what keeps the app testable off-Pi.
- No modifications to `bridge/`, `common/`, `brain/`, `training/`, `results_log.py`, or `widgets.py`. No changes to any other screen (`wiring.py`, `i2c_scan.py`, `imu.py`, `camera.py`, `microphones.py`, `speaker.py`, `results.py`).
- `IOT-Testing/iot_tester/app.py`'s `MainMenu.on_list_view_selected` must construct `ServoScreen()` and `DisplayScreen()` with no arguments.

---

### Task 1: Rewrite the Servos screen as a manual jog panel

**Files:**
- Modify: `IOT-Testing/iot_tester/screens/servos.py` (full rewrite)
- Modify: `IOT-Testing/tests/test_servos_screen.py` (full rewrite)

**Interfaces:**
- Consumes: `SERVO_CHANNELS`, `ServoDriver` from `milo_bridge.drivers.servos` (unmodified, existing).
- Produces: `ServoScreen()` (zero-arg constructor), `ANGLES = (0, 45, 90, 135, 180)`, `angle_button_id(name: str, angle: int) -> str`, `parse_angle_button_id(button_id: str) -> tuple[str, int]` — Task 3 does not need these directly but must know `ServoScreen()` takes no arguments.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `IOT-Testing/tests/test_servos_screen.py` with:

```python
from iot_tester.app import IotTesterApp
from iot_tester.screens.servos import (
    ANGLES,
    ServoScreen,
    angle_button_id,
    parse_angle_button_id,
)


def test_angle_button_id_round_trips_for_every_servo_and_angle() -> None:
    for name in ("R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4"):
        for angle in ANGLES:
            button_id = angle_button_id(name, angle)
            assert parse_angle_button_id(button_id) == (name, angle)


def test_angle_button_id_format() -> None:
    assert angle_button_id("R1", 45) == "angle-R1-45"


def test_servo_screen_composes_without_error() -> None:
    screen = ServoScreen()
    widgets = list(screen.compose())
    assert len(widgets) > 0


async def test_connect_button_shows_friendly_error_without_hardware() -> None:
    """On this dev machine there's no PCA9685/adafruit-blinka, so clicking
    Connect must hit the try/except and show a friendly message instead of
    crashing -- the same graceful-degradation behavior every other screen's
    hardware-open call already has."""
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(ServoScreen())
        await pilot.pause()
        await pilot.click("#connect-btn")
        await pilot.pause()
        panel = app.screen.query_one("#panel-area")
        texts = [str(s.renderable) for s in panel.query("Static")]
        assert any("Could not open the PCA9685" in t for t in texts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_servos_screen.py -v`
Expected: FAIL — `ImportError: cannot import name 'ANGLES' from 'iot_tester.screens.servos'` (the old module doesn't define these yet)

- [ ] **Step 3: Replace the full contents of `IOT-Testing/iot_tester/screens/servos.py`**

```python
"""Servos screen: manual jog panel for all 8 MG90S servos (0-180 degrees)."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from milo_bridge.drivers.servos import SERVO_CHANNELS, ServoDriver

ANGLES = (0, 45, 90, 135, 180)


def angle_button_id(name: str, angle: int) -> str:
    return f"angle-{name}-{angle}"


def parse_angle_button_id(button_id: str) -> tuple[str, int]:
    """'angle-R1-45' -> ('R1', 45)"""
    _, name, angle_str = button_id.split("-", 2)
    return name, int(angle_str)


class ServoScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self) -> None:
        super().__init__()
        self._driver: ServoDriver | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Servos must be powered from the battery/5A rail, NEVER the Pi's 5V. "
            "Keep the robot on a stand, clear of obstructions.",
            classes="warning",
        )
        yield Button("Connect", id="connect-btn", variant="primary")
        yield VerticalScroll(id="panel-area")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "connect-btn":
            event.button.disabled = True
            self.connect()
        elif button_id == "relax-btn":
            if self._driver is not None:
                self._driver.relax()
        elif button_id.startswith("angle-"):
            self._set_angle_from_button(button_id)

    @work()
    async def connect(self) -> None:
        panel = self.query_one("#panel-area", VerticalScroll)
        try:
            self._driver = ServoDriver.from_hardware()
        except Exception as exc:
            await panel.mount(Static(f"Could not open the PCA9685: {exc}"))
            self.query_one("#connect-btn", Button).disabled = False
            return
        await self._build_panel(panel)

    async def _build_panel(self, panel: VerticalScroll) -> None:
        for name in SERVO_CHANNELS:
            channel = SERVO_CHANNELS[name]
            row_widgets: list[Static | Button | Label] = [
                Static(f"{name} (channel {channel})", classes="servo-name")
            ]
            for angle in ANGLES:
                row_widgets.append(
                    Button(
                        f"{angle}°", id=angle_button_id(name, angle), classes="angle-btn"
                    )
                )
            row_widgets.append(Label("last angle: --", id=f"label-{name}"))
            await panel.mount(Horizontal(*row_widgets, classes="servo-row"))
        await panel.mount(Button("Relax All", id="relax-btn", variant="error"))

    def _set_angle_from_button(self, button_id: str) -> None:
        if self._driver is None:
            return
        name, angle = parse_angle_button_id(button_id)
        self._driver.set_angle(name, angle)
        self.query_one(f"#label-{name}", Label).update(f"last angle: {angle}°")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_servos_screen.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/servos.py IOT-Testing/tests/test_servos_screen.py
git commit -m "feat: replace Servos screen with a manual per-servo angle jog panel"
```

---

### Task 2: Rewrite the Display screen as a manual emote panel

**Files:**
- Modify: `IOT-Testing/iot_tester/screens/display.py` (full rewrite)
- Modify: `IOT-Testing/tests/test_display_screen.py` (full rewrite)

**Interfaces:**
- Consumes: `AnimMode`, `FaceDisplay` from `milo_bridge.drivers.display` (unmodified, existing).
- Produces: `DisplayScreen()` (zero-arg constructor), `EMOTES = ("idle", "happy", "angry", "sad", "excited", "sleepy", "wave", "dance")`, `ASSETS_DIR: Path` — Task 3 does not need these directly but must know `DisplayScreen()` takes no arguments.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `IOT-Testing/tests/test_display_screen.py` with:

```python
from iot_tester.app import IotTesterApp
from iot_tester.screens.display import ASSETS_DIR, EMOTES, DisplayScreen


def test_every_emote_has_a_face_asset() -> None:
    for name in EMOTES:
        single = ASSETS_DIR / f"{name}.png"
        multi = list(ASSETS_DIR.glob(f"{name}_*.png"))
        assert single.exists() or multi, f"no asset found for emote {name!r}"


def test_display_screen_composes_without_error() -> None:
    screen = DisplayScreen()
    widgets = list(screen.compose())
    assert len(widgets) > 0


async def test_connect_button_shows_friendly_error_without_hardware() -> None:
    """On this dev machine there's no luma.oled, so clicking Connect must hit
    the try/except and show a friendly message instead of crashing."""
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(DisplayScreen())
        await pilot.pause()
        await pilot.click("#connect-btn")
        await pilot.pause()
        panel = app.screen.query_one("#panel-area")
        texts = [str(s.renderable) for s in panel.query("Static")]
        assert any("Could not open the OLED display" in t for t in texts)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest IOT-Testing/tests/test_display_screen.py -v`
Expected: FAIL — `ImportError: cannot import name 'EMOTES' from 'iot_tester.screens.display'`

- [ ] **Step 3: Replace the full contents of `IOT-Testing/iot_tester/screens/display.py`**

```python
"""Display screen: manual emote panel -- curated face buttons + pairing PIN."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from milo_bridge.drivers.display import AnimMode, FaceDisplay

ASSETS_DIR = Path(__file__).resolve().parents[3] / "bridge" / "assets" / "faces"

EMOTES = ("idle", "happy", "angry", "sad", "excited", "sleepy", "wave", "dance")


class DisplayScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self) -> None:
        super().__init__()
        self._display: FaceDisplay | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Button("Connect", id="connect-btn", variant="primary")
        yield VerticalScroll(id="panel-area")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "connect-btn":
            event.button.disabled = True
            self.connect()
        elif button_id == "pin-btn":
            self.show_pin()
        elif button_id.startswith("emote-"):
            self.show_emote(button_id.removeprefix("emote-"))

    @work()
    async def connect(self) -> None:
        panel = self.query_one("#panel-area", VerticalScroll)
        try:
            self._display = FaceDisplay.from_hardware(ASSETS_DIR)
        except Exception as exc:
            await panel.mount(Static(f"Could not open the OLED display: {exc}"))
            self.query_one("#connect-btn", Button).disabled = False
            return
        buttons: list[Button] = [
            Button(name, id=f"emote-{name}", classes="emote-btn") for name in EMOTES
        ]
        buttons.append(Button("Show Pairing PIN", id="pin-btn", variant="primary"))
        await panel.mount(Horizontal(*buttons, classes="emote-row"))

    @work()
    async def show_emote(self, name: str) -> None:
        if self._display is not None:
            await self._display.set_face(name, AnimMode.ONCE)

    @work()
    async def show_pin(self) -> None:
        if self._display is not None:
            await self._display.show_pin("123456")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest IOT-Testing/tests/test_display_screen.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/screens/display.py IOT-Testing/tests/test_display_screen.py
git commit -m "feat: replace Display screen with a manual emote button panel"
```

---

### Task 3: Wire up the new constructors, update integration test and README, verify and push

**Files:**
- Modify: `IOT-Testing/iot_tester/app.py`
- Modify: `IOT-Testing/tests/test_app_integration.py`
- Modify: `IOT-Testing/README.md`

**Interfaces:**
- Consumes: `ServoScreen()` and `DisplayScreen()` zero-arg constructors from Tasks 1 and 2.

- [ ] **Step 1: Update `IOT-Testing/iot_tester/app.py`'s menu routing**

In `MainMenu.on_list_view_selected`, change:

```python
        elif key == "servos":
            app.push_screen(ServoScreen(app.recorder))
        elif key == "display":
            app.push_screen(DisplayScreen(app.recorder))
```

to:

```python
        elif key == "servos":
            app.push_screen(ServoScreen())
        elif key == "display":
            app.push_screen(DisplayScreen())
```

- [ ] **Step 2: Update `IOT-Testing/tests/test_app_integration.py`'s screen-construction calls**

In `test_every_screen_pushes_and_pops_without_crashing`, change:

```python
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
```

to:

```python
        screens = [
            WiringScreen(),
            I2cScanScreen(app.recorder),
            ServoScreen(),
            DisplayScreen(),
            ImuScreen(app.recorder),
            CameraScreen(app.recorder),
            MicScreen(app.recorder),
            SpeakerScreen(app.recorder),
            ResultsScreen(app.recorder),
        ]
```

In `test_menu_selection_routes_to_correct_screen`, the `key_to_screen` list already maps `("servos", ServoScreen)` and `("display", DisplayScreen)` by class, not by constructor call — no change needed there, but re-read the test after Step 1's `app.py` change to confirm the assertions (`isinstance(app.screen, ServoScreen)` / `isinstance(app.screen, DisplayScreen)`) still hold with the new zero-arg construction. They will, since `isinstance` doesn't care about constructor arguments.

- [ ] **Step 3: Run the full test suite**

Run: `pytest IOT-Testing/tests/ -v`
Expected: all tests pass (the two rewritten screen test files plus every other existing test file — should be roughly 33-35 tests: 37 previous minus the removed old servo/display tests plus the new ones from Tasks 1-2)

- [ ] **Step 4: Update `IOT-Testing/README.md`'s Servos and Display sections**

Find the bullet points under `### 7. Navigate the menu` that describe **Servos** and **Display**. Replace:

```markdown
- **Servos**: click "Start Servo Tests" after reading the safety banner. Each
  of the 8 servos runs a full 0→180° sweep (TC1) then returns to 0° (TC2).
- **Display**: cycles every face asset automatically; confirm each renders,
  plus the pairing-PIN screen.
```

with:

```markdown
- **Servos**: click "Connect" after reading the safety banner. Once
  connected, each of the 8 servos gets its own row with 0°/45°/90°/135°/180°
  buttons — press one to jog that servo to that angle and watch it move.
  "Relax All" de-energizes every channel when you're done.
- **Display**: click "Connect", then press any emote button (idle, happy,
  angry, sad, excited, sleepy, wave, dance) to show that face immediately, or
  "Show Pairing PIN" to render the pairing-PIN screen.
```

- [ ] **Step 5: Commit**

```bash
git add IOT-Testing/iot_tester/app.py IOT-Testing/tests/test_app_integration.py IOT-Testing/README.md
git commit -m "chore: wire manual control panels into the menu; update README and integration test"
```

- [ ] **Step 6: Final sanity pass and push**

Run: `pytest IOT-Testing/tests/ -v` one more time — confirm 0 failures.
Run: `python -c "from iot_tester.app import main; print(main)"` — confirm no import errors.
Run: `git status` — confirm clean tree.
Run: `git push`

## Self-Review

- **Spec coverage:** Servos manual jog panel (Task 1), Display manual emote panel (Task 2), zero-arg constructors wired into `app.py` and the integration test (Task 3), README updated (Task 3) — every section of the spec has a task.
- **Placeholder scan:** no TBD/TODO; all code blocks are complete, runnable replacements.
- **Type consistency:** `ServoScreen()`/`DisplayScreen()` zero-arg constructors used identically in Task 3's `app.py` and test edits as defined in Tasks 1-2. `angle_button_id`/`parse_angle_button_id` signatures match their round-trip test. `EMOTES`/`ASSETS_DIR` match their asset-existence test.
