# Project Milo — Design Spec

**Date:** 2026-07-05
**Author:** Daham Dissanayake
**Status:** Approved — ready for implementation planning
**Supersedes:** `2026-06-29-sesame-robot-upgrade-design.md` (hardware sections carried forward; software architecture revised)

---

## 1. What Project Milo Is

Milo is the intelligent successor to the Sesame Robot. It keeps the same printed quadruped body and 8× MG90 servo layout, but replaces the ESP32 with a Raspberry Pi Zero 2W and adds a camera, dual I2S microphones, and a speaker. Three capabilities define the project:

1. **Learned walking** — a neural network gait policy trained with reinforcement learning in simulation, deployed on the Pi.
2. **Persistent memory** — a knowledge graph stored **on the robot itself**, recording people (with names and face embeddings), events, and facts gathered through its camera and microphones.
3. **Detachable intelligence** — an installable "Milo Brain" application that runs on any LAN machine with a GPU. Brains are discoverable, authenticated, and interchangeable; they provide LLM, speech, and vision compute while the robot keeps its own memory.

### Design principle: Body + Memory on the robot, Compute on the brains

The robot is never dependent on one specific computer for its identity. Any paired brain machine gives it intelligence; its knowledge graph — who it knows, what it has seen — always lives on the Pi. When no brain is reachable, Milo sleeps.

---

## 2. System Architecture

```
┌────────────────── MILO (Raspberry Pi Zero 2W) ──────────────────┐
│  milo-bridge (Python, systemd service)                          │
│  ├─ Drivers: PCA9685 (8 servos), SSD1306 face, IMX219 camera,   │
│  │           2× INMP441 mic in, MAX98357A speaker out,          │
│  │           MPU6050 IMU                                        │
│  ├─ Gait engine: ONNX policy inference @ 50 Hz (+ CPG fallback) │
│  ├─ Knowledge graph: SQLite property graph + local graph API    │
│  ├─ Brain discovery client (mDNS) + pairing auth (token)        │
│  └─ Sleep mode controller (no brain connected → rest + sleepy)  │
└──────────────────────────┬──────────────────────────────────────┘
                 WiFi LAN  │  mDNS service: _milo-brain._tcp
        ┌──────────────────┴───────────────────┐
        │                                      │
┌───────▼────── Brain A ───────┐   ┌───────────▼── Brain B ──────┐
│ Laptop, RTX 4050 6GB         │   │ Desktop, RTX 5090 32GB      │
│ Milo Brain app               │   │ Milo Brain app (same app)   │
│ • LLM: 3B class (Ollama)     │   │ • LLM: 8B+ class (Ollama)   │
│ • Whisper-small ASR          │   │ • Whisper-medium ASR        │
│ • InsightFace vision         │   │ • InsightFace vision        │
│ • Piper TTS                  │   │ • Piper TTS                 │
└──────────────────────────────┘   └─────────────────────────────┘
```

**Data flows (robot ↔ active brain, all over authenticated WebSocket):**

- Camera → Pi encodes MJPEG 640×480 @ 15fps → brain
- Mics → Pi streams stereo 16-bit PCM @ 16kHz → brain
- Brain → TTS PCM audio → Pi plays through MAX98357A
- Brain → JSON commands `{face, move, speak}` → Pi executes
- Brain ↔ Pi graph API: `query` / `upsert` / `neighbors` / `recent-events`

---

## 3. Hardware

### 3.1 Purchased BOM (actual prices)

| Item | Qty | Unit (LKR) | Total (LKR) |
|---|---|---|---|
| MG90 metal-gear servo full set (RB0057) | 8 | 580 | 4,640 |
| 0.96" 128×64 OLED I2C (DM0037) | 1 | 680 | 680 |
| KCD1 rocker power switch | 1 | 40 | 40 |
| Wire kit | 1 | 300 | 300 |
| Heat-shrink assortment | 1 | 200 | 200 |
| Raspberry Pi Zero 2 W | 1 | 23,400 | 23,400 |
| Pi Camera V2.1 (IMX219, 8MP, original) | 1 | 9,500 | 9,500 |
| 3D printed body | 1 | 5,900 | 5,900 |
| INMP441 I2S microphone | 2 | 450 | 900 |
| PCA9685 16-ch PWM servo driver (MD0223) | 1 | 1,000 | 1,000 |
| MAX98357 I2S 3W amp (MD0860) | 1 | 500 | 500 |
| Speaker | 1 | 200 | 200 |
| 2× 18650 + BMS + bucks + charger | 1 | 4,000 | 4,000 |
| microSD | 1 | 2,500 | 2,500 |
| **Subtotal** | | | **53,760** |
| **MPU6050 IMU (required addition — see 3.2)** | 1 | ~450 | ~450 |
| Pi Zero CSI ribbon cable 15→22 pin (verify included with camera) | 1 | ~400 | ~400 |
| **Total** | | | **~54,600** |

### 3.2 Required addition: IMU

The gait policy needs body orientation and angular velocity as observations — the robot cannot learn or execute balanced walking blind. An **MPU6050** (I2C, address 0x68) joins the existing I2C bus alongside the PCA9685 (0x40) and SSD1306 (0x3C). Mount rigidly to the internal frame, near the body center.

### 3.3 Wiring map (Pi Zero 2W)

| GPIO | Pin | Function | Connects to |
|---|---|---|---|
| GPIO 2 | 3 | I2C SDA | PCA9685 + SSD1306 + MPU6050 |
| GPIO 3 | 5 | I2C SCL | PCA9685 + SSD1306 + MPU6050 |
| GPIO 18 | 12 | I2S BCLK | INMP441 ×2 SCK + MAX98357A BCLK |
| GPIO 19 | 35 | I2S LRCLK | INMP441 ×2 WS + MAX98357A LRC |
| GPIO 20 | 38 | I2S DATA IN | INMP441 ×2 SD (shared; L/R pin selects channel) |
| GPIO 21 | 40 | I2S DATA OUT | MAX98357A DIN |
| CSI | — | Camera | IMX219 via Zero ribbon |
| 5V | 2/4 | Power in | Buck 1 (logic rail) |
| GND | multiple | Ground | Common ground |

Mic A: L/R pin → GND (left channel), left side of head. Mic B: L/R → 3.3V (right channel), right side. Target 10–15cm separation for usable inter-channel delay.

### 3.4 Power

Carried forward from the 2026-06-29 spec: 2S 18650 pack → BMS → KCD1 switch → two buck converters. Buck 1 (5V/2A) → Pi + all logic. Buck 2 (5V/5A) → PCA9685 V+ servo rail. Charger module wired behind the switch. Estimated runtime ~3.5h at average interactive load.

---

## 4. Gait Learning (sim-to-real RL)

**Decision: train in simulation on a GPU brain machine, deploy the policy to the Pi.** On-robot RL training was rejected (destroys MG90 servos, Pi cannot train); CPG-only was rejected as primary (user goal is a genuine learned neural gait) but retained as fallback.

### 4.1 Pipeline

1. **Model:** Build a MuJoCo MJCF model of Milo from the community Sesame Simulator URDF, updated with measured masses/dimensions of the upgraded body (Pi + battery shift the center of mass vs. the ESP32 build).
2. **Train:** PPO (Stable-Baselines3 or CleanRL) on the RTX machine.
   - **Observations (~30 dims):** 8 joint positions, 8 previous actions, IMU orientation (roll/pitch), angular velocity (3), gravity vector (3), command (vx, vy, yaw-rate).
   - **Actions:** 8 target joint angles (delta from a standing pose, clamped).
   - **Rewards:** forward velocity tracking, upright bonus, energy/torque penalty, foot-slip penalty, fall penalty.
   - **Domain randomization:** ground friction, servo strength/latency (MG90s are slow and sloppy — model 20–40ms lag and ±3° backlash), mass ±10%, sensor noise, push perturbations.
3. **Export:** trained MLP (2×64 hidden, <50k params) → ONNX.
4. **Deploy:** `onnxruntime` on the Pi runs inference at 50 Hz; outputs go through the same servo abstraction as scripted poses. Well within Pi Zero 2W budget (sub-millisecond per step).

### 4.2 Fallback

The gait engine exposes one interface (`set_velocity_command(vx, vy, yaw)`) with two implementations: the RL policy and a parameterized CPG (trot gait, sine-based). If sim-to-real transfer stalls, Milo still walks while training iterates.

---

## 5. Knowledge Graph (on the Pi)

SQLite-backed property graph in `milo-bridge`:

- **Nodes:** `person`, `place`, `object`, `event`, `fact` — JSON properties, created/updated timestamps. Person nodes hold name and one or more face-embedding blobs (512-float vectors from InsightFace).
- **Edges:** typed and timestamped (`knows`, `said`, `seen_at`, `likes`, `participated_in`, …).
- **API:** authenticated local HTTP + WebSocket endpoints: `upsert_node`, `upsert_edge`, `query` (by type/property/text), `neighbors`, `recent_events`, `match_face` (embedding similarity search over person nodes).

**Division of labor:** brains do all extraction (LLM turns conversation into graph facts; vision produces embeddings) and write results through the API. The Pi only stores, indexes, and serves — well within 512MB RAM. Nightly local backup of the DB file; optional export to the desktop for safekeeping.

---

## 6. Milo Brain Application

One installable app (Python; system-tray UI), identical on every machine.

- **Advertise:** mDNS `_milo-brain._tcp` with machine name, GPU info, model tier, and load status.
- **Pairing:** first connection from a robot triggers a pairing flow — Milo displays a 6-digit PIN on its OLED face; the user types it into the brain app; both sides derive and store a persistent shared token. All subsequent WebSocket sessions authenticate with that token (HMAC challenge–response); unpaired brains are ignored.
- **Brain selection:** Milo maintains a paired-brain list with priority; it connects to the highest-priority reachable brain and fails over if the connection drops. The brain app can also mark itself "busy" (GPU under load) which sends Milo to sleep.
- **Per-machine model config:** the app detects the GPU and defaults accordingly — RTX 4050 6GB: 3B-class LLM (e.g. Llama 3.2 3B via Ollama) + Whisper-small; RTX 5090 32GB: 8B+ LLM + Whisper-medium. The model is user-changeable in the UI at any time.
- **Pipelines hosted:** VAD (Silero) → GCC-PHAT sound direction → faster-whisper ASR → LLM (with graph context fetched from Milo) → Piper TTS; InsightFace detection/embedding on the video stream.

### Cognition loop

1. Person speaks → VAD gates ASR → transcript + direction.
2. Vision matches current face against `match_face` on Milo's graph → identity (or unknown).
3. Brain fetches relevant graph context (`neighbors` of the person node, `recent_events`).
4. LLM (structured output) returns: reply text, face expression, movement intent, and zero or more graph facts.
5. Brain sends TTS audio + face + move to Milo, writes facts back to the graph.
6. Unknown person flow: Milo asks their name; on answer, a new person node with embeddings is created.

---

## 7. Sleep / Wake

- **No paired brain reachable, or active brain reports busy →** Milo plays the rest pose, shows the sleepy face, idle-blinks. Camera and mic streams stop (saves power); the discovery client keeps scanning; a loud sound (simple on-Pi RMS threshold) makes Milo perk up briefly and rescan.
- **Brain connects / frees up →** stand pose, excited face, streams resume.

---

## 8. Repository & Naming

Work proceeds in this repository. New top-level directories:

- `milo/bridge/` — Pi-side service (drivers, gait engine, graph, discovery)
- `milo/brain/` — desktop brain application
- `milo/training/` — MuJoCo model, RL training code, export scripts
- `dev-logs/` — project plan and build logs

Existing ESP32 firmware, CAD, and docs remain untouched as the Sesame baseline.

---

## 9. Build Phases

| Phase | Scope | Exit criterion |
|---|---|---|
| **A — Hardware** | Assemble Pi, PCA9685, IMU, camera, mics, amp, power into the printed body | All devices enumerate (`i2cdetect`, `arecord`, `libcamera-hello`); servos sweep from a test script |
| **B — Bridge core** | milo-bridge service: servo/face/IMU drivers, scripted poses ported from firmware | Milo stands, rests, waves, shows faces — headless via systemd |
| **C — Connectivity** | Camera/audio streaming, brain app skeleton, mDNS discovery, PIN pairing, auth | Laptop app shows live video/audio; pairing + failover between two machines works |
| **D — Gait** | MuJoCo model, PPO training, domain randomization, ONNX deploy | Milo walks forward/turns on the real floor on policy commands; CPG fallback works |
| **E — Perception** | VAD, sound direction, ASR, face detect/recognize on brain | Milo turns toward a voice; recognizes a known face; transcribes speech |
| **F — Cognition & memory** | Knowledge graph + API on Pi, LLM loop, TTS, unknown-person naming flow | Milo greets a returning person by name and recalls a stored fact about them |
| **G — Integration** | Sleep/wake, GPU-busy handling, battery runtime testing, polish | All success criteria below pass |

---

## 10. Success Criteria

1. Milo walks with the trained neural policy (not scripted gait) on command: forward, turn left/right.
2. Milo recognizes a returning person by face and greets them by name.
3. Milo turns toward the direction of a voice.
4. Milo holds a spoken conversation via a brain machine LLM with matching facial expressions.
5. Milo remembers a fact told in a previous session (stored in the on-Pi graph) after a full power cycle **and** when connected to a different brain machine.
6. Pairing a fresh brain machine takes under two minutes via the PIN flow; unpaired machines cannot connect.
7. Milo sleeps when no brain is available and wakes on reconnection.
8. Battery runtime exceeds 3 hours of normal interactive use.

---

## 11. Key Risks

| Risk | Mitigation |
|---|---|
| Sim-to-real gap: MG90s are slow/sloppy vs. ideal sim actuators | Aggressive domain randomization of latency/backlash; measure real servo step response and encode into sim; CPG fallback |
| Pi Zero 2W CPU saturation (camera encode + audio + policy + graph) | 640×480@15fps hardware-assisted MJPEG; policy is tiny; profile in Phase C; drop fps before dropping features |
| 6GB VRAM contention on the RTX 4050 (LLM + Whisper + InsightFace) | 3B LLM default on that machine; sequential model loading; heavy tier reserved for the 5090 box |
| 2.4GHz-only WiFi congestion / stream latency | Local network only; UDP option for audio if WebSocket jitters; QoS testing in Phase C |
| Servo current spikes browning out the Pi | Separate 5A servo buck rail (spec'd); staggered servo activation carried over from the ESP32 firmware lessons |
| No CSI cable for Zero form factor | Verify the 15→22-pin ribbon is in hand before Phase A |
