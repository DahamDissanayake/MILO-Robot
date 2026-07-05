# Project Milo — A–Z Project Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Phases D–F are large; write a detailed per-phase implementation plan (superpowers:writing-plans) before starting each.

**Date:** 2026-07-05
**Author:** Daham Dissanayake
**Spec:** [`docs/specs/2026-07-05-project-milo-design.md`](docs/specs/2026-07-05-project-milo-design.md) · **Architecture:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

**Goal:** Upgrade the Sesame quadruped (same body, same 8× MG90 servos) into Milo — a free-roaming robot with a Raspberry Pi Zero 2W, camera, dual microphones and speaker, that walks with an RL-trained neural network, remembers people and events in an on-robot knowledge graph, and borrows LLM/vision/speech compute from any paired GPU machine on the LAN.

**Architecture:** *Body + Memory on the robot, Compute on the brains.* The Pi runs `milo-bridge` (drivers, gait policy inference, knowledge graph, discovery/auth, sleep mode). Any machine running the installable **Milo Brain** app (RTX 4050 laptop, RTX 5090 desktop) advertises itself over mDNS; Milo pairs once via a PIN shown on its OLED, then streams camera/mic audio up and receives speech/face/movement commands back. No brain reachable → Milo sleeps.

**Tech stack:** Python 3.11+ everywhere. Pi: `picamera2`, `sounddevice`, `adafruit-circuitpython-pca9685`, `luma.oled`, `onnxruntime`, `websockets`, `zeroconf`, SQLite. Brain: Ollama (LLM), `faster-whisper` (ASR), Silero VAD, InsightFace (faces), Piper (TTS), PyQt6 tray UI. Training: MuJoCo + Stable-Baselines3 PPO → ONNX.

## Global Constraints

- Robot memory (knowledge graph, person embeddings) lives **only** on the Pi; brains are stateless compute.
- All robot↔brain traffic authenticates with the pairing token (HMAC challenge–response); unpaired machines are refused.
- Gait engine exposes one interface — `set_velocity_command(vx, vy, yaw)` — with two backends: ONNX RL policy (primary) and CPG trot (fallback).
- Servo channel map matches the Sesame firmware naming: R1=0, R2=1, L1=2, L2=3, R4=4, R3=5, L3=6, L4=7.
- I2C addresses: PCA9685 `0x40`, SSD1306 `0x3C`, MPU6050 `0x68`.
- Camera stream: MJPEG 640×480 @ 15 fps. Mic stream: stereo 16-bit PCM @ 16 kHz.
- This standalone repo holds all Milo code (`common/`, `bridge/`, `brain/`, `training/`); the original ESP32 firmware/CAD stays untouched in [sesame-robot](https://github.com/dorianborian/sesame-robot) as the Sesame baseline (reference copies in `hardware/reference-sesame/`).
- Servos are powered ONLY from the 5A buck rail (PCA9685 V+), never from the Pi's 5V.

---

## Phase 0 — Procurement & Prep (before any assembly)

**Deliverable:** every part on the bench, verified working alone; repo scaffold committed.

- [ ] **0.1 Verify parts against the BOM** (spec §3.1, total ≈ LKR 54,600). Missing/at-risk items to confirm explicitly:
  - **MPU6050 IMU** (~LKR 450) — required addition, not in the original purchase list.
  - **Pi Zero CSI ribbon cable, 15→22 pin** — the standard Pi camera cable does **not** fit the Zero. Check the camera box; buy separately if absent.
  - 40-pin GPIO header for the Pi Zero 2W (ships headerless) + soldering supplies.
- [ ] **0.2 Flash the microSD** with Raspberry Pi OS Lite (64-bit, Bookworm) using Raspberry Pi Imager. In imager settings: hostname `milo`, enable SSH, set WiFi SSID/password (2.4 GHz network), locale.
- [ ] **0.3 First boot smoke test** — power the bare Pi from a bench 5V supply or USB:

```bash
ssh daham@milo.local
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config nonint do_i2c 0        # enable I2C
sudo apt install -y python3-pip python3-venv i2c-tools git
```

- [ ] **0.4 Scaffold the repo** and commit:

```
MILO-Robot/
├── README.md              # project overview, credits, links to spec + this plan
├── docs/                  # ARCHITECTURE.md, specs/, dev-logs/
├── common/                # milo-common: shared WS protocol + pairing/auth
│   ├── milo_common/
│   ├── tests/
│   └── pyproject.toml
├── bridge/                # Pi-side service
│   ├── milo_bridge/       #   package: drivers/, gait/, graph/, net/
│   ├── tests/
│   └── pyproject.toml
├── brain/                 # desktop Milo Brain app
│   ├── milo_brain/        #   package: pipelines/, llm/, ui/, net/
│   ├── tests/
│   └── pyproject.toml
├── training/              # MuJoCo model + RL training
│   ├── models/            #   milo.xml (MJCF), meshes
│   ├── milo_training/     #   env.py, train_ppo.py, export_onnx.py
│   └── tests/
└── hardware/reference-sesame/   # copied Sesame firmware reference files
```

```bash
git add -A && git commit -m "chore: scaffold Project Milo repo"
```

**Exit criterion:** parts inventory complete; Pi boots headless on WiFi; scaffold committed.

---

## Phase A — Hardware Assembly & Power

**Deliverable:** all electronics installed in the printed body, every device enumerating, servos sweeping.

### A.1 Power system (build and test BEFORE connecting anything expensive)

- [ ] Assemble: 2× 18650 in series → 2S BMS → KCD1 rocker switch → split to **Buck 1 (5V/2A → Pi + logic)** and **Buck 2 (5V/5A → PCA9685 V+ servo rail)**. Charger module wired battery-side, behind the switch.
- [ ] **Set both buck outputs to 5.1V with a multimeter BEFORE connecting any load.** An unadjusted buck can output 12V and kill the Pi instantly.
- [ ] Load-test Buck 2 with 2–3 servos sweeping; confirm no voltage sag below 4.8V.
- [ ] Common ground between both rails, the Pi, and every breakout. Heat-shrink every splice.

### A.2 Wiring (spec §3.3)

| GPIO | Pin | Function | Connects to |
|---|---|---|---|
| GPIO 2 | 3 | I2C SDA | PCA9685 + SSD1306 + MPU6050 |
| GPIO 3 | 5 | I2C SCL | PCA9685 + SSD1306 + MPU6050 |
| GPIO 18 | 12 | I2S BCLK | both INMP441 SCK + MAX98357A BCLK |
| GPIO 19 | 35 | I2S LRCLK | both INMP441 WS + MAX98357A LRC |
| GPIO 20 | 38 | I2S DATA IN | both INMP441 SD (shared line) |
| GPIO 21 | 40 | I2S DATA OUT | MAX98357A DIN |

- [ ] Solder the 40-pin header to the Pi Zero 2W.
- [ ] Mic A: L/R pin → **GND** (left channel), mounted left side of head. Mic B: L/R → **3.3V** (right), right side. Target 10–15 cm separation.
- [ ] MPU6050 mounted **rigidly** to the internal frame near body center (double-sided foam is too soft — use screws or hard standoffs; a floppy IMU ruins gait feedback).
- [ ] Camera ribbon (15→22 pin) into the CSI port; camera facing forward in the head.
- [ ] 8 servos into PCA9685 channels per the map in Global Constraints.

### A.3 Bring-up checks

- [ ] I2C scan shows all three devices:

```bash
i2cdetect -y 1
# Expect: 0x3c (OLED), 0x40 (PCA9685), 0x68 (MPU6050)
```

- [ ] Enable I2S mics + amp. In `/boot/firmware/config.txt`:

```ini
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard   # provides simultaneous I2S capture + playback
```

Reboot, then:

```bash
arecord -l                                    # capture device listed
arecord -D plughw:0 -c2 -r16000 -f S16_LE -d 3 test.wav
aplay test.wav                                # hear both mics through the speaker
```

If `googlevoicehat-soundcard` misbehaves, fall back to separate overlays (`dtoverlay=i2s-mems-mic` style capture + `dtoverlay=max98357a`) — resolve in a dev-log entry.

- [ ] Camera check: `rpicam-hello --list-cameras` shows imx219; `rpicam-jpeg -o test.jpg` produces an image.
- [ ] Servo sweep script (throwaway, `bridge/tools/servo_sweep.py`): sweep each channel 60°→120° one at a time; verify leg mapping matches R1/R2/L1/L2/R4/R3/L3/L4 and no brownout with all 8 moving.

**Exit criterion (spec Phase A):** `i2cdetect` shows 3 devices; stereo recording plays back; camera captures; all 8 servos sweep on the correct legs from battery power.

---

## Phase B — milo-bridge Core (Pi service)

**Deliverable:** a systemd service that stands, rests, waves and shows faces — the ESP32 firmware's baseline behavior, reimplemented in Python.

**Files:** `bridge/milo_bridge/drivers/{servos.py, display.py, imu.py, audio.py, camera.py}`, `milo_bridge/poses.py`, `milo_bridge/main.py`, `tests/` alongside.

- [ ] **B.1 Servo driver** (`servos.py`): PCA9685 @ 50 Hz, 500–2500 µs pulse range, `set_angle(channel, degrees)` with per-servo trim offsets and the staggered-activation delay lesson from the ESP32 firmware (default 20 ms between simultaneous multi-servo writes). Unit-test angle→duty math off-hardware (mock the I2C bus).
- [ ] **B.2 Pose library** (`poses.py`): port `rest`, `stand`, `wave`, `walk` keyframes from `firmware/movement-sequences.h` — the servo angles transfer directly since the body and horn geometry are unchanged. Each pose = list of (8-angle frame, duration ms).
- [ ] **B.3 Face display** (`display.py`): `luma.oled` SSD1306 driver. Convert face bitmaps from `firmware/face-bitmaps.h` PROGMEM arrays to PBM/PNG assets (one-off conversion script in `tools/`); implement `set_face(name)` + idle blink loop (3–7 s random, 30% double-blink — same behavior as firmware).
- [ ] **B.4 IMU driver** (`imu.py`): MPU6050 read at 100 Hz → complementary filter → `get_state()` returning roll, pitch, angular velocity (3-axis). Calibrate gyro bias on startup (robot must be still for 2 s).
- [ ] **B.5 Service entry** (`main.py`): asyncio main loop wiring drivers together; systemd unit `milo-bridge.service` (Restart=always, After=network-online.target).
- [ ] **B.6 Verify on hardware:** `sudo systemctl start milo-bridge` → Milo boots to rest pose with idle face; a CLI test command (`python -m milo_bridge.cli pose wave`) waves.

**Exit criterion (spec Phase B):** Milo stands, rests, waves, shows faces — headless via systemd, on battery.

---

## Phase C — Connectivity: Streaming, Discovery, Pairing, Brain Skeleton

**Deliverable:** the Milo Brain app skeleton runs on both GPU machines; Milo discovers, pairs (PIN on OLED), streams live video+audio, and fails over between brains.

**Files:** `bridge/milo_bridge/net/{discovery.py, auth.py, session.py, streams.py}`, `brain/milo_brain/{server.py, net/auth.py, ui/tray.py, config.py}`.

### Protocol (locked here; later phases build on it)

- Brain advertises mDNS `_milo-brain._tcp.local.` with TXT records: `name`, `gpu`, `tier` (small/large), `busy` (0/1).
- One WebSocket connection, multiplexed JSON + binary frames:
  - `{"t":"video", ...}` + binary MJPEG frame (robot→brain)
  - `{"t":"audio", ...}` + binary PCM chunk, 20 ms/frame (robot→brain)
  - `{"t":"tts"}` + binary PCM (brain→robot)
  - `{"t":"cmd", "face":..., "move":..., }` (brain→robot)
  - `{"t":"graph", "op":"upsert_node"|..., ...}` (brain→robot, Phase F)
- **Pairing:** unpaired brain connects → robot generates 6-digit PIN, renders it on the OLED → user types PIN into brain UI → both derive token = HKDF(PIN, robot_id, brain_id) → stored both sides (`/etc/milo/paired.json` on Pi). Every session opens with HMAC challenge–response over that token; failure → disconnect.

### Tasks

- [ ] **C.1** Brain skeleton: `milo_brain/server.py` — websockets server + zeroconf advertisement + tray icon (PyQt6) showing connection state. Config file (`~/.milo-brain/config.yaml`) holds machine name, tier, model choices.
- [ ] **C.2** Auth both sides (`auth.py`): pairing flow + HMAC session handshake. Unit-test: wrong token → refused; replayed challenge → refused.
- [ ] **C.3** Robot discovery client (`discovery.py`): browse mDNS, filter to paired brains, rank by (priority, latency), connect; on drop, reconnect/fail over within 10 s.
- [ ] **C.4** Camera + audio streaming (`streams.py`): picamera2 MJPEG encoder → video frames; sounddevice stereo capture → PCM frames. Brain displays live video and level meters in a debug window.
- [ ] **C.5** Sleep mode v1: no paired brain reachable → rest pose + sleepy face + streams stopped + keep scanning; brain found → stand + excited face + streams resume. Loud-sound perk-up: RMS threshold on a low-cost mic tap even while asleep.
- [ ] **C.6** Test the full matrix: pair laptop, pair desktop, kill one → failover to the other, kill both → sleep, restart one → wake. Measure Pi CPU: streaming must leave ≥ 40% headroom for the Phase D policy (drop to 10 fps if not).

**Exit criterion (spec Phase C):** live video/audio visible on a brain machine; PIN pairing under 2 minutes; failover and sleep/wake work; unpaired machine cannot connect.

---

## Phase D — Gait: Simulation, PPO Training, Deployment

**Deliverable:** Milo walks forward and turns on the real floor driven by a trained neural network. **Write a detailed per-phase implementation plan before starting — this is the highest-risk phase.**

**Files:** `training/models/milo.xml`, `training/{env.py, train_ppo.py, export_onnx.py}`, `bridge/milo_bridge/gait/{engine.py, policy.py, cpg.py}`.

- [ ] **D.1 Measure the real robot:** total mass, per-leg link lengths, CoM shift from the Pi+battery vs the ESP32 build, and **servo step response** (command a 30° step, film at 120 fps or log IMU, fit time constant — expect 20–40 ms lag on MG90s). These numbers go straight into the sim.
- [ ] **D.2 MuJoCo model** (`milo.xml`): start from the community Sesame Simulator URDF geometry, convert to MJCF, set measured masses/inertias, position actuators with measured lag (`actuator` gainprm / filter). Sanity check: model stands under gravity in the MuJoCo viewer at the `stand` pose angles.
- [ ] **D.3 Gym env** (`env.py`): observation (~30 dims: 8 joint pos, 8 prev actions, roll/pitch, 3 ang-vel, 3 gravity vec, 3 command), action = 8 target-angle deltas from stand pose (clamped ±25°), reward = velocity tracking + upright − energy − foot-slip − fall. Episode: 10 s or fall.
- [ ] **D.4 Domain randomization:** friction (0.6–1.4×), servo strength (0.8–1.2×), latency (10–50 ms), mass (±10%), IMU noise, random pushes every 2–4 s. This is what makes sim policies survive real MG90s — do not skip.
- [ ] **D.5 Train PPO** (Stable-Baselines3, policy MLP 2×64) on the 5090 box (or 4050 — this trains in hours either way at ~10k steps/s vectorized). Success in sim: tracks 0.1 m/s forward and ±30°/s yaw commands without falling across randomization draws.
- [ ] **D.6 Export + deploy:** `export_onnx.py` → `policy.onnx`; `gait/policy.py` runs it via onnxruntime at 50 Hz on the Pi (benchmark: must be < 5 ms/step — expect < 1 ms). `gait/engine.py` exposes `set_velocity_command(vx, vy, yaw)` and owns the 50 Hz control loop reading the IMU.
- [ ] **D.7 CPG fallback** (`cpg.py`): parameterized trot (per-leg sine hip/knee, tunable amplitude/frequency/phase) behind the same interface, hand-tuned until it walks. Build this FIRST if D.5 stalls — Milo must always be able to walk.
- [ ] **D.8 Sim-to-real iteration:** run the policy on carpet and smooth floor; when it fails, adjust randomization ranges/measured lag and retrain. Budget 3–5 iterations. Film everything for the dev-log.

**Exit criterion (spec Phase D):** Milo walks forward and turns left/right on the real floor on velocity commands from the RL policy; CPG fallback also walks.

---

## Phase E — Perception (on the brain)

**Deliverable:** Milo turns toward a voice, recognizes a known face, and transcribes speech. **Write a per-phase implementation plan before starting.**

**Files:** `brain/milo_brain/pipelines/{vad.py, direction.py, asr.py, vision.py}`.

- [ ] **E.1 VAD** (`vad.py`): Silero VAD over the incoming 16 kHz stream; emits speech segments with timestamps.
- [ ] **E.2 Sound direction** (`direction.py`): GCC-PHAT cross-correlation between L/R channels over each VAD segment → inter-mic delay → bearing estimate (left/center/right is sufficient; the mic baseline of 10–15 cm gives ±~40 µs max delay ≈ coarse angle only). Feed `{"t":"cmd","move":{"turn": bearing}}` so Milo orients toward speakers.
- [ ] **E.3 ASR** (`asr.py`): faster-whisper (`small` on the 4050, `medium` on the 5090, set by tier config) on VAD segments → transcript + confidence.
- [ ] **E.4 Vision** (`vision.py`): InsightFace (`buffalo_l`) on the MJPEG stream at 2–5 fps analysis rate → per-face bbox + 512-d embedding; call robot graph `match_face` (Phase F provides it; until then, match against a local stub) → identity or `unknown`.
- [ ] **E.5 VRAM check on the 4050 (6 GB):** whisper-small (~1 GB) + InsightFace (~0.5 GB) + 3B LLM Q4 (~2.5 GB) must coexist; if tight, run InsightFace on CPU — it's fast enough at 2 fps.

**Exit criterion (spec Phase E):** Milo turns toward a clap/voice; a known face is matched to its name; speech is transcribed live in the brain debug window.

---

## Phase F — Memory & Cognition: Knowledge Graph + LLM Loop

**Deliverable:** Milo greets a returning person by name and recalls stored facts — even after a power cycle, even on a different brain. **Write a per-phase implementation plan before starting.**

**Files:** `bridge/milo_bridge/graph/{store.py, api.py}`, `brain/milo_brain/{llm/agent.py, llm/extract.py, pipelines/tts.py}`.

### Graph schema (SQLite on the Pi, `~/.milo/graph.db`)

```sql
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,            -- person | place | object | event | fact
  props TEXT NOT NULL,           -- JSON: {"name": "Daham", ...}
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE edges (
  id INTEGER PRIMARY KEY,
  src INTEGER NOT NULL REFERENCES nodes(id),
  dst INTEGER NOT NULL REFERENCES nodes(id),
  type TEXT NOT NULL,            -- knows | said | seen_at | likes | participated_in
  props TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE face_embeddings (
  node_id INTEGER NOT NULL REFERENCES nodes(id),  -- person node
  embedding BLOB NOT NULL,       -- 512× float32
  created_at TEXT NOT NULL
);
```

- [ ] **F.1 Graph store** (`store.py`): typed CRUD over that schema + `match_face(embedding, threshold=0.45)` via brute-force cosine over `face_embeddings` (fine for hundreds of people on the Pi). Unit-test off-hardware.
- [ ] **F.2 Graph API** (`api.py`): expose `upsert_node`, `upsert_edge`, `query`, `neighbors`, `recent_events`, `match_face` as `{"t":"graph", ...}` messages on the existing authenticated WebSocket. Nightly `sqlite3 .backup` to a timestamped file; optional copy-to-brain export.
- [ ] **F.3 TTS** (`tts.py`): Piper (en, medium voice) on the brain → 16 kHz PCM → `{"t":"tts"}` frames → Milo's speaker, with talk-face animation while audio plays.
- [ ] **F.4 Cognition agent** (`agent.py`): the loop from spec §6 — on each utterance: identity (E.4) + transcript (E.3) + graph context (`neighbors` of speaker + `recent_events`) → Ollama chat with structured JSON output `{reply, face, move, facts[]}` → send TTS + cmd, write `facts[]` to the graph. Models: `llama3.2:3b` (tier small) / 8B-class (tier large), switchable in the tray UI.
- [ ] **F.5 Unknown-person flow:** vision reports `unknown` → agent asks "Hi! I don't think we've met — what's your name?" → next transcript becomes the name → create person node + store the session's embeddings.
- [ ] **F.6 The portability test (core promise):** teach Milo a fact via the laptop brain, power-cycle the robot, connect via the desktop brain — Milo must greet by name and recall the fact.

**Exit criterion (spec Phase F):** F.6 passes.

---

## Phase G — Integration, Endurance & Polish

**Deliverable:** all spec success criteria pass; project documented.

- [ ] **G.1** GPU-busy handling: brain sets `busy=1` in mDNS TXT + sends a sleep command when its GPU is loaded (user threshold in config); Milo prefers a non-busy paired brain before sleeping.
- [ ] **G.2** Battery endurance test: full interactive session; log voltage over time; require > 3 h. Add a low-battery face + graceful shutdown at cutoff.
- [ ] **G.3** Run the full success-criteria checklist (spec §10, all 8 items) and record results in a dev-log entry with video.
- [ ] **G.4** Docs: `README.md` (setup for Pi and brain machines from scratch), update root `README.md` with a Project Milo section, final dev-log build report.
- [ ] **G.5** Merge the `milo` branch (PR against `main`).

---

## Timeline & Effort (evenings/weekends pace)

| Phase | Estimate | Notes |
|---|---|---|
| 0 + A | 1–2 weeks | Soldering + power bring-up is the slow part |
| B | 1 week | Mostly porting known firmware behavior |
| C | 1–2 weeks | Protocol + auth is new ground |
| D | 2–4 weeks | Highest variance — sim-to-real iterations |
| E | 1 week | Mature libraries, integration work |
| F | 1–2 weeks | Graph + agent loop |
| G | 1 week | Testing + docs |
| **Total** | **~8–13 weeks** | |

## Risk register (top 5, from spec §11)

1. **Sim-to-real gap** — mitigate with measured servo lag in sim + aggressive domain randomization + CPG fallback (D.7 exists before D.5 ships).
2. **Pi Zero 2W CPU saturation** — measured gate in C.6 (≥ 40% headroom before Phase D); degrade fps first.
3. **6 GB VRAM contention on the 4050** — tier config keeps models small there; InsightFace to CPU if needed (E.5).
4. **WiFi jitter on 2.4 GHz** — 20 ms audio frames + jitter buffer; UDP escape hatch if WebSocket audio stutters.
5. **Servo brownout** — dedicated 5A rail + staggered activation (A.1, B.1); never power servos from the Pi.

## Kick-start: the first three actions

1. Order the **MPU6050** and confirm the **Zero CSI ribbon cable** (Phase 0.1).
2. Flash the SD and get the bare Pi on WiFi (0.2–0.3).
3. Solder the header, build and *voltage-check* the power rails (A.1) — everything else hangs off that.
