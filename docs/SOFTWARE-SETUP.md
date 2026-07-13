# MILO — Software Setup: Getting the Code Onto the Robot and Running It

**Date:** 2026-07-08
**Author:** Daham Dissanayake
**Companion docs:** [`BUILD-PLAN.md`](BUILD-PLAN.md) (physical build phases) · [`ARCHITECTURE.md`](ARCHITECTURE.md) (wiring, pin maps) · [`GETTING-STARTED.md`](GETTING-STARTED.md)

This guide covers everything software: preparing the SD card, transferring this repository from your Windows PC (`D:\Github\MILO-Robot`) to the Raspberry Pi, installing every dependency in the right order, configuring, running as a service, setting up the brain machines, and updating code later. Follow it top to bottom.

**One thing to understand first:** you do not copy the code onto the SD card directly from Windows. The SD card's main partition is ext4, which Windows cannot write, and the small FAT32 `bootfs` partition is only for firmware config. The correct flow is: flash the OS onto the card, boot the Pi on WiFi, then transfer the code over the network (git clone or SCP). Both methods are below.

---

## Part 1 — Prepare the SD Card

### 1.1 Flash the OS

1. Insert the microSD (16 GB+) into your PC.
2. Download and open **Raspberry Pi Imager** from https://www.raspberrypi.com/software/.
3. Choose device: **Raspberry Pi Zero 2 W**.
4. Choose OS: **Raspberry Pi OS Lite (64-bit)** (Bookworm, under "Raspberry Pi OS (other)"). Lite means no desktop; 64-bit is required for onnxruntime.
5. Choose storage: your microSD card.
6. Click Next, then **Edit Settings** when asked to apply OS customisation. This step replaces needing a monitor and keyboard entirely:
   - General tab: hostname `milo`, username `dama` + a password, WiFi SSID and password (**must be your 2.4 GHz network** — the Zero 2W has no 5 GHz), WiFi country `LK`, locale/timezone.
   - Services tab: **enable SSH**, password authentication.
7. Write and wait for verification, then eject the card.

Note: the username matters. The systemd service file in this repo (`bridge/systemd/milo-bridge.service`) is written for user `dama` with paths under `/home/dama`. If you pick a different username you must edit those lines later.

### 1.2 First boot

1. Put the card in the Pi, power it from a bench 5 V USB supply (or Buck 1 if the power system is already built and verified at 5.1 V).
2. Wait about 90 seconds for the first boot to complete.
3. From your PC (PowerShell or any terminal):

```powershell
ssh dama@milo.local
```

If `milo.local` does not resolve, find the Pi's IP in your router's client list (or `ping milo.local` after installing Bonjour, or use an app like Fing) and ssh to the IP instead.

### 1.3 System update and required system packages

On the Pi:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-pip python3-venv i2c-tools git python3-picamera2
```

`python3-picamera2` is important: the camera library comes from apt, NOT pip. The virtual environment in Part 3 is created in a way that can see it.

### 1.4 Enable the hardware interfaces

```bash
sudo raspi-config nonint do_i2c 0      # enable I2C (OLED, PCA9685, IMU)
```

Then enable the I2S audio devices (mics + amp). Edit the boot config:

```bash
sudo nano /boot/firmware/config.txt
```

Append at the end:

```ini
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Save (Ctrl+O, Enter, Ctrl+X) and reboot:

```bash
sudo reboot
```

The `googlevoicehat-soundcard` overlay gives simultaneous mic capture and speaker playback on one card. If it misbehaves during bring-up, the fallback is separate `i2s-mems-mic` style and `max98357a` overlays.

**Exit criterion:** `ssh dama@milo.local` works; `ls /dev/i2c-1` exists; `arecord -l` lists a capture device after the reboot.

---

## Part 2 — Transfer the Repository to the Pi

Two options. Option A (GitHub) is recommended because updating later is one `git pull`. Option B works fully offline over your LAN.

### Option A — Clone from GitHub (recommended)

If the repo is pushed to GitHub (push from your PC first if needed):

```powershell
# on your PC, in D:\Github\MILO-Robot — make sure everything is pushed
git status
git push origin main
```

Then on the Pi:

```bash
cd ~
git clone https://github.com/DahamDissanayake/MILO-Robot.git
cd MILO-Robot
```

If the repo is private, the simplest auth on the Pi is a fine-grained personal access token (GitHub Settings > Developer settings > Tokens), used as the password when git asks, or embedded once:

```bash
git clone https://<TOKEN>@github.com/DahamDissanayake/MILO-Robot.git
```

### Option B — Copy directly from your PC over the LAN (no GitHub needed)

From PowerShell on your PC. `scp` ships with Windows 10/11:

```powershell
scp -r D:\Github\MILO-Robot dama@milo.local:/home/dama/MILO-Robot
```

Notes on this method:

- Delete junk before copying to keep it fast: `.venv`, `__pycache__`, `.pytest_cache`, `*.egg-info` folders are not needed on the Pi.
- Alternatively use **WinSCP** (GUI): connect with SFTP to `milo.local`, user `dama`, and drag the folder across.
- To update later you must re-copy changed files; with Option A it is just `git pull`.

### Verify the transfer

On the Pi:

```bash
ls ~/MILO-Robot
# expect: README.md  common/  bridge/  brain/  training/  docs/  hardware/  ...
```

---

## Part 3 — Install the Robot Software (milo-bridge)

### 3.1 Create the virtual environment

The path `~/.venvs/milo` is not arbitrary — the systemd service file points at it. The `--system-site-packages` flag lets the venv see the apt-installed `picamera2`; without it the camera driver cannot import.

```bash
python3 -m venv --system-site-packages ~/.venvs/milo
source ~/.venvs/milo/bin/activate
pip install --upgrade pip
```

### 3.2 Install the packages, in order

`milo-bridge` depends on `milo-common`, so common installs first:

```bash
cd ~/MILO-Robot
pip install -e ./common
pip install -e "./bridge[pi]"
```

The `[pi]` extra pulls the hardware stack: `adafruit-circuitpython-pca9685`, `adafruit-blinka`, `luma.oled`, `smbus2`, `onnxruntime`. This takes a while on a Zero 2W — onnxruntime and numpy are large. If pip is killed (out of memory), retry; or add temporary swap:

```bash
# only if pip keeps getting killed
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
sudo dphys-swapfile setup && sudo dphys-swapfile swapon
```

Audio capture/playback shells out to `arecord`/`aplay` directly (not a Python audio library), so make sure `alsa-utils` is installed: `sudo apt install -y alsa-utils` (usually present by default on Raspberry Pi OS).

### 3.3 Sanity check the install (no hardware needed)

```bash
python -c "import milo_common, milo_bridge; print('imports OK')"
python -m pytest ~/MILO-Robot/common/tests ~/MILO-Robot/bridge/tests -q
```

All bridge tests run against mocked hardware, so they must pass even before wiring. If they pass on your PC and fail on the Pi, the install is broken, not the code.

### 3.4 Smoke-test on real hardware

Only after the wiring and bring-up phases of [`BUILD-PLAN.md`](BUILD-PLAN.md) (Phases 7–8) pass:

```bash
source ~/.venvs/milo/bin/activate
cd ~/MILO-Robot

python bridge/tools/servo_sweep.py       # each servo sweeps 60 -> 120 -> centers at 90
python -m milo_bridge.cli face happy     # OLED shows the happy face
python -m milo_bridge.cli pose rest      # all servos to rest
python -m milo_bridge.cli pose stand     # reference stance
python -m milo_bridge.cli pose wave      # waves
```

### 3.5 Calibrate servo trims

If `pose stand` looks crooked, edit the config on the Pi:

```bash
nano ~/.milo/config.json
```

Adjust `"servo_trims": [0,0,0,0,0,0,0,0]` — degrees, in channel order (R1, R2, L1, L2, R4, R3, L3, L4). Positive/negative small values (2–6 degrees typical). Re-run `pose stand` and iterate until the stance is square.

### 3.6 Install as a systemd service (starts on boot, restarts on crash)

```bash
sudo cp ~/MILO-Robot/bridge/systemd/milo-bridge.service /etc/systemd/system/
```

If your username is not `dama`, edit the copied file first: the `User=` line and the two `/home/dama` paths in `ExecStart`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now milo-bridge
journalctl -u milo-bridge -f             # watch it come up; Ctrl+C to stop watching
```

Useful service commands:

```bash
sudo systemctl status milo-bridge        # is it running
sudo systemctl restart milo-bridge       # after code or config changes
sudo systemctl stop milo-bridge          # stop (e.g. to run CLI commands manually)
journalctl -u milo-bridge -n 100         # last 100 log lines
```

**Exit criterion:** power-cycle the robot; with no keyboard, no monitor, no ssh, it boots to the rest pose with the idle blinking face on its own.

---

## Part 4 — Set Up the Brain Machines (Windows PC / laptop)

Repeat on every machine that will act as a brain (e.g. the RTX 4050 laptop and the desktop).

### 4.1 Prerequisites

- Python 3.11+ installed and on PATH (`python --version`).
- Same LAN/WiFi network as the robot.
- **Ollama** installed and running: download from https://ollama.com.

### 4.2 Get the code and install

```powershell
git clone https://github.com/DahamDissanayake/MILO-Robot.git
cd MILO-Robot
python -m venv .venv
.venv\Scripts\activate
pip install -e ./common
pip install -e ./brain            # light install first, for pairing
```

### 4.3 Pull the LLM for your tier

```powershell
ollama pull llama3.2:3b           # small tier: 6 GB-class GPU (4050 laptop)
ollama pull llama3.1:8b           # large tier: desktop GPU
```

### 4.4 First run and pairing

```powershell
python -m milo_brain --pairing
```

A tray icon appears and the app advertises itself on the LAN. Then:

1. Milo (with `milo-bridge` running) discovers the brain and shows a **6-digit PIN on its OLED face**.
2. Type the PIN into the brain app's dialog.
3. Done permanently — the trust token is stored on both sides (`/etc/milo/paired.json` on the Pi, `~/.milo-brain/paired.json` on the PC).

Pair every brain machine the same way. Verify: live video and mic levels appear in the brain's debug window; kill one brain and Milo fails over to the other within ~10 s; kill both and Milo sleeps.

### 4.5 Full AI stack (when you reach the cognition phase)

```powershell
pip install -e ".\brain[full]"    # faster-whisper, InsightFace, Piper, PyQt6, torch, opencv
python -m milo_brain              # first run downloads Whisper / InsightFace / Silero models
```

Config lives in `~/.milo-brain/config.yaml` — tier is auto-detected from the GPU, models overridable. On a 6 GB GPU: whisper-small + InsightFace + 3B-Q4 LLM fit in about 4 GB; if tight, InsightFace runs fine on CPU.

---

## Part 5 — Deploy the Trained Gait Policy (after training)

Training runs on the GPU machine, never on the Pi. The Pi only receives one small file.

```powershell
# on the GPU machine
pip install -e ".\training[full]"
python -m milo_training.train_ppo --timesteps 20_000_000 --envs 16
python -m milo_training.export_onnx training/runs/ppo-milo/final.zip policy.onnx
scp policy.onnx dama@milo.local:/home/dama/.milo/policy.onnx
```

Then on the Pi:

```bash
sudo systemctl restart milo-bridge
journalctl -u milo-bridge -n 20        # log should say: gait backend: policy
```

Until a policy file exists, the bridge automatically uses the CPG trot fallback — Milo can always walk.

---

## Part 6 — Updating the Code Later

### With git (Option A)

```bash
# on the Pi
cd ~/MILO-Robot && git pull
source ~/.venvs/milo/bin/activate
pip install -e ./common -e "./bridge[pi]"   # only needed if dependencies changed
sudo systemctl restart milo-bridge
```

Because the packages are installed with `-e` (editable), pure code changes take effect on a plain `git pull` + service restart — no reinstall needed.

### With scp (Option B)

```powershell
# from the PC — re-copy only what changed, e.g.
scp -r D:\Github\MILO-Robot\bridge\milo_bridge dama@milo.local:/home/dama/MILO-Robot/bridge/
```

then restart the service on the Pi.

### Brain machines

```powershell
cd MILO-Robot && git pull
.venv\Scripts\activate
python -m milo_brain
```

---

## Part 7 — Where Everything Lives (quick reference)

| Thing | Location |
|---|---|
| Repo on the Pi | `/home/dama/MILO-Robot` |
| Python venv on the Pi | `/home/dama/.venvs/milo` |
| Robot config (trims, fps, thresholds) | `~/.milo/config.json` |
| Knowledge graph (Milo's memory) | `~/.milo/graph.db` |
| Gait policy | `~/.milo/policy.onnx` |
| Pairing tokens (robot side) | `/etc/milo/paired.json` |
| systemd unit | `/etc/systemd/system/milo-bridge.service` |
| Service logs | `journalctl -u milo-bridge` |
| Brain config (PC) | `~/.milo-brain/config.yaml` |
| Pairing tokens (brain side) | `~/.milo-brain/paired.json` |
| Boot/hardware config | `/boot/firmware/config.txt` |

Robot CLI cheat sheet (venv active, service stopped if it holds the hardware):

```bash
python -m milo_bridge.cli pose <rest|stand|wave|...>
python -m milo_bridge.cli face <name>
python -m milo_bridge.cli sweep
python -m milo_bridge.cli paired
```

---

## Part 8 — Troubleshooting

| Symptom | Fix |
|---|---|
| `ssh milo.local` fails | Wrong WiFi band (needs 2.4 GHz), typo in imager WiFi settings, or mDNS not resolving — use the IP from your router |
| `pip install` killed on the Pi | Out of RAM — add the 1 GB swap from 3.2, retry |
| `picamera2` import error in the venv | Venv created without `--system-site-packages` — recreate it with the flag |
| `arecord`/`aplay` not found | `sudo apt install -y alsa-utils` |
| No audio device after reboot | Typo in `config.txt` overlay lines; check `dtoverlay=googlevoicehat-soundcard` spelling |
| Service crashes on boot loop | `journalctl -u milo-bridge -n 50` — usually wrong username/paths in the unit file or hardware not wired yet |
| CLI says device busy | The service holds the hardware — `sudo systemctl stop milo-bridge`, run CLI, restart the service |
| Brain never discovers the robot | Both must be on the same LAN/subnet; check Windows firewall allows Python (mDNS + WebSocket); confirm the tray app is running |
| Pairing PIN never appears | Bridge not running (`systemctl status milo-bridge`) or brain not in `--pairing` mode |
| Streaming laggy / Pi hot | Drop `video_fps` to 10 in `~/.milo/config.json`; confirm 40% idle CPU headroom with `htop` |
