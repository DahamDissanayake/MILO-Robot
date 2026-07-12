# MILO — Physical Build Plan (Hardware-First, Phase by Phase)

**Date:** 2026-07-08
**Author:** Daham Dissanayake
**Companion docs:** [`ARCHITECTURE.md`](ARCHITECTURE.md) (wiring diagrams, pin maps) · [`GETTING-STARTED.md`](GETTING-STARTED.md) (full walkthrough) · [`SOFTWARE-SETUP.md`](SOFTWARE-SETUP.md) (SD card, code transfer, install, service, brain setup) · [`../project-milo-plan.md`](../project-milo-plan.md) (software A–Z plan) · Project Milo Proposal (scope, budget, risk)

**Context:** All software (bridge, brain, protocol, gait engine, knowledge graph, training pipeline) is already implemented and passing 134 tests. This plan covers only what remains: building the physical robot, verified step by step, so nothing expensive gets damaged and nothing is assembled before it is proven working.

**Golden rule of this plan:** test every component on the bench BEFORE it is screwed into the body. Assembly is the last step of each subsystem, never the first.

---

## Phase 1 — Procurement and Parts Verification

**Goal:** every part on the bench, individually inspected, before touching a soldering iron.

### 1.1 Electronics checklist

- [ ] Raspberry Pi Zero 2 W (not the original Zero)
- [ ] 40-pin GPIO header (2x20, 2.54 mm) — the Zero 2W ships headerless
- [ ] microSD card, 16 GB+, A1 class
- [ ] Pi Camera V2.1 (IMX219, 8 MP)
- [ ] CSI ribbon cable 15-to-22 pin ("Pi Zero camera cable") — the standard camera cable does NOT fit a Zero; check the camera box, buy separately if absent
- [ ] PCA9685 16-channel PWM servo driver board
- [ ] MPU6050 IMU breakout (GY-521) — easy to forget, required for the gait
- [ ] 8x MG90S metal-gear servos (2 spares recommended)
- [ ] 0.96" SSD1306 128x64 I2C OLED
- [ ] 2x INMP441 I2S microphone breakouts
- [ ] MAX98357A I2S 3 W amplifier breakout + small speaker (4–8 ohm, 2–3 W)
- [ ] 2x 18650 Li-ion cells + 2S BMS protection board
- [ ] Buck converter 5 V / 2–3 A adjustable (logic rail)
- [ ] Buck converter 5 V / 5 A adjustable (servo rail, e.g. XL4015)
- [ ] 2S Li-ion charger module (balance type)
- [ ] KCD1 rocker switch
- [ ] Wire: 22 AWG (power) and 30 AWG (signals), silicone — thicker signal wire will not fit the frame
- [ ] Heat-shrink assortment + small zip ties

### 1.2 Mechanical fasteners

- [ ] ~40x M2 x 5 mm self-threading screws — every plastic joint, servo mount, and cover
- [ ] ~10x M2.5 x 5 mm machine screws — servo horns to servo shafts (screws in the servo bag are usually too short)
- [ ] M2.5 standoffs / nylon spacers — mounting the Pi, PCA9685, and IMU to the frame

### 1.3 Tools

Soldering iron with leaded solder (0.6–0.8 mm), flux pen, solder wick, multimeter (non-negotiable, Phase 4 depends on it), flush cutters, precision screwdrivers, bench 5 V USB supply for early Pi testing. Hot glue or double-sided tape for the mics and speaker only, never the IMU.

**Exit criterion:** every box ticked, all parts physically on the bench.

---

## Phase 2 — 3D Print the Body

**Goal:** all 11 printed parts, cleaned up, joints moving freely.

The body is 100% the Sesame design. Print settings: PLA/PLA+, 8–10% infill, 2 wall loops, honeycomb infill.

| Part | Qty | Supports |
|---|---|---|
| Leg joints R1–R4, L1–L4 | 8 | No (auto-orient) |
| Internal frame (v121) | 1 | No |
| Bottom cover (v121) | 1 | No |
| Top cover (Enclosed v91, has OLED slot + switch cutout) | 1 | Yes, manual placement |

**Fit warning:** the stock frame was designed for an ESP32 and a small battery. Milo carries a Pi Zero 2W, PCA9685, IMU, amp, 2x 18650 + BMS + two bucks + charger. Dry-fit everything in Phase 7 before final assembly; if the 18650 pack will not fit, use a slimmer 2S pack or edit the frame CAD.

**Exit criterion:** 11 parts printed, supports removed, joints move freely on their pins.

---

## Phase 3 — Set Up the Raspberry Pi Zero 2W

**Goal:** headless Pi on WiFi with header soldered, before any robot wiring, so software problems never get confused with hardware problems.

### 3.1 Flash and boot

- [ ] Flash the microSD with Raspberry Pi OS Lite (64-bit, Bookworm) using Raspberry Pi Imager. In imager settings: hostname `milo`, enable SSH, 2.4 GHz WiFi SSID/password (the Zero 2W has no 5 GHz), user `daham`.
- [ ] Boot from a bench 5 V USB supply, wait ~90 s, then verify:

```bash
ssh daham@milo.local
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config nonint do_i2c 0
sudo apt install -y python3-pip python3-venv i2c-tools git python3-picamera2
```

### 3.2 Enable I2S audio hardware

- [ ] Append to `/boot/firmware/config.txt`, then reboot:

```ini
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Fallback if this overlay misbehaves later: separate `i2s-mems-mic` style capture + `max98357a` overlays.

### 3.3 Solder the GPIO header

- [ ] Solder the 40-pin header to the Pi. 40 clean joints, no bridges. Inspect with magnification. This is the first soldering task of the project; take it slowly.

**Exit criterion:** `ssh daham@milo.local` works reliably; header soldered and inspected.

---

## Phase 4 — Build and Verify the Power System

**Goal:** two verified 5.1 V rails. This phase protects everything expensive. Do not rush it.

Target chain:

```
2x 18650 (series) -> 2S BMS -> KCD1 switch --+-> Buck 1 (5V/2A) -> Pi + logic
                        |                    +-> Buck 2 (5V/5A) -> PCA9685 V+ (servos ONLY)
                        +-- charger module (battery side, before the switch)
```

- [ ] Spot-check both 18650 cells (>3.0 V each), connect in series through the BMS. Heat-shrink every joint.
- [ ] Wire the charger module battery-side, before the switch, so it charges with the robot off.
- [ ] Wire the switch, then both buck inputs in parallel after it.
- [ ] With NOTHING connected to the outputs, power on and adjust each buck trim pot to exactly 5.10 V on the multimeter. An unadjusted buck can sit at 12 V and kills a Pi instantly. Mark the pots with nail polish when done.
- [ ] Load-test Buck 2: connect 2–3 servos to the PCA9685 on the bench, sweep them, confirm the rail never sags below 4.8 V.
- [ ] Join grounds: battery negative, both buck outputs, and later every breakout share one common ground.

**The three power commandments:**

1. Servos are fed only by Buck 2 through the PCA9685 V+ screw terminal, never from the Pi's 5 V pin.
2. PCA9685 VCC (logic, 3.3 V from the Pi) and V+ (servo power) are different pins.
3. Verify voltage before connecting any load, every time anything changes.

**Exit criterion:** both rails read 5.1 V, servo rail holds under load, everything heat-shrunk.

---

## Phase 5 — Bench-Test and Center the Servos

**Goal:** all 8 servos proven working and centered at 90 degrees BEFORE any horn or joint is attached. This ordering matters: there is no way to center a servo correctly after the leg is screwed on.

- [ ] Temporarily wire Pi to PCA9685: 3V3 -> VCC, SDA (GPIO 2), SCL (GPIO 3), common GND; Buck 2 -> V+.
- [ ] Plug all 8 servos into channels 0–7 and run the sweep:

```bash
# on the Pi
python3 -m venv ~/.venvs/milo && source ~/.venvs/milo/bin/activate
pip install adafruit-circuitpython-pca9685 adafruit-blinka
python bridge/tools/servo_sweep.py     # each servo: 60 -> 120 -> settles at 90
```

- [ ] Confirm every servo sweeps smoothly with no grinding, stalling, or jitter. Replace any suspect servo now (this is what the spares are for).
- [ ] Confirm no brownout with multiple servos moving (Pi rebooting = rail problem, go back to Phase 4).
- [ ] Every servo ends centered at 90 degrees. Do not move the shafts by hand after this.
- [ ] Label every servo lead with masking-tape flags (R1, R2, ... L4) — eight identical wires are indistinguishable later.

**Exit criterion:** 8 healthy servos, all centered at 90 degrees, all labeled.

---

## Phase 6 — Mechanical Assembly (Screwing the Body Together)

**Goal:** a complete quadruped skeleton with correctly oriented legs. Follow the Sesame build guide photos for each mechanical step.

Servo positions and PCA9685 channels (locked in software, do not deviate):

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

- [ ] Install one servo into each printed joint using M2 x 5 mm self-threading screws. Do not overtighten into plastic; snug is enough.
- [ ] Attach servo horns with M2.5 machine screws, matching the reference stance photos in the Sesame build guide (`reference-configuration.png` / `sesame-angle-guide.png`). Servos are still centered at 90 from Phase 5 — the horn position at attachment defines the leg's zero.
- [ ] Assemble each leg: hip servo -> upper joint, knee servo -> lower joint. Right side is R1 (front hip), R2 (front knee), R3 (rear hip), R4 (rear knee); mirror for the left.
- [ ] Mount the 4 hip servos into the internal frame with M2 screws.
- [ ] Check the full range of motion of every joint by commanding poses (not by forcing shafts by hand); nothing may bind.

**Exit criterion:** skeleton stands in the reference stance at pose angles; full range of motion with no binding.

---

## Phase 7 — Circuit Building, Wiring and Soldering

**Goal:** every device connected per the pin map. Work with the battery disconnected. Dry-fit everything before final soldering.

### 7.1 Pin map (master reference: ARCHITECTURE.md section 5; wire-by-wire detail with both ends of every wire: [`WIRING-GUIDE.md`](WIRING-GUIDE.md))

| Pi GPIO (pin) | Connects to |
|---|---|
| 5V (pin 2) | Buck 1 output |
| GND (pins 6, 9, 14, ...) | common ground |
| 3V3 (pins 1/17) | PCA9685 VCC, both INMP441 VDD, MPU6050 VCC, OLED VCC, Mic B L/R pin |
| GPIO 2 / SDA (pin 3) | PCA9685 SDA + OLED SDA + MPU6050 SDA |
| GPIO 3 / SCL (pin 5) | PCA9685 SCL + OLED SCL + MPU6050 SCL |
| GPIO 18 (pin 12) | both INMP441 SCK + MAX98357A BCLK |
| GPIO 19 (pin 35) | both INMP441 WS + MAX98357A LRC |
| GPIO 20 (pin 38) | both INMP441 SD (one shared data line) |
| GPIO 21 (pin 40) | MAX98357A DIN |
| CSI connector | IMX219 camera via the 15-to-22 pin ribbon |

I2C addresses that must appear on the bus: PCA9685 `0x40`, SSD1306 `0x3C`, MPU6050 `0x68`.

### 7.2 Wiring checklist

- [ ] Dry-fit all boards, battery pack, bucks, and wiring inside the frame BEFORE soldering anything permanently. Solve the space problem now, not after soldering.
- [ ] Servos into PCA9685 channels exactly per the Phase 6 map. Brown/black = GND, red = V+, orange/yellow = signal.
- [ ] PCA9685: VCC from Pi 3V3, V+ from Buck 2, SDA/SCL on the I2C bus, GND common.
- [ ] Mic A: L/R pin soldered to GND (left channel), mounted on the left side of the head. Mic B: L/R pin to 3V3 (right channel), right side. Target 10–15 cm between mics — this baseline is what makes sound direction work.
- [ ] MPU6050 mounted rigidly near the body center with screws or hard standoffs. Foam tape gives a floppy IMU and garbage gait feedback. This is the one component where mounting quality really matters.
- [ ] OLED into the top-cover slot; route wires per the Sesame guide.
- [ ] Speaker + amp mounted with the speaker facing outward.
- [ ] Camera facing forward in the head; ribbon seated at both ends, blue side per connector convention.
- [ ] Solder all permanent joints, heat-shrink every splice, bundle with zip ties.
- [ ] Do NOT close the covers yet — Phase 8 must pass first.

**Exit criterion:** everything connected per the pin map, nothing smoking, frame closes (or nearly).

---

## Phase 8 — Bring-Up: Test Everything Before Final Assembly

**Goal:** every subsystem enumerates and responds, on battery power, with the body still open for rework.

```bash
ssh daham@milo.local

# 1 - I2C: expect 0x3c (OLED), 0x40 (PCA9685), 0x68 (MPU6050)
i2cdetect -y 1

# 2 - Microphones: record 3 s stereo, play back through the speaker
arecord -l
arecord -D plughw:0 -c2 -r16000 -f S16_LE -d 3 test.wav
aplay test.wav

# 3 - Camera
rpicam-hello --list-cameras        # imx219 listed?
rpicam-jpeg -o test.jpg            # sharp image?

# 4 - Servos: full 8-channel sweep on battery
python bridge/tools/servo_sweep.py
```

Troubleshooting table:

| Symptom | Likely cause |
|---|---|
| Device missing from i2cdetect | swapped SDA/SCL, no 3V3, bad solder joint on that breakout |
| Only one mic channel in test.wav | both L/R pins strapped the same; one must be GND, one 3V3 |
| No audio device at all | config.txt overlay typo; reboot after editing |
| Camera not detected | ribbon backwards or not seated; reseat both ends |
| Pi reboots when servos move | servos on the wrong rail, or Buck 2 sagging |
| Wrong leg moves | channel map violated; fix the plug order, not the code |

- [ ] All checks pass on battery power (not the bench supply).
- [ ] Only now: final cable dressing, close the bottom and top covers.

**Exit criterion:** three I2C devices show; stereo record/playback works; camera captures; all 8 servos sweep the correct legs from battery with no brownout; covers closed.

---

## Phase 9 — Install milo-bridge: The Robot Comes Alive

**Goal:** Milo boots to a resting pose with a blinking face as a systemd service, and waves on command.

```bash
# on the Pi
cd ~ && git clone https://github.com/DahamDissanayake/MILO-Robot.git
cd MILO-Robot
source ~/.venvs/milo/bin/activate
pip install -e ./common
pip install -e "./bridge[pi]"

# smoke tests
python -m milo_bridge.cli face happy
python -m milo_bridge.cli pose rest
python -m milo_bridge.cli pose stand
python -m milo_bridge.cli pose wave

# install as a service
sudo cp bridge/systemd/milo-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now milo-bridge
journalctl -u milo-bridge -f
```

- [ ] If the stand pose looks crooked, calibrate per-servo trims in `~/.milo/config.json` (`servo_trims`, degrees, channel order) and iterate on `pose stand` until square.

**Exit criterion:** power-cycle the robot; it boots headless to the rest pose with the idle blinking face; `pose wave` waves; stand is square.

---

## Phase 10 — Pair a Brain: Streaming, Discovery, Failover

**Goal:** live video/audio on the PC; PIN pairing under 2 minutes; failover between two machines.

On each brain machine (laptop and/or desktop):

```bash
git clone https://github.com/DahamDissanayake/MILO-Robot.git && cd MILO-Robot
python -m venv .venv && .venv\Scripts\activate
pip install -e ./common -e ./brain
python -m milo_brain --pairing
```

Verification matrix:

- [ ] Milo discovers the brain and shows a 6-digit PIN on its face; typing it into the brain app pairs permanently.
- [ ] Pair the second machine too.
- [ ] Kill brain 1 -> Milo fails over to brain 2 within ~10 s.
- [ ] Kill both -> Milo lies down with the sleepy face, streams stop.
- [ ] Clap loudly -> Milo perks up and rescans.
- [ ] Restart a brain -> Milo stands with the excited face.
- [ ] An unpaired machine is refused.
- [ ] Pi CPU while streaming has at least 40% idle headroom (`htop`); if not, drop `video_fps` to 10 in `~/.milo/config.json`. This headroom is required before Phase 11.

**Exit criterion:** all boxes above.

---

## Phase 11 — Teach Milo to Walk (Neural Gait)

**Goal:** Milo walks on velocity commands from a trained network. Highest-risk phase; budget 3–5 sim-to-real iterations. Note that the CPG trot fallback already walks with no training, so Milo is never legless.

- [ ] Measure the real robot: total mass, per-link lengths, and servo step response (command a 30 degree step, film at 120 fps or log the IMU, fit the time constant; expect 20–40 ms).
- [ ] Update `training/models/milo.xml` with measured masses, dimensions, and actuator lag. Sanity check: the model stands in the MuJoCo viewer.
- [ ] Train and deploy from the GPU machine:

```bash
pip install -e "./training[full]"
python -m milo_training.train_ppo --timesteps 20_000_000 --envs 16
python -m milo_training.export_onnx training/runs/ppo-milo/final.zip policy.onnx
scp policy.onnx daham@milo.local:~/.milo/policy.onnx
ssh daham@milo.local sudo systemctl restart milo-bridge
```

- [ ] Test on carpet and smooth floor. On failure: adjust domain-randomization ranges / measured lag, retrain, redeploy. Film every attempt for the dev-log.

**Exit criterion:** Milo walks forward and turns left/right on the real floor under the RL policy; the CPG fallback also walks.

---

## Phase 12 — Full Cognition: See, Hear, Talk, Remember

**Goal:** conversations with memory. This phase is downloads plus configuration; the pipeline code is done.

On each brain machine:

```bash
pip install -e "./brain[full]"
ollama pull llama3.2:3b        # small tier (6 GB-class GPU)
ollama pull llama3.1:8b        # large tier (desktop GPU)
python -m milo_brain           # first run downloads Whisper/InsightFace/Silero
```

Acceptance rituals:

- [ ] Talk to Milo from the side -> it turns toward the voice, then answers with a talking face and spoken reply.
- [ ] First meeting -> Milo asks for a name -> creates the person node with face embeddings.
- [ ] Tell it a fact -> the fact lands in `~/.milo/graph.db` on the Pi.
- [ ] The portability test (core promise): teach Milo a fact via the laptop brain -> power-cycle the robot -> connect via the desktop brain -> Milo greets by name and still knows the fact.

**Exit criterion:** all four, especially the last.

---

## Phase 13 — Endurance, Success Criteria, Sign-Off

**Goal:** every success criterion from the design spec passes.

- [ ] Battery endurance: full interactive session, log voltage over time, require more than 3 hours. Add low-battery face + graceful shutdown at cutoff.
- [ ] GPU-busy handling: load the brain's GPU -> it advertises busy -> Milo prefers the other paired brain, or sleeps.
- [ ] Run and film the full checklist:

| # | Success criterion | Pass |
|---|---|---|
| 1 | Walks with the trained neural policy: forward, turn left/right | [ ] |
| 2 | Recognizes a returning person by face, greets by name | [ ] |
| 3 | Turns toward the direction of a voice | [ ] |
| 4 | Holds a spoken conversation with matching facial expressions | [ ] |
| 5 | Remembers a fact across a power cycle and a different brain | [ ] |
| 6 | Pairing a fresh machine takes under 2 minutes; unpaired machines refused | [ ] |
| 7 | Sleeps with no brain available; wakes on reconnection | [ ] |
| 8 | Battery lasts more than 3 hours of normal interactive use | [ ] |

- [ ] Final dev-log entry with video.

**Exit criterion:** all 8 criteria pass. Project complete.

---

## Timeline Estimate (evenings and weekends pace)

| Phase | Scope | Estimate |
|---|---|---|
| 1–2 | Parts + 3D printing | order wait + 1–2 days printing |
| 3 | Pi setup + header soldering | 1 evening |
| 4 | Power system | 1–2 evenings |
| 5 | Servo bench test + centering | 1 evening |
| 6 | Mechanical assembly | 1 weekend |
| 7 | Wiring + soldering | 1–2 weekends |
| 8 | Bring-up testing | 1 evening |
| 9 | milo-bridge install | 1 evening |
| 10 | Brain pairing + streaming | 1 evening |
| 11 | Gait training | 2–4 weeks (iterative) |
| 12 | Cognition | 1–2 evenings |
| 13 | Endurance + sign-off | 1 weekend |
| Total | | roughly 8–13 weeks |

## Top Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Unadjusted buck kills the Pi | Set both bucks to 5.1 V with a multimeter before connecting any load (Phase 4) |
| Servo brownout resets the Pi | Dedicated 5 A servo rail, staggered activation, never power servos from the Pi |
| Sim-to-real gap in the learned gait | Measured servo lag in the sim, aggressive domain randomization, CPG fallback exists first |
| Pi Zero 2W CPU saturation | Verify 40% idle headroom in Phase 10 before gait work; drop video to 10 fps first |
| Floppy IMU ruins gait feedback | Rigid mount with screws or standoffs, never foam tape |
| 18650 pack does not fit the frame | Dry-fit in Phase 7; slimmer 2S pack or frame CAD edit as fallback |
