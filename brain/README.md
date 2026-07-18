# Milo Brain

The desktop half of Project Milo. `milo-brain` is the compute side that gives
the robot its intelligence: speech-to-text, face recognition, an LLM
conversational agent with real tool-calling control over the robot's
movement/face/speech/IMU, and text-to-speech — all running on your GPU
machine (laptop or desktop), not on the Pi.

Milo (the robot, running `milo-bridge`) advertises itself over mDNS and
accepts one connected brain at a time; `milo-brain` discovers robots on the
LAN and dials in. Pairing (once per robot/brain pair, triggered from the
robot's web dashboard) uses a 6-digit PIN shown on the robot's face; after
that, the brain streams camera + microphone audio from whichever robot it's
connected to. The brain listens, looks, thinks, and replies — TTS audio and
movement/face tool calls go back over the same connection. Kill the brain
and Milo waits, standing by, for another paired brain to reconnect (or the
same one to come back). **The robot's identity and memory (who it knows,
what happened) never leave the Pi** — brains are stateless, interchangeable
compute.

This README covers the `brain/` package specifically: installing it on
native Linux, installing it on native Windows, configuring it, running it,
and how its pieces fit together. For the full project (robot side, wiring,
architecture), see the [top-level README](../README.md) and
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

> Setting up several brain machines as part of a full from-zero robot build?
> [`docs/SOFTWARE-SETUP.md` Part 4](../docs/SOFTWARE-SETUP.md#part-4-set-up-the-brain-machines-windows-pc-laptop)
> has the same Windows steps in condensed form alongside the rest of the
> build. This document is the detailed reference for this package alone.

---

## Contents

- [What this package actually does](#what-this-package-actually-does)
- [Requirements](#requirements)
- [Install — native Linux](#install--native-linux)
- [Install — native Windows](#install--native-windows)
- [Configuration](#configuration)
- [Running it](#running-it)
- [Pairing with the robot](#pairing-with-the-robot)
- [How it works internally](#how-it-works-internally)
- [Development / running the tests](#development--running-the-tests)
- [Troubleshooting](#troubleshooting)

---

## What this package actually does

`milo-brain` is a WebSocket client that:

1. **Discovers robots** on the LAN via mDNS (`_milo-robot._tcp.local.`),
   picking a paired-and-reachable one automatically, or a specific one
   chosen from the TUI's **Connect Robots** tab.
2. **Authenticates/pairs** with the robot it connects to (PIN-based first
   contact — triggered by the robot's "Enter Pairing Mode" — then a stored
   HKDF trust token for every session after).
3. **Runs the cognition pipeline** per connected robot:
   - `pipelines/vad.py` — voice activity detection, segments the incoming
     mic stream into utterances
   - `pipelines/asr.py` — Whisper (`faster-whisper`) speech-to-text
   - `pipelines/vision.py` — InsightFace face detection + embedding, matched
     against the robot's own knowledge graph over the wire
   - `pipelines/direction.py` — GCC-PHAT sound-direction estimate from the
     stereo mic pair
   - `llm/agent.py` — the `CognitionAgent`: builds context from the robot's
     graph, talks to Ollama, runs a bounded **tool-calling loop** so the LLM
     can actually move the robot, change its face, or speak on demand
   - `pipelines/tts.py` — Piper text-to-speech, chunked back to the robot
     over the wire
4. **Calls the robot's own MCP server** (`mcp_client.py`) to execute
   movement/face/speech/IMU tools — the robot's bridge exposes these, the
   brain is just a client.

Audio and video never touch a local mic/speaker/camera on the brain
machine — they arrive from and are sent back to the robot entirely over the
WebSocket connection. That means a plain, ordinary Windows or Linux machine
works fine with no extra audio driver setup — the brain never opens your
laptop's mic or speakers.

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** installed and running locally (or
  reachable at whatever `ollama_url` you configure) — this is what actually
  runs the LLM. Needs a model that supports native tool-calling (Ollama's
  `llama3.2:3b` and `llama3.1:8b` both do; these are also this project's
  default small/large tier models).
- Same LAN/WiFi as the robot (or a route to it — mDNS discovery needs
  multicast to reach the robot's subnet).
- **A GPU is strongly recommended** but not required for the pairing-only
  light install. The full AI stack (Whisper + InsightFace + Piper + Ollama)
  runs on CPU, just slowly.

Two install tiers, matching `brain/pyproject.toml`:

| Install | Command | Gets you |
|---|---|---|
| **Light** | `pip install -e ./brain` | mDNS discovery, pairing, WebSocket client, MCP tool-calling client. Enough to pair and see the robot connect — no cognition yet. |
| **Full** | `pip install -e "./brain[full]"` | + `faster-whisper`, `insightface`, `onnxruntime-gpu`, `piper-tts`, `torch` (Silero VAD), `opencv-python` |

## Install — native Linux

Tested on Debian/Ubuntu-family distros; adjust package-manager commands for
others.

```bash
# 1. System prerequisites
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

# 2. Install Ollama and start it (systemd service is set up automatically)
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama

# 3. Get the code
git clone https://github.com/<your-username>/MILO-Robot.git
cd MILO-Robot

# 4. Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 5. Install — light first (fast, confirms pairing works end to end)
pip install -e ./common
pip install -e ./brain

# 6. Pull a tool-calling-capable model for your tier
ollama pull llama3.2:3b        # small tier: <16 GB VRAM (or CPU)
ollama pull llama3.1:8b        # large tier: >=16 GB VRAM

# 7. Full AI stack, once you're ready for real cognition (not just pairing)
pip install -e "./brain[full]"
```

**NVIDIA GPU on native Linux:** install the proprietary driver
(`nvidia-driver-XXX` from your distro's repos, or NVIDIA's `.run` installer),
reboot, then confirm with `nvidia-smi` before installing the `full` extra —
`torch`/`onnxruntime-gpu` will otherwise silently fall back to CPU.

## Install — native Windows

No WSL, no Linux subsystem — everything below runs directly in PowerShell
against a normal Windows install of Python. This is the fully supported path
for a Windows brain machine.

### Prerequisites

- **Windows 10 21H2+ or Windows 11.**
- **Python 3.11+** from [python.org](https://www.python.org/downloads/windows/)
  — during install, tick **"Add python.exe to PATH"**. Verify afterward:
  `python --version` in PowerShell.
- **Git for Windows** — https://git-scm.com/download/win
- **[Ollama](https://ollama.com)** — the Windows installer sets it up and
  starts it automatically in the background; no separate service step.
- **NVIDIA GPU (optional but recommended):** install/update the regular
  **[Game Ready or Studio driver](https://www.nvidia.com/drivers)**. Once
  installed, `nvidia-smi` in PowerShell should print your card. CPU works
  too for the full stack, just slower.

### 1. Get the code

```powershell
git clone https://github.com/<your-username>/MILO-Robot.git
cd MILO-Robot
```

### 2. Virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> If PowerShell refuses to run the activation script with a message like
> *"running scripts is disabled on this system"*, allow it once for your
> user account and try again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### 3. Install — light first (fast, confirms pairing works end to end)

```powershell
pip install -e .\common
pip install -e .\brain
```

### 4. Pull a tool-calling-capable model for your tier

```powershell
ollama pull llama3.2:3b        # small tier: <16 GB VRAM (or CPU)
ollama pull llama3.1:8b        # large tier: >=16 GB VRAM
```

### 5. Full AI stack, once you're ready for real cognition (not just pairing)

```powershell
pip install -e ".\brain[full]"
```

First run downloads the Whisper / InsightFace / Silero model weights.

**GPU notes for Windows:**
- `torch` installed via plain `pip install` already bundles its own CUDA
  runtime — no separate CUDA Toolkit install needed for it. Verify with:
  ```powershell
  python -c "import torch; print(torch.cuda.is_available())"
  ```
  This should print `True` on a machine with a working NVIDIA driver.
- `onnxruntime-gpu` is different: it links against a **system-wide CUDA
  Toolkit + cuDNN** install rather than bundling one. If it silently falls
  back to CPU, either install the matching CUDA/cuDNN versions from the
  [ONNX Runtime CUDA requirements table](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#requirements),
  or just leave it on CPU — InsightFace's face-matching model is small
  enough to run acceptably on CPU even on a modest laptop.

### 6. Windows Firewall

The first time you run `python -m milo_brain`, **Windows Defender Firewall**
may prompt to allow `python.exe` on **Private networks** — allow it. The
brain no longer listens for inbound connections (it's the one dialing out
to the robot now), but mDNS discovery still needs multicast (UDP `5353`)
to actually see robots on the network. If discovery isn't finding anything
and no prompt appeared, add the rule manually: **Windows Defender Firewall →
Advanced settings → Inbound Rules → New Rule → Program**, pointing it at
your venv's `.venv\Scripts\python.exe`.

From here, configuration, running, and pairing are identical to the Linux
steps below — the only difference is the config path, which resolves under
`%USERPROFILE%` (i.e. `C:\Users\<you>\.milo-brain\config.yaml`) instead of
`~`.

## Configuration

Config lives at `~/.milo-brain/config.yaml` (`%USERPROFILE%\.milo-brain\config.yaml`
on Windows), created automatically on first run with sensible defaults (GPU
tier auto-detected via `nvidia-smi`). You generally don't need to touch it,
but every field:

| Field | Default | What it does |
|---|---|---|
| `brain_id` | random `brain-<hex>` | Stable identity across restarts/pairings — generated once, don't edit. |
| `name` | your hostname | Shown on the robot's web dashboard Brain card once connected/paired. |
| `tier` | auto (`small`/`large`) | `small` if VRAM < 16 GB, else `large`. Picks default model sizes. |
| `gpu` | auto | GPU name from `nvidia-smi`, informational. |
| `llm_model` | tier default | `llama3.2:3b` (small) / `llama3.1:8b` (large). Must support Ollama tool-calling. |
| `whisper_model` | tier default | `small` (small tier) / `medium` (large tier). |
| `ollama_url` | `http://127.0.0.1:11434` | Where Ollama is listening — change if it's on another machine/container. |
| `piper_voice` | `en_US-lessac-medium` | Piper TTS voice model name. |
| `face_match_threshold` | `0.45` | Cosine-similarity cutoff for "this is the same person" in face matching. |
| `vision_fps` | `3.0` | How often the video stream is analyzed for faces (independent of the robot's actual stream fps). |
| `busy_gpu_percent` | `85` | Reserved for a future "too busy to take a robot" signal — not yet wired up. |
| `reconnect_seconds` | `10.0` | How often the connector re-scans/retries when nothing is currently connectable. |
| `data_dir` | `~/.milo-brain` | Where `config.yaml` and `paired.json` (pairing trust store) live. |

Delete `~/.milo-brain/config.yaml` (or `%USERPROFILE%\.milo-brain\config.yaml`
on Windows) to reset to auto-detected defaults on the next run.

## Running it

```powershell
.venv\Scripts\Activate.ps1     # Windows, if not already active
# or: source .venv/bin/activate    (Linux)

python -m milo_brain           # TUI: dashboard, Connect Robots, model picker
python -m milo_brain --headless   # no TUI, just logs -- for headless/server boxes
```

The TUI runs in any terminal on Windows or Linux -- no GUI session required.
Keybindings: `c` opens **Connect Robots** (refreshable discovered-device
list), `m` opens the model picker (lists whatever's installed in Ollama),
`q` quits. Use `--headless` on a genuinely headless box (no terminal
attached at all, e.g. run under a service manager) -- it'll print a plain
`PIN:` prompt on stdin instead of a TUI modal when a robot it dials into
needs pairing.

It stays running, discovering robots on the LAN, and idle until it connects
to (or is told to connect to) one.

## Pairing with the robot

1. Make sure `milo-bridge` is running on the robot (see the
   [top-level README](../README.md) or
   [`docs/SOFTWARE-SETUP.md`](../docs/SOFTWARE-SETUP.md)).
2. On the robot's **web dashboard**, open the Brain card and click
   **Enter Pairing Mode**. Milo's face shows a **6-digit PIN**.
3. In the brain's TUI, press `c` for **Connect Robots**, then `r` to
   refresh -- Milo appears in the list (marked pairing-available).
4. Select it. A modal appears asking for the PIN (or the `--headless`
   prompt in the terminal) -- type the code from Milo's face.
5. Done — the trust token is stored in `~/.milo-brain/paired.json`, and
   the robot closes pairing mode automatically. You won't need the PIN
   again for this robot/brain pair; every future connection
   re-authenticates automatically via HMAC challenge-response, with the
   brain reconnecting on its own.

Once paired, the robot's `T_HELLO` handshake also advertises its own MCP
server address (`mcp_port`); the brain resolves this into a full `mcp_url`
from the connection's remote address and uses it to open a tool-calling
client against the robot — see below.

## How it works internally

```
                         ┌── milo-bridge (the robot) ─────────┐
   video frames ────────►│                                     │
   audio frames ────────►│  drivers · gait · knowledge graph   │
   T_TTS (speech out) ◄──│  MCP server :8766 (movement/face/   │
                         │  speech/IMU tools, bearer-auth)      │
                         └──────────────┬──────────────────────┘
                                        │ one WebSocket (video/audio/graph)
                                        │ + one HTTP MCP connection (tools)
                         ┌──────────────▼──────────────────────┐
                         │        milo-brain (this package)     │
                         │                                       │
   video ─► vision.py ─► FaceVision ──┐                          │
   audio ─► vad.py ────► VadSegmenter │                          │
             │                        ├─► CognitionAgent ──► Ollama (LLM)
             └► asr.py ──► WhisperAsr ┘        │                 │
                                                ▼                 │
                                    mcp_client.py (MiloMcpClient) │
                                    calls run_pose/walk/set_face/ │
                                    speak/get_imu_state/... on    │
                                    the robot's own MCP server    │
                                                                  │
                          reply text ──► tts.py (PiperTts) ──────┘
                          ──► T_TTS frames back to the robot
```

- **`net/discovery.py`** — `RobotDiscovery` browses `_milo-robot._tcp` mDNS,
  `select_robot()` ranks candidates (paired+not-busy first, else a
  pairing-mode one), with a manual-target override for the Connect Robots tab.
- **`net/connector.py`** — `RobotConnectorManager`: one discover→select→
  connect→session loop that drives both passive auto-reconnect and manual
  connects. Every connection goes through `brain_handshake` (`milo_common`),
  then gets handed to a session handler.
- **`session.py`** — `CognitionSessionFactory` builds the real pipeline
  stack once (ASR, vision, TTS, LLM client) and a `RobotCognitionSession`
  per connected robot. It also builds a `MiloMcpClient` for that robot from
  the resolved `peer.mcp_url` and the pairing token, and wires two reflexes
  through it directly: turning toward whoever's speaking (direction-of-
  arrival), and looping a "talking" face animation while TTS plays.
- **`llm/agent.py`** — `CognitionAgent.on_utterance()` is the actual
  cognition loop: builds context from the robot's knowledge graph, sends it
  to Ollama with the robot's MCP tool schemas attached, and runs a bounded
  (`MAX_TOOL_ROUNDS`) loop letting the model call tools (move, change face,
  speak unprompted, check IMU/status) before producing its final spoken
  reply. Also owns the unknown-person naming flow (asks for a name, writes a
  new graph node, waves and looks excited via direct MCP calls).
- **`mcp_client.py`** — `MiloMcpClient`, a thin wrapper over the official
  `mcp` Python SDK's Streamable HTTP client, scoped to one robot's MCP
  server for the life of one session.
- **`config.py`** — GPU tier detection (`nvidia-smi`) and
  `~/.milo-brain/config.yaml` load/save.
- **`tui/app.py`** — `MiloBrainApp`, the Textual TUI. Runs
  `RobotConnectorManager` as a background worker on its own event loop (no
  separate thread), so the reactive pairing-PIN flow is a direct `await` on
  a modal screen rather than cross-thread signaling.
- **`tui/dashboard.py`** — the main screen: identity, connection (connected
  robot + paired count), and model (with live tokens/sec) panels.
- **`tui/connect_robots.py`** — the refreshable discovered-robots list;
  selecting one requests a manual connect.
- **`tui/pairing.py`**, **`tui/model_picker.py`** — modal screens for PIN
  entry (popped reactively when a robot requests pairing mid-handshake) and
  picking an installed Ollama model.

Everything in `pipelines/` and the pairing/session flow is designed to be
testable off-hardware: real Whisper/InsightFace/Ollama/MCP clients are
injected, tests use fakes. See [`tests/`](tests/) for the fakes' shape.

## Development / running the tests

```powershell
pip install -e .\common
pip install -e ".\brain[dev]"
cd brain
pytest tests\ -v
```

```bash
# Linux
pip install -e ./common
pip install -e "./brain[dev]"
cd brain && pytest tests/ -v
```

No GPU, Ollama, or robot required — every pipeline, the agent's tool-calling
loop, the MCP client's message translation, and the session wiring are
covered with fakes standing in for the real Whisper/InsightFace/Ollama/MCP
objects.

## Troubleshooting

**`ollama pull` / connection refused talking to Ollama** — confirm it's
running: `curl http://127.0.0.1:11434/api/tags` should return JSON, not a
connection error. On Windows, check the Ollama tray icon is present (it
starts automatically on login); on native Linux, `sudo systemctl status
ollama` or run `ollama serve` in a terminal directly.

**Robot never shows up in Connect Robots** — both sides need multicast DNS
reachability on the same LAN segment, and the robot needs to actually be
advertising (check its web dashboard's Brain card, or that pairing mode is
on if it's a brand-new/unpaired robot). On Windows, the most common cause is
the **Windows Defender Firewall** prompt being dismissed or missed on first
run — see [Windows Firewall](#6-windows-firewall) above; also confirm the
network is set to **Private**, not **Public** (Public profile blocks
discovery traffic by default). On native Linux, check your firewall isn't
blocking UDP 5353 (mDNS).

**PowerShell says running scripts is disabled** — when activating the venv
(`.venv\Scripts\Activate.ps1`), run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.

**LLM never calls any tools / conversations feel "dumber" than expected** —
confirm your Ollama model actually supports tool-calling
(`llama3.2:3b`/`llama3.1:8b` do); a non-tool-calling model will just ignore
the tool schemas and reply in plain text.

**`onnxruntime-gpu` (InsightFace) stuck on CPU** — unlike `torch`,
`onnxruntime-gpu` needs a system-wide CUDA Toolkit + cuDNN install matched
to its version (see the GPU notes in the
[native Windows install](#install--native-windows) section above). It's
safe to leave this on CPU — it's a small model.

**Face recognition / Whisper very slow** — confirm `torch`/`onnxruntime-gpu`
are actually using the GPU (`tier`/`gpu` in `~/.milo-brain/config.yaml`
should show your card, not `cpu`). CPU fallback works but is much slower —
fine for pairing/testing, not for a snappy real-time conversation.
