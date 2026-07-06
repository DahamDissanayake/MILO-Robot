# MILO — Getting Started: From Nothing to a Living Robot

**Audience:** you, starting with **no robot at all** — no printed parts, no electronics, nothing assembled.
**Companion docs:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (wiring diagrams, pin maps, tech stack) · [`../project-milo-plan.md`](../project-milo-plan.md) (the original A–Z engineering plan) · [Sesame build guide](https://github.com/dorianborian/sesame-robot/tree/main/docs/build-guide) (mechanical assembly, with photos).

---

## 1. Where the project stands right now

### ✅ Already built (in this repo — you don't have to write code)

All robot and desktop software is **implemented and passing 134 automated tests**. It was developed against mocked hardware, so it's waiting for a real robot to run on:

| Package | What it does | Status |
|---|---|---|
| `common/` | Robot↔brain WebSocket protocol, PIN pairing, HMAC session auth | ✅ done, 21 tests |
| `bridge/` | Everything that runs on the Pi: servo/OLED/IMU/camera/mic drivers, all 20 Sesame poses ported from the ESP32 firmware, 46 face graphics converted, CPG walking gait + ONNX policy runner, SQLite knowledge graph, brain discovery/failover, sleep mode, systemd service, test CLI | ✅ done, 81 tests |
| `brain/` | Desktop app: mDNS advertising, tray UI, VAD → sound direction → Whisper ASR → face recognition → Ollama LLM → Piper TTS cognition loop, unknown-person naming flow | ✅ done, 28 tests |
| `training/` | MuJoCo simulation, PPO trainer, ONNX export for the neural gait | ✅ code done, 4 tests (model geometry is placeholder — see Phase 10) |

### 🔩 Not built (what this guide walks you through)

1. **The physical robot** — every printed part, every solder joint, every screw.
2. **The trained walking policy** — the CPG fallback gait works out of the box; the neural gait needs a training run on your GPU machine *after* the robot exists (it needs real measurements).
3. **The AI model downloads** — Whisper, InsightFace, Piper, and an Ollama LLM on your brain machine(s).

### What Milo is, in one paragraph

Milo is [Dorian Todd's Sesame quadruped](https://github.com/dorianborian/sesame-robot) body with a brain transplant: the ESP32 is replaced by a **Raspberry Pi Zero 2W** driving the 8 servos through a **PCA9685**, and gains a **camera, two I2S microphones, a speaker, and an IMU**. It walks with a neural network, remembers people and facts in an on-robot database, and borrows LLM/speech/vision compute from any PC on your WiFi running the **Milo Brain** app. No brain around → it sleeps.

### Build sequence at a glance

| Phase | What | Time (evenings/weekends) | Money |
|---|---|---|---|
| 1 | Buy every part | order + shipping wait | ≈ LKR 55,000 total |
| 2 | 3D print the body | 1–2 days printing | — |
| 3 | Flash + boot the bare Pi | 1 evening | — |
| 4 | Build the power system | 1–2 evenings | — |
| 5 | Assemble body + servos | 1 weekend | — |
| 6 | Wire all electronics | 1–2 weekends | — |
| 7 | Bring-up checks | 1 evening | — |
| 8 | Install milo-bridge → robot waves | 1 evening | — |
| 9 | Brain app + pairing → robot streams | 1 evening | — |
| 10 | Train the walking policy | 2–4 weeks (iterative) | — |
| 11 | Full cognition → robot talks & remembers | 1–2 evenings | — |
| 12 | Endurance + success criteria | 1 weekend | — |

Phases 8–12 are mostly *running* existing software, not writing it. Keep a dev-log (`docs/dev-logs/`) as you go — future-you will thank you.

---

## Phase 1 — Get every part

**Goal:** everything on the bench before you heat a soldering iron.

### 1a. Electronics (the Milo BOM)

| # | Item | Qty | Why | ~LKR |
|---|---|---|---|---|
| ☐ | Raspberry Pi Zero **2** W (not the original Zero) | 1 | the robot's computer | 23,400 |
| ☐ | 40-pin GPIO header (2×20, 2.54 mm) | 1 | the Zero 2W ships **headerless** — you solder this | incl. misc |
| ☐ | microSD card, 16 GB+ (A1 class) | 1 | Raspberry Pi OS | 2,500 |
| ☐ | Pi Camera V2.1 (IMX219, 8 MP) | 1 | vision | 9,500 |
| ☐ | **CSI ribbon 15→22 pin ("Pi Zero camera cable")** | 1 | ⚠️ the standard camera cable does **not** fit a Zero — check the camera box, buy if absent | ~400 |
| ☐ | PCA9685 16-ch PWM servo driver board | 1 | drives all 8 servos over I2C | 1,000 |
| ☐ | MPU6050 IMU breakout (GY-521) | 1 | balance sensing for the gait — **easy to forget, don't** | ~450 |
| ☐ | MG90S metal-gear servos | 8 (+2 spares ideal) | legs, 2 per leg | 4,640 |
| ☐ | 0.96" SSD1306 128×64 I2C OLED | 1 | the face | 680 |
| ☐ | INMP441 I2S microphone breakout | 2 | stereo hearing + sound direction | 900 |
| ☐ | MAX98357A I2S 3W amp breakout | 1 | the voice | 500 |
| ☐ | Small speaker (4–8 Ω, 2–3 W) | 1 | plugs into the amp | 200 |
| ☐ | 18650 Li-ion cells | 2 | main battery (2S) | ~2,000 |
| ☐ | 2S BMS protection board | 1 | over-discharge/short protection | ~600 |
| ☐ | Buck converter, 5 V / 2–3 A (adjustable, e.g. MP1584/LM2596) | 1 | **logic rail** — Pi + sensors | ~400 |
| ☐ | Buck converter, 5 V / **5 A** (adjustable, e.g. XL4015) | 1 | **servo rail** — PCA9685 V+ only | ~700 |
| ☐ | 2S Li-ion charger module (balance type) | 1 | charge without disassembly | ~300 |
| ☐ | KCD1 rocker switch | 1 | main power, snaps into the top cover | 40 |
| ☐ | Wire: 22 AWG (power) + 30 AWG (signals), silicone | 1 kit each | ⚠️ thicker signal wire will NOT fit the frame | 300 |
| ☐ | Heat-shrink assortment + small zip ties | 1 each | every splice gets shrink | 200 |

### 1b. Mechanical (from the Sesame BOM)

- ☐ **M2 × 5 mm self-threading screws, ~40** — every plastic joint, servo mount, cover
- ☐ **M2.5 × 5 mm machine screws, ~10** — servo horns to servo shafts (the screws in the servo bag are usually too short)
- ☐ M2.5 standoffs / nylon spacers — mounting the Pi, PCA9685, and IMU boards to the frame

### 1c. Tools

Soldering iron + leaded solder (0.6–0.8 mm) · flux pen · solder wick · **multimeter (non-negotiable — Phase 4 depends on it)** · flush cutters · precision screwdrivers · hot-glue or double-sided tape (for mics/speaker only, **never** the IMU) · a bench 5 V USB supply for early Pi testing.

### 1d. What you do NOT need from the Sesame BOM

Skip these — Milo's Pi + PCA9685 replace them: ~~ESP32 / S2 Mini~~, ~~Sesame Distro Board (any version)~~, ~~protoboard + 3-pin servo header matrix~~ (servos plug straight into the PCA9685), ~~Bambu 14500 battery + charger~~ (Milo uses the 2×18650 pack for 3 h+ runtime), ~~USB-C PD cable for tethered running~~.

**Exit criterion:** every box above ticked, parts physically on your bench.

---

## Phase 2 — 3D print the body

**Goal:** all 11 printed parts, cleaned up and ready.

The body is 100% the Sesame design — download STLs from [`sesame-robot/hardware/legged-robo-printing/stl/`](https://github.com/dorianborian/sesame-robot/tree/main/hardware) (also in your local checkout `d:\Github\sesame-robot\hardware\legged-robo-printing.zip`).

**Print settings:** PLA/PLA+ · 8–10% infill · 2 wall loops · honeycomb infill. Joints: use your slicer's auto-orient; no supports. Top cover: **needs manual supports** — follow the [printing guide](https://github.com/dorianborian/sesame-robot/tree/main/hardware/legged-robo-printing)'s support-placement photos, outer brim only.

| Part | Supports | Notes |
|---|---|---|
| ☐ Joints R1–R4, L1–L4 (8 parts) | No | one per servo; auto-orient |
| ☐ Internal frame (v121) | No | holds electronics; see fit note below |
| ☐ Bottom cover (v121) | No | |
| ☐ Top cover (**Enclosed v91 recommended**) | Yes | has the OLED slot + KCD1 cutout |

> **Milo fit note:** the stock internal frame was designed around an ESP32 and a Bambu 14500 pack. Milo carries more: Pi Zero 2W, PCA9685, IMU, amp, 2×18650 + BMS + two bucks + charger. Expect to improvise mounting — standoffs, zip ties, and patience. Dry-fit *everything* in Phase 6 before final assembly; if the 18650 pack won't fit the cavity, the pragmatic options are (a) a slimmer 2S pack, or (b) editing the frame CAD (`Sesame-ESP32-v122.f3z` in the sesame repo) — measure first.

**Exit criterion:** 11 parts printed, supports removed, joints move freely on their pins.

---

## Phase 3 — Flash and boot the bare Pi

**Goal:** headless Pi on your WiFi. Do this *before* any wiring so software problems never get confused with hardware problems.

1. ☐ Flash the microSD with **Raspberry Pi OS Lite (64-bit, Bookworm)** using Raspberry Pi Imager. In the imager's settings (gear icon): hostname `milo`, enable SSH, your **2.4 GHz** WiFi SSID/password (the Zero 2W has no 5 GHz), locale/user (`daham`).
2. ☐ Boot from any 5 V USB supply, wait ~90 s, then from your PC:

```bash
ssh daham@milo.local
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config nonint do_i2c 0          # enable I2C
sudo apt install -y python3-pip python3-venv i2c-tools git python3-picamera2
```

3. ☐ Enable the I2S sound hardware — append to `/boot/firmware/config.txt`:

```ini
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

then `sudo reboot`. (This overlay gives simultaneous mic capture + speaker playback. If it misbehaves in Phase 7, the fallback is separate `i2s-mems-mic`-style + `max98357a` overlays.)

4. ☐ Solder the 40-pin header to the Pi. Take your time — 40 clean joints, no bridges. Inspect with magnification.

**Exit criterion:** `ssh daham@milo.local` works reliably; header soldered and inspected.

---

## Phase 4 — Build the power system

**Goal:** two verified 5.1 V rails. **This phase protects everything expensive — don't rush it.**

Build this chain (full diagram: [ARCHITECTURE.md §5.1](ARCHITECTURE.md#51-power-tree--build-and-verify-this-first)):

```
2×18650 (series) → 2S BMS → KCD1 switch ─┬→ Buck 1 (5V/2A)  → Pi + logic
                          └ charger      └→ Buck 2 (5V/5A)  → PCA9685 V+ (servos ONLY)
```

1. ☐ Spot-check both 18650s (>3.0 V each), connect in series through the BMS. Heat-shrink every joint.
2. ☐ Wire the charger module **battery-side, before the switch** (so it charges with the robot off).
3. ☐ Wire the switch, then both buck inputs in parallel after it.
4. ☐ **With NOTHING connected to the outputs**, power on and adjust each buck's trim pot to **5.10 V on the multimeter.** An unadjusted buck can sit at 12 V — that kills a Pi instantly. Mark the pots with nail polish when done.
5. ☐ Load-test Buck 2: connect 2–3 servos to the PCA9685 (bench, next phase's board), sweep them, confirm the rail never sags below **4.8 V**.
6. ☐ Join the grounds: battery −, both buck outputs, and (later) every breakout share **one common ground**.

> **The three power commandments:** servos are fed **only** by Buck 2 through the PCA9685 V+ screw terminal, never from the Pi's 5 V pin. PCA9685 **VCC** (logic, 3.3 V from the Pi) and **V+** (servo power) are different pins. Verify voltage before connecting any load, every time you change something.

**Exit criterion:** both rails read 5.1 V, servo rail holds under load, everything heat-shrunk.

---

## Phase 5 — Assemble the body and servos

**Goal:** a complete quadruped skeleton with correctly-centered servos.

Follow the [Sesame build guide](https://github.com/dorianborian/sesame-robot/tree/main/docs/build-guide) photo-by-photo for the mechanical steps. The one thing you must do differently is the *centering step*, since there's no ESP32:

1. ☐ **Center every servo to 90° BEFORE attaching any horn or joint.** Wire the Pi ↔ PCA9685 I2C temporarily (next phase's table), plug servos into the PCA9685, then from the repo on the Pi (install per Phase 8, or just these two packages):

```bash
# on the Pi — one-time quick install for bench work
python3 -m venv ~/.venvs/milo && source ~/.venvs/milo/bin/activate
pip install adafruit-circuitpython-pca9685 adafruit-blinka
python bridge/tools/servo_sweep.py        # each servo: 60° → 120° → settles at 90°
```

   Every servo ends the sweep centered at 90°. Only now attach horns/joints, matching the **reference stance** photo in the Sesame build guide (`reference-configuration.png` / `sesame-angle-guide.png`).
2. ☐ Install a servo into each printed joint (M2 self-tappers), assemble legs: hip servo → upper joint, knee servo → lower joint. Right legs are R1 (front hip), R2 (front knee), R3 (rear hip), R4 (rear knee); mirror for L.
3. ☐ Mount the 4 hip servos into the internal frame.
4. ☐ Label every servo lead (masking-tape flags: R1, R2, …) — future-you cannot tell 8 identical wires apart.

**Exit criterion:** robot skeleton stands in the reference stance when all servos are at their pose angles; nothing binds through the full range.

---

## Phase 6 — Wire all the electronics

**Goal:** every device connected per the pin map. Work with the battery **disconnected**; wear grounding discipline.

The master reference is [ARCHITECTURE.md §5](ARCHITECTURE.md#5-wiring-diagram). The compact version:

| Pi GPIO (pin) | Goes to |
|---|---|
| 5V (pin 2) | Buck 1 output |
| GND (pins 6, 9, 14, …) | common ground |
| 3V3 (pin 1/17) | PCA9685 VCC, both INMP441 VDD, MPU6050 VCC, OLED VCC, Mic B L/R pin |
| GPIO 2 / SDA (pin 3) | PCA9685 SDA + OLED SDA + MPU6050 SDA |
| GPIO 3 / SCL (pin 5) | PCA9685 SCL + OLED SCL + MPU6050 SCL |
| GPIO 18 (pin 12) | both INMP441 **SCK** + MAX98357A **BCLK** |
| GPIO 19 (pin 35) | both INMP441 **WS** + MAX98357A **LRC** |
| GPIO 20 (pin 38) | both INMP441 **SD** (one shared line) |
| GPIO 21 (pin 40) | MAX98357A **DIN** |
| CSI connector | camera via the 15→22-pin ribbon |

Checklist:

1. ☐ **Servos → PCA9685 channels**, exactly this map (it's baked into the software): `R1=0, R2=1, L1=2, L2=3, R4=4, R3=5, L3=6, L4=7`. Brown/black = GND, red = V+, orange/yellow = signal.
2. ☐ **PCA9685**: VCC ← Pi 3V3, V+ ← Buck 2, SDA/SCL ← I2C bus, GND ← common.
3. ☐ **Mics**: Mic A **L/R pin → GND** (left channel), mounted **left** side of the head; Mic B **L/R → 3V3** (right), **right** side. Aim for **10–15 cm between them** — this baseline is what makes sound direction work.
4. ☐ **MPU6050**: mounted **rigidly** near the body center — screws or hard standoffs into the frame. Foam tape = a floppy IMU = garbage gait feedback. This is the one component where mounting quality really matters.
5. ☐ **OLED** into the top-cover slot (see Sesame guide for wire routing through the cover), **speaker + amp** wherever they fit with the speaker facing outward, **camera** facing forward in the head, ribbon blue-side per connector convention.
6. ☐ Dry-fit everything in the frame *before* final soldering; then solder, shrink, bundle with zip ties, and close up only after Phase 7 passes.

**Exit criterion:** everything connected, nothing smoking, frame closes (or nearly).

---

## Phase 7 — Bring-up: prove every device works

**Goal:** every subsystem enumerates and responds, on battery power.

```bash
ssh daham@milo.local

# 1 ─ I2C: expect 0x3c (OLED), 0x40 (PCA9685), 0x68 (MPU6050)
i2cdetect -y 1

# 2 ─ Microphones: record 3 s stereo, play it back through the speaker
arecord -l                                        # capture device listed?
arecord -D plughw:0 -c2 -r16000 -f S16_LE -d 3 test.wav
aplay test.wav                                    # hear yourself?

# 3 ─ Camera
rpicam-hello --list-cameras                       # imx219 listed?
rpicam-jpeg -o test.jpg                           # sharp image?

# 4 ─ Servos: full 8-channel sweep on battery — watch for the correct leg
#     moving each time and NO brownout (Pi rebooting = rail problem)
python bridge/tools/servo_sweep.py
```

Troubleshooting the usual suspects:

| Symptom | Likely cause |
|---|---|
| A device missing from `i2cdetect` | swapped SDA/SCL, no 3V3, bad solder joint on that breakout |
| Only one mic channel in `test.wav` | both L/R pins strapped the same way — one must be GND, one 3V3 |
| No audio device at all | `config.txt` overlay typo; reboot after editing |
| Camera not detected | ribbon backwards or not seated; Zeros are picky — reseat both ends |
| Pi reboots when servos move | servos powered from the wrong rail, or Buck 2 undersized/sagging |
| A leg moves when a different name is expected | channel map violated — fix the plug order, not the code |

**Exit criterion (spec Phase A):** all three I2C devices show; stereo record/playback works; camera captures; all 8 servos sweep the correct legs from battery with no brownout.

---

## Phase 8 — Install milo-bridge: the robot comes alive

**Goal:** Milo boots to a resting pose with a blinking face, as a service, and waves on command.

```bash
# on the Pi
cd ~ && git clone https://github.com/DahamDissanayake/MILO-Robot.git
cd MILO-Robot
source ~/.venvs/milo/bin/activate        # from Phase 5, or create it now
pip install -e ./common
pip install -e "./bridge[pi]"

# smoke-test directly first
python -m milo_bridge.cli face happy     # OLED shows the happy face
python -m milo_bridge.cli pose rest      # all servos to 90
python -m milo_bridge.cli pose stand     # reference stance
python -m milo_bridge.cli pose wave      # 👋

# per-servo trim calibration: if the stand pose looks crooked, edit
# ~/.milo/config.json → "servo_trims": [0,0,0,0,0,0,0,0]  (degrees, channel order)
# and iterate on `pose stand` until square.

# then install as a service (starts on boot, restarts on crash)
sudo cp bridge/systemd/milo-bridge.service /etc/systemd/system/
# edit the two /home/daham paths inside if your username differs
sudo systemctl daemon-reload && sudo systemctl enable --now milo-bridge
journalctl -u milo-bridge -f             # watch it come up
```

**Exit criterion (spec Phase B):** power-cycle the robot → it boots headless to the rest pose with the idle blinking face; `pose wave` waves; trims tuned so `stand` is square.

---

## Phase 9 — Milo Brain: pair a computer, see through Milo's eyes

**Goal:** live video/audio streaming to your PC; PIN pairing; failover between two machines.

On each brain machine (your RTX 4050 laptop and/or 5090 desktop):

```bash
git clone https://github.com/DahamDissanayake/MILO-Robot.git && cd MILO-Robot
python -m venv .venv && .venv\Scripts\activate       # Windows
pip install -e ./common -e ./brain                    # light install (no AI models yet)
python -m milo_brain --pairing                        # tray icon appears; pairing mode ON
```

Pairing dance (once per machine, <2 min):

1. ☐ Brain app running with pairing enabled → it advertises itself on the LAN.
2. ☐ Milo discovers it, connects, and **shows a 6-digit PIN on its face**.
3. ☐ Type the PIN into the brain app's dialog. Done — the trust token is stored on both sides forever (`/etc/milo/paired.json` ↔ `~/.milo-brain/paired.json`).

Then verify the resilience story:

- ☐ Pair the second machine too.
- ☐ Kill brain #1 → Milo fails over to #2 within ~10 s.
- ☐ Kill both → Milo lies down, shows the sleepy face (streams stop).
- ☐ Clap loudly → Milo perks up briefly and rescans.
- ☐ Restart a brain → Milo stands up with the excited face.
- ☐ Try connecting from an unpaired machine → refused.
- ☐ Check Pi CPU while streaming: `htop` over ssh — you want **≥40% idle headroom**. If not, drop `video_fps` to 10 in `~/.milo/config.json`.

**Exit criterion (spec Phase C):** all seven boxes above.

---

## Phase 10 — Teach Milo to walk (the neural gait)

**Goal:** Milo walks on velocity commands from a trained network. **Highest-risk phase; budget 3–5 sim-to-real iterations.**

Note: **Milo can already walk** — the CPG trot in `bridge/milo_bridge/gait/cpg.py` works without any training (send `{"move": {"velocity": [0.1, 0, 0]}}` or let the brain drive it). The neural gait is the upgrade pass:

1. ☐ **Measure the real robot** (this is why training waits for hardware): total mass, per-link lengths, and **servo step response** — command a 30° step via the CLI, film at 120 fps (or log the IMU), fit the time constant. Expect 20–40 ms.
2. ☐ **Update `training/models/milo.xml`** with those numbers (masses, dims, actuator `kp`/lag). The current file is a structurally-correct placeholder and says so at the top. Sanity check: the model stands in the MuJoCo viewer.
3. ☐ On the GPU machine: `pip install -e "./training[full]"`, then

```bash
python -m milo_training.train_ppo --timesteps 20_000_000 --envs 16
python -m milo_training.export_onnx training/runs/ppo-milo/final.zip policy.onnx
scp policy.onnx daham@milo.local:~/.milo/policy.onnx
ssh daham@milo.local sudo systemctl restart milo-bridge   # log shows "gait backend: policy"
```

4. ☐ Test on carpet and smooth floor. When it stumbles: adjust the randomization ranges / measured lag in `env.py`, retrain, redeploy. Film every attempt for the dev-log — the fail compilation is half the fun.

**Exit criterion (spec Phase D):** Milo walks forward and turns left/right on the real floor under the RL policy; the CPG fallback also walks.

---

## Phase 11 — Full cognition: Milo sees, hears, talks, remembers

**Goal:** conversations with memory. This phase is downloads + configuration; the pipeline code is done.

On each brain machine:

```bash
pip install -e "./brain[full]"          # faster-whisper, InsightFace, Piper, PyQt6, torch
# install Ollama from https://ollama.com, then pull the tier model:
ollama pull llama3.2:3b                 # 4050 laptop (small tier)
ollama pull llama3.1:8b                 # 5090 desktop (large tier)
python -m milo_brain                    # first run downloads Whisper/InsightFace/Silero
```

Config lives in `~/.milo-brain/config.yaml` (tier auto-detected from your GPU; models overridable). **4050 VRAM note:** whisper-small + InsightFace + 3B-Q4 ≈ 4 GB — fits in 6 GB; if it's tight, InsightFace runs fine on CPU at 3 fps.

Then run the acceptance rituals:

- ☐ Talk to Milo from the side → it turns toward your voice, then answers with a talking face and spoken reply.
- ☐ First meeting: Milo doesn't know you → *"Hi! I don't think we've met — what's your name?"* → answer → it creates your person node with your face embeddings.
- ☐ Tell it a fact ("I have an exam tomorrow") → fact lands in the graph (`~/.milo/graph.db` on the Pi).
- ☐ **The portability test — the project's core promise (spec F.6):** teach Milo a fact via the *laptop* brain → power-cycle the robot → connect via the *desktop* brain → Milo greets you **by name** and still **knows the fact**. Its memory lives on the robot, not the computer.

**Exit criterion (spec Phases E+F):** all four boxes, especially the last one.

---

## Phase 12 — Integration, endurance, sign-off

**Goal:** every success criterion from the design spec passes.

- ☐ Battery endurance: full interactive session, log voltage over time, require **>3 h**.
- ☐ GPU-busy handling: load a game/training job on the brain → it advertises `busy` → Milo prefers the other paired brain, or sleeps.
- ☐ Run the full spec §10 checklist and film it:

| # | Success criterion | Pass? |
|---|---|---|
| 1 | Walks with the trained neural policy: forward, turn left/right | ☐ |
| 2 | Recognizes a returning person by face, greets by name | ☐ |
| 3 | Turns toward the direction of a voice | ☐ |
| 4 | Holds a spoken conversation with matching facial expressions | ☐ |
| 5 | Remembers a fact across a power cycle **and** a different brain | ☐ |
| 6 | Pairing a fresh machine takes <2 min; unpaired machines refused | ☐ |
| 7 | Sleeps with no brain available; wakes on reconnection | ☐ |
| 8 | Battery >3 h of normal interactive use | ☐ |

- ☐ Write the final dev-log entry with the video. You built a robot.

---

## Appendix — quick reference

- **Repo test suite** (any machine, no hardware): `python -m pytest common/tests bridge/tests brain/tests training/tests`
- **Robot CLI:** `python -m milo_bridge.cli pose <name> | face <name> | sweep | paired`
- **Service logs:** `journalctl -u milo-bridge -f`
- **Locked constants** (change nothing without changing everything): servo map `R1=0 R2=1 L1=2 L2=3 R4=4 R3=5 L3=6 L4=7`; I2C `0x40` PCA9685 / `0x3C` OLED / `0x68` MPU6050; streams MJPEG 640×480@15 + stereo PCM 16 kHz.
- **Credits:** body, mechanics, face art, and pose library from the [Sesame Robot Project](https://github.com/dorianborian/sesame-robot) by Dorian Todd (Apache 2.0).
