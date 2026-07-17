# Milo Brain

The desktop half of Project Milo. `milo-brain` is the compute side that gives
the robot its intelligence: speech-to-text, face recognition, an LLM
conversational agent with real tool-calling control over the robot's
movement/face/speech/IMU, and text-to-speech — all running on your GPU
machine (laptop or desktop), not on the Pi.

Milo (the robot, running `milo-bridge`) discovers brains over mDNS, pairs
once with a 6-digit PIN, then streams camera + microphone audio to whichever
brain it's paired with. The brain listens, looks, thinks, and replies — TTS
audio and movement/face tool calls go back over the same connection. Kill the
brain and Milo fails over to another paired machine, or goes to sleep if none
are reachable. **The robot's identity and memory (who it knows, what
happened) never leave the Pi** — brains are stateless, interchangeable
compute.

This README covers the `brain/` package specifically: installing it on
native Linux, installing it on Windows via WSL2, configuring it, running it,
and how its pieces fit together. For the full project (robot side, wiring,
architecture), see the [top-level README](../README.md) and
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

> **Native Windows (no WSL)?** See
> [`docs/SOFTWARE-SETUP.md` Part 4](../docs/SOFTWARE-SETUP.md#part-4-set-up-the-brain-machines-windows-pc-laptop)
> instead — that path uses PowerShell directly and is fully supported. This
> document is for Linux and WSL2.

---

## Contents

- [What this package actually does](#what-this-package-actually-does)
- [Requirements](#requirements)
- [Install — native Linux](#install--native-linux)
- [Install — Windows via WSL2](#install--windows-via-wsl2)
- [Configuration](#configuration)
- [Running it](#running-it)
- [Pairing with the robot](#pairing-with-the-robot)
- [How it works internally](#how-it-works-internally)
- [Development / running the tests](#development--running-the-tests)
- [Troubleshooting](#troubleshooting)

---

## What this package actually does

`milo-brain` is a WebSocket server (default port `8765`) that:

1. **Advertises itself** on the LAN via mDNS (`_milo-brain._tcp.local.`) with
   its name, detected GPU, and tier so any robot on the network can find it.
2. **Authenticates/pairs** incoming robot connections (PIN-based first
   contact, then a stored HKDF trust token for every session after).
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
WebSocket connection. This means a WSL2 setup with no audio passthrough
works fine.

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
| **Light** | `pip install -e ./brain` | mDNS discovery, pairing, WebSocket server, MCP tool-calling client. Enough to pair and see the robot connect — no cognition yet. |
| **Full** | `pip install -e "./brain[full]"` | + `faster-whisper`, `insightface`, `onnxruntime-gpu`, `piper-tts`, `torch` (Silero VAD), `opencv-python`, `PyQt6` (tray UI) |

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

## Install — Windows via WSL2

WSL2 gives you a real Linux userspace with GPU passthrough to your NVIDIA
card, which is what lets `torch`/`onnxruntime-gpu` actually use the GPU
without dual-booting.

### 1. Enable WSL2 and install a distro

From an **elevated PowerShell** (Windows 11, or Windows 10 21H2+):

```powershell
wsl --install -d Ubuntu
```

Reboot if prompted, then finish the Ubuntu first-run setup (creates your
Linux username/password) from the Start Menu.

### 2. Install the NVIDIA driver — on Windows, not inside WSL

If you have an NVIDIA GPU: install/update the regular
**[NVIDIA Game Ready or Studio driver](https://www.nvidia.com/drivers)** on
the **Windows host**. Do **not** install a separate Linux NVIDIA driver
inside WSL — WSL2 passes the Windows driver through automatically. Verify
from inside WSL:

```bash
nvidia-smi
```

If this prints your GPU, passthrough is working. If it errors, update the
Windows-side driver and restart WSL (`wsl --shutdown` from PowerShell, then
reopen the Ubuntu terminal).

### 3. From here, it's the native Linux install

Open the Ubuntu (WSL) terminal and follow **[Install — native Linux](#install--native-linux)**
above exactly — steps 1 through 7 are identical inside WSL2. The only
WSL-specific notes:

- **Ollama**: the `curl -fsSL https://ollama.com/install.sh | sh` installer
  works fine inside WSL2 and will use the passed-through GPU automatically
  once `nvidia-smi` works. `sudo systemctl enable --now ollama` requires
  systemd support in WSL — if `systemctl` isn't available, run
  `ollama serve &` manually instead, or add
  ```
  [boot]
  systemd=true
  ```
  to `/etc/wsl.conf`, then `wsl --shutdown` from PowerShell and reopen the
  terminal.
- **Networking / mDNS discovery**: WSL2 uses a virtualized network adapter
  behind NAT by default, which can prevent mDNS (multicast) from reaching
  the robot on your physical LAN. If the brain's tray/log never shows the
  robot as discovered:
  - Easiest fix: set WSL to **mirrored networking mode** (Windows 11
    23H2+), which shares the host's network adapter directly. Add to
    `%UserProfile%\.wslconfig` on the Windows side:
    ```ini
    [wsl2]
    networkingMode=mirrored
    ```
    then `wsl --shutdown` and reopen.
  - If mirrored mode isn't available, run the **light** install (pairing
    only) on native Windows per
    [`docs/SOFTWARE-SETUP.md` Part 4](../docs/SOFTWARE-SETUP.md#part-4-set-up-the-brain-machines-windows-pc-laptop)
    instead, and keep WSL2 only for GPU-heavy workloads that don't need to
    reach the robot directly (e.g. gait policy training, per
    [`training/`](../training/)).
- **Filesystem location**: clone the repo into the WSL filesystem
  (`~/MILO-Robot`, i.e. `/home/<you>/MILO-Robot`), not into `/mnt/c/...`.
  Cross-filesystem access from WSL to Windows-side files is significantly
  slower and can trip up `pip install -e`'s editable-install symlinks.

## Configuration

Config lives at `~/.milo-brain/config.yaml`, created automatically on first
run with sensible defaults (GPU tier auto-detected via `nvidia-smi`). You
generally don't need to touch it, but every field:

| Field | Default | What it does |
|---|---|---|
| `brain_id` | random `brain-<hex>` | Stable identity across restarts/pairings — generated once, don't edit. |
| `name` | your hostname | Shown in the robot's pairing UI and mDNS TXT record. |
| `port` | `8765` | WebSocket listen port. |
| `tier` | auto (`small`/`large`) | `small` if VRAM < 16 GB, else `large`. Picks default model sizes. |
| `gpu` | auto | GPU name from `nvidia-smi`, informational. |
| `llm_model` | tier default | `llama3.2:3b` (small) / `llama3.1:8b` (large). Must support Ollama tool-calling. |
| `whisper_model` | tier default | `small` (small tier) / `medium` (large tier). |
| `ollama_url` | `http://127.0.0.1:11434` | Where Ollama is listening — change if it's on another machine/container. |
| `piper_voice` | `en_US-lessac-medium` | Piper TTS voice model name. |
| `face_match_threshold` | `0.45` | Cosine-similarity cutoff for "this is the same person" in face matching. |
| `vision_fps` | `3.0` | How often the video stream is analyzed for faces (independent of the robot's actual stream fps). |
| `busy_gpu_percent` | `85` | Above this, the brain advertises itself as busy over mDNS. |
| `data_dir` | `~/.milo-brain` | Where `config.yaml` and `paired.json` (pairing trust store) live. |

Delete `~/.milo-brain/config.yaml` to reset to auto-detected defaults on the
next run.

## Running it

```bash
source .venv/bin/activate      # if not already active

python -m milo_brain           # tray UI (needs PyQt6 — included in the [full] extra, or `pip install PyQt6` on its own)
python -m milo_brain --headless   # no tray, just logs — the only option without PyQt6, and what you want on a headless WSL/server box
python -m milo_brain --pairing    # start with pairing mode already enabled (skips the tray toggle)
```

On WSL2 without an X server, always use `--headless`.

On startup you'll see something like:

```
milo-brain 'my-laptop' (small tier) listening on :8765
```

That confirms it's up and advertising over mDNS. It stays running,
discoverable, and idle until a robot connects.

## Pairing with the robot

1. Make sure `milo-bridge` is running on the robot (see the
   [top-level README](../README.md) or
   [`docs/SOFTWARE-SETUP.md`](../docs/SOFTWARE-SETUP.md)).
2. Start the brain with `--pairing` (or enable pairing mode from the tray
   icon).
3. Milo's face shows a **6-digit PIN**.
4. Type it into the brain (tray dialog, or the `--headless` prompt in the
   terminal).
5. Done — the trust token is stored in `~/.milo-brain/paired.json`. You
   won't need the PIN again for this robot/brain pair; every future
   connection re-authenticates automatically via HMAC challenge-response.

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

- **`server.py`** — the WebSocket listener + mDNS `Advertiser`. Every
  connecting robot goes through `brain_handshake` (`milo_common`), then gets
  handed to a session handler.
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
- **`ui/tray.py`** — optional PyQt6 system tray (connection state, pairing
  toggle, PIN entry dialog). Falls back to headless automatically if PyQt6
  isn't installed.

Everything in `pipelines/` and the pairing/session flow is designed to be
testable off-hardware: real Whisper/InsightFace/Ollama/MCP clients are
injected, tests use fakes. See [`tests/`](tests/) for the fakes' shape.

## Development / running the tests

```bash
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
connection error. `sudo systemctl status ollama` (native Linux/WSL with
systemd) or run `ollama serve` in a terminal directly.

**Robot never appears / brain never appears on the other side** — both
sides need multicast DNS reachability on the same LAN segment. On WSL2 this
is the most common failure; see the WSL2 networking note above. On native
Linux, check your firewall isn't blocking UDP 5353 (mDNS) or the brain's
WebSocket port (`8765` by default).

**`PyQt6 not installed — running headless`** — expected and harmless if you
didn't install the `[full]` extra, or on WSL2 without an X server. Use
`--headless` explicitly to skip the message.

**LLM never calls any tools / conversations feel "dumber" than expected** —
confirm your Ollama model actually supports tool-calling
(`llama3.2:3b`/`llama3.1:8b` do); a non-tool-calling model will just ignore
the tool schemas and reply in plain text.

**`nvidia-smi` works on Windows but not inside WSL** — update the Windows
NVIDIA driver (not a Linux driver inside WSL), then `wsl --shutdown` from
PowerShell and reopen the WSL terminal.

**Face recognition / Whisper very slow** — confirm `torch`/`onnxruntime-gpu`
are actually using the GPU (`tier`/`gpu` in `~/.milo-brain/config.yaml`
should show your card, not `cpu`). CPU fallback works but is much slower —
fine for pairing/testing, not for a snappy real-time conversation.
