# MILO — a Sesame robot that walks with a neural net, remembers you, and borrows brains

![Platform](https://img.shields.io/badge/Robot-Raspberry%20Pi%20Zero%202W-c51a4a?logo=raspberrypi&logoColor=white)
![Language](https://img.shields.io/badge/Code-Python%203.11%2B-blue?logo=python&logoColor=white)
![Gait](https://img.shields.io/badge/Gait-RL%20(PPO%20%E2%86%92%20ONNX)-8A2BE2)
![License](https://img.shields.io/badge/License-Apache%202.0-yellow)

Milo is a free-roaming quadruped robot built on the body of the open-source
**[Sesame Robot](https://github.com/dorianborian/sesame-robot)**. It keeps Sesame's
3D-printed frame, 8× MG90 servo layout, and expressive OLED face — and replaces
everything else: the ESP32 gives way to a Raspberry Pi Zero 2W with a camera, stereo
microphones, a speaker, and an IMU. Milo walks with a reinforcement-learning-trained
neural network, stores everyone it meets in an on-robot knowledge graph, and gets its
intelligence (LLM, speech, vision) from any GPU machine on your LAN running the
**Milo Brain** desktop app.

> **Body + Memory on the robot, Compute on the brains.** Milo's identity — who it
> knows, what it has seen — lives only on the Pi. Brains are stateless, paired,
> interchangeable. No brain around? Milo goes to sleep.

---

## Credits — standing on Sesame's shoulders

Milo is a derivative of the **Sesame Robot Project** created by
**[Dorian Todd](https://www.doriantodd.com/)** —
[github.com/dorianborian/sesame-robot](https://github.com/dorianborian/sesame-robot)
(Apache 2.0). Sesame provides the entire mechanical platform this project is built on:

- The 3D-printed quadruped body, leg geometry, and CAD/STL files
- The 8-servo (2-per-leg) actuation layout and servo naming (R1–R4 / L1–L4)
- The OLED face system and its 37-face bitmap art library
- The original scripted poses and trot gaits (ported to Python in `bridge/milo_bridge/poses.py`)
- The build and wiring knowledge base (including hard-won lessons like staggered servo
  activation to prevent brownouts)

Reference copies of the firmware files Milo ports from live in
[`hardware/reference-sesame/`](hardware/reference-sesame/). If you want a friendly,
remote-controlled robot without the AI stack, go build a Sesame — it's excellent.

## What Milo upgrades over Sesame

| Area | Sesame (original) | Milo (this project) |
|---|---|---|
| Controller | ESP32, Arduino C++ | **Raspberry Pi Zero 2W**, Python 3.11 (asyncio) |
| Servo drive | ESP32 PWM pins | **PCA9685** 16-ch I2C driver on a dedicated 5A rail |
| Locomotion | Scripted keyframe gaits | **PPO-trained neural gait policy** (MuJoCo sim → ONNX @ 50 Hz on the Pi) + CPG trot fallback |
| Balance sensing | None | **MPU6050 IMU** (100 Hz complementary filter feeding the policy) |
| Vision | None | **IMX219 camera** → face recognition (InsightFace) on the brain |
| Hearing | None | **2× INMP441 I2S mics** → VAD, speech-to-text, and sound-direction (GCC-PHAT) |
| Voice | None | **MAX98357A I2S amp + speaker**, Piper TTS, conversational LLM (Ollama) |
| Memory | None | **On-robot SQLite knowledge graph** — people, face embeddings, events, facts; survives power cycles and brain swaps |
| Intelligence | HTTP commands from your phone | **Milo Brain** desktop app — mDNS-discovered, PIN-paired, hot-failover between machines |
| Security | Open HTTP server | **HKDF pairing tokens + HMAC challenge–response** on every session |
| Power | USB-C / small Li-ion | 2S 18650 pack, BMS, dual 5V bucks (2A logic / 5A servo), >3 h target runtime |
| Face display | SSD1306, C++ engine | Same display and same face art, rendered by `luma.oled` with the same idle-blink personality |

## How it works

```
        ┌── MILO (Pi Zero 2W) ──────────────┐        ┌── any GPU machine on the LAN ──┐
camera ─►                                    │  WiFi  │                                │
mics ───►  milo-bridge: drivers · gait NN   ◄─────────►  Milo Brain app: Whisper ASR   │
servos ◄─  · knowledge graph · sleep mode   │   WS    │  · InsightFace · Ollama LLM    │
OLED ◄──   · mDNS discovery · PIN pairing   │        │  · Piper TTS · PyQt6 tray      │
speaker ◄─                                   │        │                                │
        └────────────────────────────────────┘        └────────────────────────────────┘
```

Milo streams camera + microphone audio to the highest-priority paired brain. The brain
listens (VAD → ASR), looks (face embeddings matched against **Milo's** graph), thinks
(LLM with graph context), and answers back: TTS audio for the speaker, an expression
for the face, a movement intent for the gait engine, and new facts written into Milo's
memory. Kill that brain and Milo fails over to another paired machine within seconds —
with all of its memories intact, because they never left the robot.

Full detail — every component, wiring diagram, pin map, protocol, and tech-stack
decision — lives in **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**.

## Repository map

| Path | What it is |
|---|---|
| [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md) | **Building the robot? Start here** — phase-by-phase from zero (parts, 3D prints, assembly, wiring, software, training) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System design — wiring diagrams, components, protocols, tech stack |
| [`project-milo-plan.md`](project-milo-plan.md) | A–Z phased build plan (Phase 0 → G) with checklists |
| [`common/`](common/) | `milo-common` — WebSocket protocol + pairing/auth shared by both sides |
| [`bridge/`](bridge/) | `milo-bridge` — the Pi service: drivers, gait engine, knowledge graph, discovery |
| [`brain/`](brain/) | `milo-brain` — the desktop app: ASR/vision/TTS pipelines, LLM agent, tray UI |
| [`training/`](training/) | MuJoCo model + PPO training + ONNX export for the gait policy |
| [`hardware/reference-sesame/`](hardware/reference-sesame/) | Original Sesame firmware files Milo ports from (credit: Dorian Todd) |
| [`docs/specs/`](docs/specs/) | The approved Project Milo design spec |

## Quickstart

### On your desktop (Milo Brain)

```bash
# prerequisites: Python 3.11+, and Ollama installed & running (https://ollama.com)
cd common && pip install -e . && cd ../brain && pip install -e .[full]
python -m milo_brain            # tray icon appears; advertises _milo-brain._tcp on the LAN
```

### On the robot (Raspberry Pi Zero 2W)

```bash
# Raspberry Pi OS Lite 64-bit (Bookworm), I2C + I2S enabled — see docs/ARCHITECTURE.md §5
cd common && pip install -e . && cd ../bridge && pip install -e .[pi]
sudo cp bridge/systemd/milo-bridge.service /etc/systemd/system/
sudo systemctl enable --now milo-bridge
```

First contact: the brain's tray UI shows the discovered robot → Milo displays a 6-digit
PIN on its face → type it into the brain → paired for good.

### Developing without hardware

Everything is testable off-robot — hardware buses and GPU models are injected and mocked:

```bash
pip install -e common -e bridge -e brain
pytest common bridge brain training
```

## Project status

- ✅ Software: bridge, brain, protocol/auth, gait engine (CPG + ONNX runner), knowledge
  graph, training pipeline — implemented and unit-tested off-hardware (134 tests)
- 🔩 Hardware: not yet built — the full from-zero build walkthrough is
  [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md)
- 🎯 The bar (spec success criterion #5): teach Milo a fact through the laptop brain,
  power-cycle the robot, reconnect through the desktop brain — Milo must greet you by
  name and still know the fact.

## License

Apache 2.0, same as the original Sesame Robot Project. This repository contains
files derived from and copied from
[dorianborian/sesame-robot](https://github.com/dorianborian/sesame-robot)
© Dorian Todd, used under Apache 2.0; see [`LICENSE`](LICENSE) and per-directory
notices in [`hardware/reference-sesame/`](hardware/reference-sesame/).
