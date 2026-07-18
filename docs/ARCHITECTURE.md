# MILO — System Architecture & Build Reference

**Date:** 2026-07-05
**Author:** Daham Dissanayake
**Status:** Approved baseline for implementation
**Derived from:** the approved [Project Milo design spec](specs/2026-07-05-project-milo-design.md) (originally written in the [sesame-robot](https://github.com/dorianborian/sesame-robot) repository), adapted to this standalone repo.

This is the single reference for **what Milo is made of, how every part connects (electrically and over the network), how the repository is laid out, and what technology each part uses**. Read this before touching code or a soldering iron.

---

## 1. What Milo Is

Milo upgrades the [Sesame quadruped robot](https://github.com/dorianborian/sesame-robot) by Dorian Todd — same 3D-printed body, same 8× MG90 servo layout — from a WiFi-remote-controlled toy into a free-roaming, seeing, hearing, remembering robot:

| | Sesame (baseline) | Milo (this repo) |
|---|---|---|
| Controller | ESP32 (Arduino C++) | Raspberry Pi Zero 2W (Python 3.11) |
| Locomotion | Scripted keyframe gaits | RL-trained neural gait policy (ONNX @ 50 Hz) + CPG fallback |
| Senses | None | IMX219 camera, 2× I2S mics, MPU6050 IMU |
| Voice | None | Speaker (I2S amp), TTS, conversational LLM |
| Memory | None | On-robot SQLite knowledge graph (people, faces, events, facts) |
| Intelligence | Remote HTTP commands | Detachable **Milo Brain** desktop app on any LAN GPU machine |
| Face display | SSD1306 OLED, 37 bitmap faces | Same display + same face art, driven from Python |

**Design principle — *Body + Memory on the robot, Compute on the brains*.** The Pi runs `milo-bridge` (drivers, gait inference, knowledge graph, WebSocket server + mDNS advertising, sleep mode). Milo itself advertises on the LAN and accepts connections; a brain machine discovers it and dials in. Pairing (once per brain, triggered from the robot's web dashboard) shows a PIN on Milo's OLED that's typed into the brain app; after that, streaming and reconnection are automatic. Milo's identity (who it knows, what it remembers) always lives on the Pi — brains are stateless, interchangeable compute. No brain connected → Milo stands by, self-leveling, waiting (see §3.4).

---

## 2. Repository File Structure

Standalone layout (the original plan's `milo/` prefix is dropped — this whole repo *is* Milo):

```
MILO-Robot/
├── README.md                      # project intro, credits, quickstart
├── LICENSE                        # Apache 2.0 (derivative of sesame-robot)
├── project-milo-plan.md           # A–Z phased build plan (paths adapted to this layout)
│
├── docs/
│   ├── ARCHITECTURE.md            # ← this document
│   ├── specs/                     # design specs (original approved Milo spec copied in)
│   └── dev-logs/                  # build logs; sesame firmware deep-dive reference
│
├── common/                        # shared robot↔brain code (installed on BOTH sides)
│   ├── pyproject.toml             # package: milo-common
│   ├── milo_common/
│   │   ├── protocol.py            # WebSocket message framing (JSON + binary), msg types
│   │   └── auth.py                # PIN pairing, HKDF token derivation, HMAC handshake
│   └── tests/
│
├── bridge/                        # ═══ RASPBERRY PI SERVICE (milo-bridge) ═══
│   ├── pyproject.toml             # package: milo-bridge (depends on milo-common)
│   ├── milo_bridge/
│   │   ├── main.py                # asyncio entrypoint: wires drivers+gait+net+graph
│   │   ├── cli.py                 # `python -m milo_bridge.cli pose wave` etc.
│   │   ├── config.py              # paths, servo trims, thresholds (~/.milo/config)
│   │   ├── poses.py               # rest/stand/wave/walk… keyframes ported from firmware
│   │   ├── sleep.py               # sleep/wake controller (no brain → rest + sleepy face)
│   │   ├── drivers/
│   │   │   ├── servos.py          # PCA9685 @50Hz, trims, staggered writes
│   │   │   ├── display.py         # SSD1306 faces + idle blink loop (luma.oled)
│   │   │   ├── imu.py             # MPU6050 @100Hz → complementary filter
│   │   │   ├── camera.py          # picamera2 MJPEG 640×480@15fps
│   │   │   └── audio.py           # arecord/aplay stereo 16kHz capture + PCM playback
│   │   ├── gait/
│   │   │   ├── engine.py          # 50Hz control loop; set_velocity_command(vx,vy,yaw)
│   │   │   ├── policy.py          # ONNX RL policy backend (onnxruntime)
│   │   │   └── cpg.py             # parameterized trot fallback backend
│   │   ├── graph/
│   │   │   ├── store.py           # SQLite property graph + match_face (cosine)
│   │   │   └── api.py             # {"t":"graph"} ops over the authenticated WS
│   │   └── net/
│   │       ├── advertiser.py      # registers/updates the _milo-robot._tcp mDNS service
│   │       ├── pairing.py         # generates+shows the OLED PIN, gates robot_handshake
│   │       ├── server.py          # accepts the one connected brain, runs the handshake
│   │       ├── session.py         # post-handshake dispatch loop (RobotSession)
│   │       └── streams.py         # camera/mic → WS frames; TTS PCM → speaker
│   ├── assets/faces/              # PNG face frames converted from face-bitmaps.h
│   ├── tools/
│   │   ├── servo_sweep.py         # hardware bring-up: sweep each channel
│   │   └── convert_faces.py       # one-off: firmware PROGMEM bitmaps → PNGs
│   ├── systemd/milo-bridge.service
│   └── tests/                     # all off-hardware (mocked I2C/I2S/camera)
│
├── brain/                         # ═══ DESKTOP MILO BRAIN APP ═══
│   ├── pyproject.toml             # package: milo-brain (depends on milo-common)
│   ├── milo_brain/
│   │   ├── __main__.py            # `python -m milo_brain`
│   │   ├── config.py              # ~/.milo-brain/config.yaml (name, tier, models)
│   │   ├── net/
│   │   │   ├── discovery.py       # mDNS browse _milo-robot._tcp, rank paired robots
│   │   │   └── connector.py       # discover→select→connect→session loop + manual connect
│   │   ├── pipelines/
│   │   │   ├── vad.py             # Silero VAD → speech segments
│   │   │   ├── direction.py       # GCC-PHAT L/R delay → bearing
│   │   │   ├── asr.py             # faster-whisper (small/medium by tier)
│   │   │   ├── vision.py          # InsightFace detect + 512-d embeddings
│   │   │   └── tts.py             # Piper → 16kHz PCM frames
│   │   ├── llm/
│   │   │   ├── agent.py           # cognition loop (Ollama, structured JSON out)
│   │   │   └── token_rate.py      # tokens/sec tracker shown on the dashboard
│   │   └── tui/                   # Textual TUI: dashboard, Connect Robots, model picker
│   └── tests/                     # models mocked; DSP math tested for real
│
├── training/                      # ═══ RL GAIT TRAINING (GPU machine) ═══
│   ├── pyproject.toml
│   ├── models/milo.xml            # MuJoCo MJCF (from Sesame sim geometry + measurements)
│   ├── milo_training/
│   │   ├── env.py                 # Gymnasium env: obs ~30d, action 8 deltas, DR
│   │   ├── train_ppo.py           # Stable-Baselines3 PPO
│   │   └── export_onnx.py         # policy MLP → policy.onnx for the Pi
│   └── tests/
│
└── hardware/
    ├── reference-sesame/          # copied verbatim from dorianborian/sesame-robot:
    │   ├── movement-sequences.h   #   servo pose source of truth (angles ported to poses.py)
    │   └── face-bitmaps.h         #   OLED face art source (converted to assets/faces/)
    └── (BOM + wiring live in this document, §4–§5)
```

**Why three installable packages?** `milo-common` holds the protocol and auth code that must byte-for-byte agree on both ends of the WebSocket. The Pi installs `milo-common + milo-bridge`; a desktop installs `milo-common + milo-brain`; the training package is only ever installed on a GPU box. Each package is independently testable off-hardware.

---

## 3. System Architecture & How Everything Connects

### 3.1 Big picture

```
┌───────────────────────── MILO (Raspberry Pi Zero 2W) ─────────────────────────┐
│                     milo-bridge  (Python asyncio, systemd)                    │
│                                                                               │
│   drivers/                gait/engine (50 Hz)         graph/ (SQLite)         │
│   ┌─────────┐  angles ┌──────────────────────┐   ┌────────────────────┐       │
│   │ servos  │◄────────┤ policy.onnx │ cpg.py │   │ nodes/edges/faces  │       │
│   │ display │         └──────▲───────────────┘   │ match_face()       │       │
│   │ imu ────┼── roll/pitch/ω─┘                   └─────────▲──────────┘       │
│   │ camera ─┼── MJPEG ──────────────┐                      │ graph ops        │
│   │ audio ──┼── PCM ────────────┐   │                      │                  │
│   └─────────┘                   │   │                      │                  │
│        ▲                net/streams ── net/server ───┴── net/advertiser       │
│        │ poses/faces/tts        │   │            ▲                 │          │
│     sleep.py ◄──────────────────┴───┴────────────┴─────────────────┘          │
└────────────────────────────────────┬──────────────────────────────────────────┘
                    WiFi LAN (2.4GHz)│ mDNS: _milo-robot._tcp   one authenticated
                     (robot advertises,│ (robot advertised)      WebSocket at a time
                      brains discover) │                          (robot is the server)
            ┌────────────────────────┴──────────────────────┐
            │                                               │
┌───────────▼─────── Brain A ──────────┐   ┌────────────────▼── Brain B ────────┐
│ Laptop · RTX 4050 6GB · tier=small   │   │ Desktop · RTX 5090 32GB · tier=large│
│           Milo Brain app             │   │        Milo Brain app (same)        │
│ VAD → direction → ASR(whisper-small) │   │ VAD → direction → ASR(whisper-medium)│
│ InsightFace · Piper TTS              │   │ InsightFace · Piper TTS             │
│ LLM: llama3.2:3b via Ollama          │   │ LLM: 8B-class via Ollama            │
│ Textual TUI (net/discovery+connector)│   │ Textual TUI (net/discovery+connector)│
└──────────────────────────────────────┘   └─────────────────────────────────────┘
```

### 3.2 Data flows (all on ONE multiplexed, authenticated WebSocket)

| Direction | Frame | Content | Rate |
|---|---|---|---|
| robot → brain | `{"t":"video"}` + binary | MJPEG frame 640×480 | 15 fps (degrade to 10 if CPU-bound) |
| robot → brain | `{"t":"audio"}` + binary | stereo 16-bit PCM @ 16 kHz | 20 ms chunks |
| brain → robot | `{"t":"tts"}` + binary | mono 16-bit PCM @ 16 kHz | streamed |
| brain → robot | `{"t":"cmd", "face":…, "move":…, "speak_done":…}` | face + movement intents | event |
| brain → robot | `{"t":"graph", "op":"upsert_node"\|"upsert_edge"\|"query"\|"neighbors"\|"recent_events"\|"match_face", …}` | knowledge-graph ops | event |
| both | `{"t":"hello"}`, `{"t":"challenge"}`, `{"t":"auth"}`, `{"t":"pair_*"}` | handshake / pairing | connect |

Binary frames immediately follow their JSON header frame; the header carries `seq`, timestamps, and payload length so either side can re-sync.

### 3.3 Pairing & authentication (milo_common.auth)

The robot is the WebSocket server and mDNS advertiser; a brain discovers it and dials in (`net/discovery.py` + `net/connector.py` on the brain side). Pairing is triggered from the **robot's web dashboard**, not the brain app:

1. User clicks **Enter Pairing Mode** on the robot's web dashboard (Brain card) → the robot generates a 6-digit PIN, renders it on the OLED, and flips its mDNS `pairing` TXT flag on.
2. In the brain app's **Connect Robots** tab, the user refreshes, sees the robot listed as pairing-available, and selects it.
3. The brain dials in; the robot's handshake reactively requests the PIN, which pops a modal in the brain's TUI — the user types the code shown on the robot's face.
4. Both sides derive `token = HKDF(PIN, salt=robot_id‖brain_id)` and persist it (`~/.milo/paired.json` on the Pi, `~/.milo-brain/paired.json` on the brain); the robot closes pairing mode automatically.
5. Every later session opens with an HMAC challenge–response over that token (fresh nonce per session; replays refused) — no PIN prompt, no manual step. Failure → disconnect. Unpaired machines never get past this.

### 3.4 Robot discovery, failover, standby

- The robot advertises mDNS `_milo-robot._tcp.local.` with TXT records `id`, `name`, `busy` (0/1), `pairing` (0/1) — **always on** from boot, independent of pairing mode, so an already-paired brain can reconnect automatically without anyone touching the dashboard again.
- Each brain's `net/discovery.py` browses continuously; `net/connector.py` filters to a *paired, not-busy* robot, ranks by priority, connects, and retries on drop (~10 s). The robot only accepts one connected brain at a time; a second one is refused until the first disconnects — so two paired brains failing over to the same robot is safe (whichever reconnects first wins, the other keeps retrying).
- The robot's idle/standby state is driven by `ControlBroker.on_change` (`main.py`'s `_make_control_change_handler`), not brain state alone: whenever **no brain is connected and no web dashboard client holds control**, the robot stands at standby with self-leveling engaged — not asleep or limp. A brain connecting, or a web client taking control, is what actually moves it (stand pose, excited face, streams resume); either one is enough, and losing both returns it to standing by.

### 3.5 Cognition loop (runs on the brain)

1. Mic stream → **VAD** gates a speech segment; **GCC-PHAT** on L/R channels gives a coarse bearing → `{"t":"cmd","move":{"turn":bearing}}` so Milo faces the speaker.
2. Video stream → **InsightFace** embedding → `match_face` against **Milo's** graph → identity or `unknown`.
3. **ASR** transcribes the segment.
4. Brain pulls graph context (`neighbors` of the speaker node, `recent_events`).
5. **LLM** (Ollama, structured JSON output) returns `{reply, face, move, facts[]}`.
6. Brain streams **Piper TTS** audio + face/move commands to Milo (talk-face animates while audio plays) and writes `facts[]` back to the graph.
7. Unknown person → Milo asks their name → next transcript names a new person node + stores the session's face embeddings.

### 3.6 Gait engine (runs on the Pi)

One interface, two backends — callers never know which is active:

```python
gait.engine.set_velocity_command(vx: float, vy: float, yaw_rate: float)
```

- **`policy.py` (primary):** `policy.onnx` (MLP 2×64, <50k params) via onnxruntime at 50 Hz. Observation (~30 dims): 8 joint positions, 8 previous actions, roll/pitch, 3-axis angular velocity, gravity vector, command. Action: 8 target-angle deltas from the stand pose, clamped ±25°. Inference budget <5 ms/step (expect <1 ms).
- **`cpg.py` (fallback):** parameterized diagonal-trot — per-leg sine hip/knee oscillators with tunable amplitude/frequency/phase, hand-tuned. Exists *before* RL training ships so Milo can always walk.
- `engine.py` owns the 50 Hz loop, reads the IMU each tick, and routes output angles through the same servo driver as scripted poses.

### 3.7 Knowledge graph (lives ONLY on the Pi — `~/.milo/graph.db`)

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
  node_id INTEGER NOT NULL REFERENCES nodes(id),   -- person node
  embedding BLOB NOT NULL,       -- 512 × float32 (InsightFace)
  created_at TEXT NOT NULL
);
```

`match_face(embedding, threshold=0.45)` does brute-force cosine over `face_embeddings` — fine for hundreds of people on a Pi. Brains do all *extraction*; the Pi only stores, indexes, serves. Nightly `sqlite3 .backup` to a timestamped file. **This is the portability guarantee:** teach Milo a fact via the laptop, power-cycle, connect via the desktop — Milo still knows it.

---

## 4. Hardware: Components & BOM

### 4.1 Component inventory

**Carried over from the Sesame build (unchanged):** 3D-printed body + legs, 8× MG90 metal-gear servos (2 per leg), SSD1306 0.96" 128×64 I2C OLED face, KCD1 rocker switch, wiring/heat-shrink sundries.

**New components (the Milo upgrade):**

| Component | Role | Interface | Addr / pins |
|---|---|---|---|
| Raspberry Pi Zero 2W | replaces the ESP32 as the robot's computer | — | — |
| PCA9685 16-ch PWM board | drives all 8 servos (ESP32 drove them from its own PWM pins) | I2C | `0x40` |
| MPU6050 IMU | body orientation + angular velocity for the gait policy | I2C | `0x68` |
| Pi Camera V2.1 (IMX219, 8MP) | vision — faces, scenes | CSI (15→22-pin Zero ribbon!) | — |
| 2× INMP441 MEMS microphones | stereo hearing + sound direction | I2S (shared data-in line) | L/R pin selects channel |
| MAX98357A 3W amp + speaker | Milo's voice (TTS playback) | I2S | — |
| 2× 18650 cells + 2S BMS | main battery | — | — |
| Buck converter #1 (5V/2A) | logic rail: Pi + OLED + IMU + camera + mics | — | set to **5.1 V** |
| Buck converter #2 (5V/5A) | servo rail: PCA9685 V+ only | — | set to **5.1 V** |
| Charger module | charges pack; wired battery-side, behind the switch | — | — |
| microSD (≥16 GB) | Raspberry Pi OS Lite 64-bit (Bookworm) | — | — |

Existing OLED keeps its address (`0x3C`) and joins the same I2C bus as the two new I2C devices.

### 4.2 BOM with prices (LKR, actual purchase list)

| Item | Qty | Unit | Total |
|---|---|---|---|
| MG90 metal-gear servo (RB0057) | 8 | 580 | 4,640 |
| 0.96" 128×64 OLED I2C (DM0037) | 1 | 680 | 680 |
| KCD1 rocker switch | 1 | 40 | 40 |
| Wire kit | 1 | 300 | 300 |
| Heat-shrink assortment | 1 | 200 | 200 |
| Raspberry Pi Zero 2 W | 1 | 23,400 | 23,400 |
| Pi Camera V2.1 (IMX219) | 1 | 9,500 | 9,500 |
| 3D-printed body | 1 | 5,900 | 5,900 |
| INMP441 I2S microphone | 2 | 450 | 900 |
| PCA9685 servo driver (MD0223) | 1 | 1,000 | 1,000 |
| MAX98357 I2S 3W amp (MD0860) | 1 | 500 | 500 |
| Speaker | 1 | 200 | 200 |
| 2× 18650 + BMS + bucks + charger | 1 | 4,000 | 4,000 |
| microSD | 1 | 2,500 | 2,500 |
| **MPU6050 IMU** (required addition) | 1 | ~450 | ~450 |
| **CSI ribbon 15→22-pin** (verify camera box first) | 1 | ~400 | ~400 |
| **Total** | | | **≈ 54,600** |

---

## 5. Wiring Diagram

### 5.1 Power tree — build and verify this FIRST

```
 2× 18650 (2S, 7.4V nom) ── 2S BMS ──┬── charger module (charge path, always on battery)
                                     │
                               KCD1 switch
                                     │
                    ┌────────────────┴─────────────────┐
                    │                                  │
             Buck 1: 5V @ 2A                    Buck 2: 5V @ 5A
             (LOGIC RAIL)                       (SERVO RAIL)
                    │                                  │
        ┌───────────┼──────────┐                PCA9685 V+ terminal ONLY
        │           │          │                       │
   Pi 5V (pins  OLED VCC   MPU6050 VCC          8× MG90 servo power
   2/4) + camera INMP441×2  MAX98357A VIN
        │
   Pi 3V3 → INMP441 VDD (mics are 3.3V parts — check your breakout)

   ⏚ COMMON GROUND: battery −, both buck outputs, Pi GND, every breakout GND.
```

> **Rules that keep the magic smoke in:**
> 1. Set BOTH bucks to **5.1 V with a multimeter BEFORE connecting anything** — an unadjusted buck can output 12 V and kill the Pi instantly.
> 2. Servos are powered **only** from Buck 2 via PCA9685 V+. Never from the Pi's 5 V pin.
> 3. PCA9685 **VCC** (logic) comes from the Pi's 3.3 V; **V+** (servo power) from Buck 2. They are different pins.
> 4. Load-test Buck 2 with 2–3 servos sweeping; no sag below 4.8 V.
> 5. Common ground everywhere, heat-shrink every splice.

### 5.2 Pi Zero 2W pin map

```
                       Raspberry Pi Zero 2W (40-pin header, top view)
                 3V3  [ 1] [ 2]  5V   ◄── Buck 1 (logic rail)
   I2C SDA ──►  GPIO2 [ 3] [ 4]  5V
   I2C SCL ──►  GPIO3 [ 5] [ 6]  GND  ◄── common ground
                      [ 7] [ 8]
                 GND  [ 9] [10]
                      [11] [12]  GPIO18 ──► I2S BCLK
                      [13] [14]  GND
                      [15] [16]
                 3V3 [17] [18]        ◄── 3V3 → mic VDD, PCA9685 VCC, Mic B L/R pin
                      [19] [20]  GND
                      [21] [22]
                      [23] [24]
                 GND [25] [26]
                      [27] [28]
                      [29] [30]  GND
                      [31] [32]
                      [33] [34]  GND
   I2S LRCLK ◄─ GPIO19[35] [36]
                      [37] [38]  GPIO20 ──► I2S DATA IN (from mics)
                 GND [39] [40]  GPIO21 ──► I2S DATA OUT (to amp)

   CSI connector (board edge): IMX219 camera via 15→22-pin Zero ribbon
```

| GPIO | Pin | Function | Connects to |
|---|---|---|---|
| GPIO 2 | 3 | I2C1 SDA | PCA9685 SDA + SSD1306 SDA + MPU6050 SDA |
| GPIO 3 | 5 | I2C1 SCL | PCA9685 SCL + SSD1306 SCL + MPU6050 SCL |
| GPIO 18 | 12 | I2S BCLK | INMP441 ×2 SCK **and** MAX98357A BCLK |
| GPIO 19 | 35 | I2S LRCLK | INMP441 ×2 WS **and** MAX98357A LRC |
| GPIO 20 | 38 | I2S DATA IN | INMP441 ×2 SD (one shared line) |
| GPIO 21 | 40 | I2S DATA OUT | MAX98357A DIN |
| 5V | 2/4 | Power in | Buck 1 output |
| 3V3 | 1/17 | Logic ref | mic VDD, PCA9685 VCC, Mic B channel-select |
| GND | 6,9,14,… | Ground | common ground |
| CSI | — | Camera | IMX219, 15→22-pin ribbon |

### 5.3 Bus detail — I2C (3 devices, one bus)

```
GPIO2 (SDA) ──┬── PCA9685 @ 0x40   (servo driver; VCC=3V3, V+=Buck 2)
              ├── SSD1306 @ 0x3C   (OLED face)
              └── MPU6050 @ 0x68   (IMU — mount RIGID near body center;
GPIO3 (SCL) ──┴── (same three)      screws/standoffs, never foam)
```

Bring-up check: `i2cdetect -y 1` must show `0x3c`, `0x40`, `0x68`.

### 5.4 Bus detail — I2S (2 mics in + 1 amp out, shared clocks)

```
GPIO18 BCLK  ──┬── Mic A SCK ──┬── Mic B SCK ──┬── MAX98357A BCLK
GPIO19 LRCLK ──┼── Mic A WS  ──┼── Mic B WS  ──┼── MAX98357A LRC
GPIO20 ◄───────┴── Mic A SD  ──┴── Mic B SD      (shared data-in)
GPIO21 ──────────────────────────────────────► MAX98357A DIN

Mic A: L/R pin → GND  (LEFT channel)  · mounted LEFT side of head
Mic B: L/R pin → 3V3  (RIGHT channel) · mounted RIGHT side of head
Target 10–15 cm mic separation — this baseline gives GCC-PHAT its bearing signal.
```

Device-tree config (`/boot/firmware/config.txt`): `dtparam=i2s=on` + `dtoverlay=googlevoicehat-soundcard` (simultaneous capture/playback). Fallback if it misbehaves: separate `i2s-mems-mic`-style capture + `max98357a` overlays.

### 5.5 Servo channel map (PCA9685) — front legs match Sesame firmware naming, rear legs rewired

| Channel | Servo | Position |
|---|---|---|
| 0 | R1 | front-right hip |
| 1 | R2 | front-right knee |
| 2 | L1 | front-left hip |
| 3 | L2 | front-left knee |
| 8 | R4 | rear-right knee |
| 9 | R3 | rear-right hip |
| 10 | L3 | rear-left hip |
| 11 | L4 | rear-left knee |

```
        FRONT (camera + OLED head)
   L1 ──hip──┐         ┌──hip── R1     ch2/ch0
   L2 ──knee─┤  BODY   ├─knee── R2     ch3/ch1
             │ Pi+IMU  │
   L3 ──hip──┤ battery ├──hip── R3     ch10/ch9
   L4 ──knee─┘         └─knee── R4     ch11/ch8
        BACK
```

PWM: 50 Hz, 500–2500 µs pulse range, per-servo trim offsets, **20 ms staggered activation** between simultaneous multi-servo writes (brownout lesson inherited from the ESP32 firmware), and a **safe-angle clamp of 5°–175°** applied to every write — a servo driven to its mechanical hard-stop stalls at full current, sagging the shared rail and twitching the others, so a commanded 0°/180° drives the safe near-extreme instead of grinding into the wall.

---

## 6. Tech Stack

### 6.1 Robot — `milo-bridge` on the Pi Zero 2W

| Concern | Choice | Why |
|---|---|---|
| OS | Raspberry Pi OS Lite 64-bit (Bookworm) | headless, current, 64-bit for onnxruntime |
| Language/runtime | Python 3.11+, asyncio | one language across the whole project |
| Servos | `adafruit-circuitpython-pca9685` | mature PCA9685 driver |
| OLED | `luma.oled` | clean SSD1306 API, PIL-image based |
| IMU | `smbus2` register driver (MPU6050) | tiny, no heavyweight dependency |
| Camera | `picamera2` | official libcamera stack, MJPEG encode |
| Audio | `arecord`/`aplay` (alsa-utils) | stereo I2S capture + playback; avoids PortAudio's ALSA enumeration, which is empty on this hardware without a configured ALSA default |
| Gait inference | `onnxruntime` | <1 ms/step for a 2×64 MLP on the Zero 2W |
| Graph | stdlib `sqlite3` | zero-dependency, fits 512 MB RAM |
| Networking | `websockets`, `zeroconf` | one multiplexed WS + mDNS discovery |
| Process | systemd `milo-bridge.service` | Restart=always, After=network-online.target |

### 6.2 Desktop — `milo-brain` app (Windows/Linux, any LAN GPU machine)

| Concern | Choice | Tier small (RTX 4050 6GB) | Tier large (RTX 5090 32GB) |
|---|---|---|---|
| LLM | Ollama | `llama3.2:3b` (Q4 ≈ 2.5 GB) | 8B-class |
| ASR | `faster-whisper` | `small` (~1 GB) | `medium` |
| VAD | Silero VAD | same | same |
| Faces | InsightFace `buffalo_l` | GPU, or CPU if VRAM-tight (~0.5 GB) | GPU |
| TTS | Piper (en, medium voice) | CPU | CPU |
| Sound direction | GCC-PHAT (numpy/scipy) | CPU | CPU |
| UI | Textual TUI (dashboard, Connect Robots, model picker) | same | same |
| Client | `websockets` + `zeroconf` discovery | same | same |

Tier is set in `~/.milo-brain/config.yaml` (auto-detected from GPU at first run, user-overridable in the TUI's model picker). VRAM budget on the 4050: whisper-small + InsightFace + 3B-Q4 LLM ≈ 4 GB — fits; InsightFace drops to CPU if tight.

### 6.3 Training — GPU box only

| Concern | Choice |
|---|---|
| Physics | MuJoCo (MJCF `milo.xml`, geometry from the community Sesame Simulator, masses/lag measured on the real robot) |
| RL | Stable-Baselines3 PPO, policy MLP 2×64, ~10k steps/s vectorized |
| Env API | Gymnasium |
| Export | PyTorch → ONNX (`export_onnx.py`) |
| Domain randomization | friction 0.6–1.4×, servo strength 0.8–1.2×, latency 10–50 ms, mass ±10%, IMU noise, random pushes — **mandatory** for MG90 sim-to-real |

### 6.4 Testing strategy (everything runs off-hardware)

- Hardware drivers take an injected bus/device object; tests pass mocks (angle→duty math, filter math, stagger timing are all pure functions).
- `milo-common` auth/protocol: golden-value tests both packages share (wrong token refused, replayed challenge refused).
- Graph store: real SQLite in tmpdir, including `match_face` similarity.
- Gait: CPG output properties (phase opposition, amplitude bounds); policy runner against a tiny generated ONNX model.
- Brain pipelines: model classes behind interfaces; GCC-PHAT tested with synthetic delayed signals; agent loop tested with a fake Ollama client.
- Hardware-in-the-loop steps (i2cdetect, arecord, camera, servo sweep, endurance) remain manual checklists in `project-milo-plan.md`.

---

## 7. Build Phases (summary — full detail in `project-milo-plan.md`)

| Phase | Scope | Exit criterion |
|---|---|---|
| 0 | Parts, SD flash, repo scaffold | Pi boots headless on WiFi |
| A | Power rails, wiring, bring-up | `i2cdetect` ×3, stereo record/playback, camera, 8-servo sweep |
| B | Bridge core: drivers + poses + faces | stands/rests/waves/faces via systemd on battery |
| C | Protocol, pairing, streaming, brain skeleton | live A/V on brain; PIN pair <2 min; failover + sleep/wake |
| D | MuJoCo + PPO + ONNX deploy (+CPG first) | walks + turns on the real floor on velocity commands |
| E | VAD, direction, ASR, vision | turns to voice; recognizes a known face; live transcripts |
| F | Graph + LLM loop + TTS | greets returning person by name; recalls facts across brains + power cycles |
| G | Busy-handling, endurance, docs | all 8 success criteria pass; >3 h battery |

**Software-first note:** all Phase B–F *code* in this repo is written and unit-tested off-hardware (mocked buses/models) before the physical build completes; hardware phases then become integration checklists.

---

## 8. Top Risks

1. **Sim-to-real gap** (MG90s are slow/sloppy) → measure real servo step response into the sim, aggressive domain randomization, CPG fallback exists before RL ships.
2. **Pi Zero 2W CPU saturation** → streaming must leave ≥40% headroom before Phase D; drop to 10 fps first.
3. **6 GB VRAM contention** (4050) → tier config keeps models small; InsightFace to CPU.
4. **2.4 GHz WiFi jitter** → 20 ms audio frames + jitter buffer; UDP escape hatch if needed.
5. **Servo brownout** → dedicated 5 A rail, staggered activation, never power servos from the Pi.
