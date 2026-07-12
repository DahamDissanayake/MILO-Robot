# IOT-Testing — Group 01: Pi + PCA9685 + Servos

**Date:** 2026-07-12
**Status:** Approved

## Purpose

Milo's hardware needs standalone, on-Pi bring-up/regression tests that can be run
over SSH independent of the `milo-bridge` software stack — useful the moment the
electronics are wired, before any application code is installed, and again any time
a servo or connection is suspect. This spec covers the first test group: the
Raspberry Pi's I2C link to the PCA9685 and all 8 MG90 servos.

`IOT-Testing/` is a new top-level folder, organized into numbered groups so future
hardware (OLED display, IMU, camera, mic/speaker) can each get their own
self-contained test group without restructuring what exists today.

## Scope

In scope:
- `IOT-Testing/README.md` — top-level index of test groups.
- `IOT-Testing/01-pi-pca9685-servos/` — the servo test group: script, README,
  requirements, results folder.

Out of scope (future groups, not built now): OLED display, IMU, camera, mic/speaker.
These are noted as planned in the top-level index only.

## Structure

```
IOT-Testing/
  README.md
  01-pi-pca9685-servos/
    test_servos.py
    requirements.txt
    README.md
    results/.gitkeep
```

## `test_servos.py`

**Dependency model:** standalone. Talks to the PCA9685 directly via `board`,
`busio`, and `adafruit_pca9685` (the same libraries `ServoDriver.from_hardware()`
uses in `bridge/milo_bridge/drivers/servos.py`). No dependency on the `milo_bridge`
package — this script must run on a bare Pi before the bridge is installed.

**Constants** (mirrored from `bridge/milo_bridge/drivers/servos.py` so results are
meaningful against production wiring, but not imported — this script has zero
project-package dependencies):
- `PCA9685_ADDRESS = 0x40`
- `PWM_FREQUENCY_HZ = 50`
- `PULSE_MIN_US = 500`, `PULSE_MAX_US = 2500`
- `SERVO_CHANNELS = {"R1": 0, "R2": 1, "L1": 2, "L2": 3, "R4": 4, "R3": 5, "L3": 6, "L4": 7}`

**Startup:**
1. Print a safety banner: confirm servos are powered from the battery/5A buck rail
   (never the Pi's 5V rail), and the robot is on a stand clear of obstructions.
2. Wait for Enter to continue.
3. Initialize I2C + PCA9685. On failure (device not found), print a friendly error
   pointing at `i2cdetect -y 1` and exit — do not stack-trace at the tester.

**Per-servo test loop** (one servo at a time, in channel order R1, R2, L1, L2, R4,
R3, L3, L4 — unless `--servo NAME` restricts to one):

- Print header: `Servo R1 (channel 0)`.
- **TC1 — Full range sweep:** step the angle 0°→45°→90°→135°→180°, pausing briefly
  at each step (stepped motion, not an instant jump — avoids gear shock / current
  spike). Prompt: `Did R1 sweep smoothly through its full range? (y/n)`. On `n`,
  prompt for a one-line failure note.
- **TC2 — Return to zero:** step back down 180°→90°→0°. Prompt:
  `Did R1 return cleanly to 0°? (y/n)`. On `n`, prompt for a one-line note.
- Record both results (PASS/FAIL + optional note) in memory for the log.

**Shutdown:**
- Relax all channels (`duty_cycle = 0`) so nothing holds torque or heats up
  unattended after the run.
- Print a summary table: per-servo TC1/TC2 result, plus an overall
  `N/16 test cases passed` line and a list of any failures.
- Write the same information to `results/servo-test-<ISO8601 timestamp>.log`
  (plain text, human-readable — no structured format needed for this scale).

**CLI options:**
- `--servo NAME` — test a single servo by name (e.g. retesting after a fix).
- `--list` — print the channel map and exit, no motion.

## `requirements.txt`

```
adafruit-circuitpython-pca9685
adafruit-blinka
```

## `results/`

Gitignored log contents, but the directory itself is tracked via `.gitkeep` so it
exists after a fresh `git pull` without the tester having to `mkdir` it.

## `01-pi-pca9685-servos/README.md` — A-Z guide

Ordered walkthrough, written for a tester following it fresh over SSH:

1. Hardware prerequisites — 8 servos wired to the PCA9685 per the channel map,
   servo rail on battery power, robot on a stand.
2. Confirm I2C is enabled on the Pi (`sudo raspi-config nonint do_i2c 0`) and
   `i2c-tools`/`python3-venv` are installed.
3. Verify the PCA9685 enumerates: `i2cdetect -y 1` → expect `0x40`.
4. SSH into the Pi (`ssh <user>@milo.local`).
5. `git pull` the repo (or `git clone` if not present yet).
6. `cd IOT-Testing/01-pi-pca9685-servos`.
7. Create/activate a venv, `pip install -r requirements.txt`.
8. Run `python3 test_servos.py` (or `--servo R1` for a single retest).
9. Watch each servo, answer the `y/n` prompts as motion happens.
10. Read the printed summary; find the full log under `results/`.
11. Troubleshooting: no I2C device found, a servo not moving, jitter/brownout
    symptoms, wrong servo moving for a given channel (wiring mismatch).
12. Safety notes recap (battery-only power, clear of obstructions).

## Top-level `IOT-Testing/README.md`

Short index: states the purpose of the folder (grouped hardware bring-up tests, run
directly on the Pi over SSH, independent of the main software stack), links to
group 01, and lists future groups (OLED display, IMU, camera, mic/speaker) as
planned/not yet built.

## Testing

This is itself a hardware test tool — there's no unit-testable business logic
beyond what `bridge/milo_bridge/drivers/servos.py` already covers (angle→pulse
math). Verification is: run the script against real hardware on the Pi. No
automated test suite is added for this script.
