# IOT-Testing — Manual Control Panels for Servos and Display

**Date:** 2026-07-12
**Status:** Approved
**Supersedes (partially):** the Servos and Display sections of
`2026-07-12-iot-testing-tui-design.md`. That spec's other screens (Wiring,
I2C Scan, IMU, Camera, Microphones, Speaker, Results, main menu) are
unaffected and stay exactly as built.

## Purpose

The original Servos and Display screens automated a fixed test sequence
(sweep every servo through a scripted set of angles, cycle every face asset)
and asked the tester to judge PASS/FAIL after each step. In practice, hands-on
bring-up work is better served by direct manual control: a panel of buttons
the tester presses to jog a specific servo (MG90S, 0–180°) to a specific angle,
or to trigger a specific facial expression on demand, watching the result
directly rather than answering a scripted verdict. This spec replaces the
automated-sequence-plus-verdict design for these two screens with manual
control panels. No other screen changes.

## Scope

In scope: `IOT-Testing/iot_tester/screens/servos.py`,
`IOT-Testing/iot_tester/screens/display.py`, their test files, and the two
call sites in `IOT-Testing/iot_tester/app.py`'s `MainMenu.on_list_view_selected`
that construct them.

Out of scope: `wiring.py`, `i2c_scan.py`, `imu.py`, `camera.py`,
`microphones.py`, `speaker.py`, `results.py`, `results_log.py`, `widgets.py`
(the shared `PassFailPrompt`/`ask_pass_fail` machinery stays as-is for the
screens that still use it — nothing here removes or changes that shared
code, these two screens simply stop calling it). No changes to
`bridge/milo_bridge/drivers/{servos,display}.py`.

## Servos screen

**Constructor:** `ServoScreen()` — zero arguments. (Previously took
`recorder: ResultRecorder`; no longer needed since this screen produces no
PASS/FAIL results.) `app.py`'s call site changes from `ServoScreen(app.recorder)`
to `ServoScreen()`, matching `WiringScreen()`'s existing pattern.

**Layout:**
1. The existing safety banner text, unchanged: *"Servos must be powered from
   the battery/5A rail, NEVER the Pi's 5V. Keep the robot on a stand, clear
   of obstructions."*
2. A **Connect** button. Disabled after first successful press. Pressing it
   calls `ServoDriver.from_hardware()` inside the existing
   `try/except Exception` pattern (friendly `"Could not open the PCA9685: {exc}"`
   message on failure, button re-enabled so the tester can retry after fixing
   wiring).
3. Once connected: one row per servo, in `SERVO_CHANNELS` order (R1, R2, L1,
   L2, R4, R3, L3, L4), each showing:
   - The servo name and channel (e.g. "R1 (channel 0)").
   - Five buttons: **0°**, **45°**, **90°**, **135°**, **180°**.
   - A label showing the last angle commanded to that servo (initially "—").
4. A single **Relax All** button at the bottom, calling `driver.relax()`.

**Behavior:** pressing an angle button calls `driver.set_angle(name, angle)`
directly — a single immediate move, no stepped/staggered sweep (the earlier
automated full-range sweep stepped through intermediate angles for pacing
during an unattended scripted test; a single tester-initiated move to a named
angle doesn't need that) — and updates that servo's "last angle" label to the
pressed value. No `ResultRecorder`, no `ask_pass_fail`, no session-log entries
from this screen.

## Display screen

**Constructor:** `DisplayScreen()` — zero arguments (same reasoning as
`ServoScreen`). `app.py`'s call site changes from `DisplayScreen(app.recorder)`
to `DisplayScreen()`.

**Layout:**
1. A **Connect** button. Pressing it calls `FaceDisplay.from_hardware(ASSETS_DIR)`
   inside the existing `try/except Exception` pattern (friendly
   `"Could not open the OLED display: {exc}"` message, button re-enabled on
   failure so the tester can retry).
2. Once connected: a curated set of emote buttons — **idle, happy, angry,
   sad, excited, sleepy, wave, dance** — plus a **Show Pairing PIN** button.
   These are a fixed list (not `discover_face_names()`-driven — the earlier
   automated cycling screen discovered every asset because it needed to test
   all ~35 systematically; a manual panel is deliberately a curated subset per
   the approved design, so `discover_face_names()` and its dynamic-discovery
   behavior are no longer used by this screen). Each of the 8 emote names must
   exist in `bridge/assets/faces/` (verified against the real asset directory
   during implementation, not assumed).
3. Pressing an emote button calls `await display.set_face(name, AnimMode.ONCE)`.
   Pressing **Show Pairing PIN** calls `await display.show_pin("123456")`. No
   `ResultRecorder`, no `ask_pass_fail`, no session-log entries from this screen.

## `app.py` changes

`MainMenu.on_list_view_selected`: change the `"servos"` branch to
`app.push_screen(ServoScreen())` and the `"display"` branch to
`app.push_screen(DisplayScreen())`. No other menu entries change.

## Testing

Both screens' hardware calls stay behind `try/except Exception` exactly as
established elsewhere in this codebase, so they remain testable off-Pi: a
`compose()`-smoke test (constructor + `list(screen.compose())` doesn't raise),
plus — since button-driven single-action behavior is straightforward to drive
through a real Textual `Pilot` — an app-level test that connects (with the
try/except naturally catching the missing-hardware `ImportError` on this dev
machine) and confirms button presence, rather than asserting on actual servo
motion or OLED rendering (which requires real hardware). The existing
`test_app_integration.py` menu-routing test continues to cover reachability
from the main menu; update its screen-construction calls to the new zero-arg
constructors.

## README

`IOT-Testing/README.md`'s per-screen description for Servos and Display
should be updated to describe the new Connect + button-panel interaction
instead of the old "click Start, answer PASS/FAIL per case" flow.
