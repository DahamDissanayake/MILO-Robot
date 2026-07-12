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
screen. Servos and Display are manual control panels — no verdicts, just
buttons you press to move things and watch. IMU, Microphones, and Speaker
show PASS/FAIL buttons after each test case — click FAIL to reveal a note
field for what went wrong.

- **Servos**: click "Connect" after reading the safety banner. Once
  connected, each of the 8 servos gets its own row with 0°/45°/90°/135°/180°
  buttons — press one to jog that servo to that angle and watch it move.
  "Relax All" de-energizes every channel when you're done.
- **Display**: click "Connect", then press any emote button (idle, happy,
  angry, sad, excited, sleepy, wave, dance) to show that face immediately, or
  "Show Pairing PIN" to render the pairing-PIN screen.
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
| Re-entering the Camera or Microphones screen in the same session fails to open the device (e.g. "device busy") | The previous session's camera/mic handle isn't explicitly released on exit — quit the app (`q` or navigate to Quit) and relaunch `milo-iot-tester` rather than re-entering the screen. |

## Safety reminders

- Servos draw from the battery/5A buck rail only. Powering them from the Pi's
  5V rail can brown out the Pi mid-test.
- Keep the robot supported on a stand during servo tests — legs move through
  their entire range.
- Common ground between every rail, the Pi, and every breakout (see
  `PINOUT.md`).
