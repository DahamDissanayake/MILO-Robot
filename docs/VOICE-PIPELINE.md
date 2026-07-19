# Milo Voice Pipeline — how it works, how to tune, how to test

This is the "voice-mode" loop that runs on the **brain** (your desktop/laptop),
not the robot. The robot streams mic audio + camera video to the brain; the
brain listens, decides when you've finished a sentence, transcribes it, asks the
LLM for a reply, and streams the spoken answer back to the robot.

```
robot mic ──audio──► VAD (detect speech, endpoint on silence)
                         └─► Whisper ASR ──text──► Ollama LLM ──reply──► Piper TTS ──audio──► robot speaker
robot cam ──video──► face vision ──► (identity is optional context, never a gate)
```

Everything below is the brain. After changing config or pulling new code,
**restart the brain**: `python -m milo_brain` (from `brain/`, or however you run
it). Nothing here needs a robot/Pi redeploy.

---

## What each stage does (and the recent fixes)

- **VAD (voice-activity detection)** — `pipelines/vad.py`. A Silero model gates
  the mic stream into speech segments: a segment opens when you start talking and
  closes after a short silence (endpointing). Tuned so it captures a *whole*
  sentence, not just the loud word:
  - `SileroSpeechDetector` threshold **0.5** (standard).
  - `VadSegmenter` `pre_roll_frames=10` (200 ms) — keeps the quiet start of a
    phrase.
  - `VadSegmenter` `min_silence_ms=700` — a natural mid-sentence pause no longer
    splits one sentence into fragments.

- **ASR (speech→text)** — `pipelines/asr.py`, faster-whisper. Loads the model on
  connect (see warm-up) and runs it on the **NVIDIA GPU** when one is present
  (~0.2–0.4 s/clip vs ~3 s on CPU — the main fix for both latency *and*
  mishearing). A per-segment **`no_speech_prob > 0.6` filter** drops Whisper's
  phantom "Bye."/"Thank you." hallucinations before they reach the LLM. With no
  usable CUDA GPU it falls back to **CPU** automatically (slower, lower accuracy
  — see *GPU acceleration* below).

- **LLM (reply + actions)** — `llm/agent.py`, Ollama. Milo **always** replies via
  the LLM; identity (face match / a name you gave this session) is just context,
  never a gate. An LLM error degrades to a spoken fallback (no traceback spam).
  The robot also turns toward your voice and animates its face while speaking —
  those are direct reflexes, not LLM-driven.
  - **Tool-calling** (`llm_use_tools`) lets the LLM autonomously drive the robot
    — call `run_pose` (wave/dance/bow/…), `set_face`, `walk`, etc. — over MCP in
    response to what you say ("wave at me" → it waves). Two things make this work
    even on a small model like `llama3.2:3b`: (1) a **tightened system prompt**
    that enumerates poses vs faces, distinguishes them, and gives worked examples
    ("turn left" → `run_pose(name="turn_left")`); (2) **`repair_tool_args`**,
    which unwraps the `{"object": {…}}` nesting and drops stray params a small
    model dumps into a call, so an *almost*-right call still reaches the robot
    instead of being rejected. Measured on `llama3.2:3b`: 3/6 correct raw → 8/8
    after these fixes. Off by default in code (`BrainConfig.llm_use_tools=False`)
    so a fresh install with any model is safe; turn it on in your config once
    you've confirmed your model calls tools sanely. A bigger model
    (e.g. `qwen2.5:7b-instruct`) is even more reliable if you want it.

- **TTS (text→speech)** — `pipelines/tts.py`, Piper. Auto-downloads the voice on
  first use; if it can't load, Milo stays silent (logged once) instead of
  crashing.

- **Warm-up on connect** — `session.py::_warm_up`. As soon as a robot connects,
  the brain pre-loads ASR + TTS **and** fires a throwaway LLM chat in the
  background. This is why the readiness bar fills on its own (~20–25 s) without
  you speaking, and why the first real reply is ~2 s instead of ~30 s (Ollama
  cold-loads the model on its first request).

---

## Config knobs (`~/.milo-brain/config.yaml`)

| Key | Default | Notes |
|---|---|---|
| `llm_model` | `llama3.2:3b` | Any Ollama model you've `ollama pull`ed. Must fit in RAM/VRAM. |
| `llm_use_tools` | `false` (code default) | Let the LLM autonomously call movement/face tools over MCP. A tightened prompt + `repair_tool_args` make this work on `llama3.2:3b` (~8/8 on simple commands); `qwen2.5:7b-instruct` is even more reliable. |
| `whisper_model` | `small` (`medium` recommended on a GPU) | `tiny`/`base`/`small`/`medium` (or `.en` variants). Bigger = more accurate, slower. `medium` is great on GPU (~0.4 s/clip), slow on CPU. |
| `piper_voice` | `en_US-lessac-medium` | Auto-downloaded on first use. |
| `ollama_url` | `http://127.0.0.1:11434` | Where Ollama runs. |

After editing, restart the brain.

---

## How to test (end to end)

1. **Robot up:** the Pi's `milo-bridge` service is running (it advertises over
   mDNS / listens on `:8765`).
2. **Ollama up + model pulled:** `ollama list` should show your `llm_model`
   (default `llama3.2:3b` — `ollama pull llama3.2:3b` if missing).
3. **Start the brain:** `python -m milo_brain`. On the TUI home screen you'll see:
   - **Pipelines** bar climbing to **5/5** on its own within ~20–25 s of
     connecting (ASR/TTS warming) — you do *not* need to speak for this.
   - **Model:** `responding…` then `ready` (the LLM warmed).
   - **Connection:** `connected: milo`.
4. **Talk to it.** Say a full sentence. Watch the **Conversation** panel:
   - `You: <what you said>` should match your words (full sentence, not one word).
   - `Milo: <reply>` should be a natural sentence (not JSON, not "Bye").
5. **If it mishears / is slow:** you're almost certainly on **CPU Whisper** — see
   below. Check the brain log for `whisper device 'auto' failed ... falling back
   to cpu`.
6. **Web dashboard** (`http://milo.local`): taking pilot control now suspends the
   brain (it goes quiet), releasing resumes it; the camera stays live; the Memory
   Graph is draggable/pannable.

---

## GPU acceleration (the real fix for accuracy + speed)

On CPU, Whisper `small` is both slow (~3 s/clip) and the main source of
mis-hearing. An NVIDIA GPU runs it ~5–10× faster *and* more accurately, and lets
you use the far-more-accurate `medium` model (~0.4 s/clip on a 6 GB RTX 4050).

**This now works out of the box** — no manual steps:

- The CUDA 12 runtime libraries (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, and
  their `nvidia-cuda-nvrtc-cu12` dependency) are declared in the brain's `full`
  extra (`brain/pyproject.toml`). A normal `pip install -e ".\brain[full]"` pulls
  them automatically on Windows/Linux x86_64 (~1.3 GB). There are no macOS/ARM
  wheels, so the markers skip them there and the brain runs CPU Whisper instead.
- On Windows those DLLs aren't on the search path by default, so
  `pipelines/asr.py::_ensure_cuda_dll_path()` locates the installed
  `nvidia/*/bin` dirs (via the `nvidia` namespace package) and registers them
  before ctranslate2's first inference. Nothing for you to configure.
- Device selection is automatic: ASR loads on the GPU when one works and falls
  back to CPU **once**, at warm-up, if it can't (`_build_probed`). The TUI/log
  reports the real device it settled on (`cuda` or `cpu`).

**To use it:**
1. `pip install -e ".\brain[full]"` (or re-run it after pulling — it's a no-op if
   the libs are already present).
2. Set `whisper_model: medium` in `~/.milo-brain/config.yaml` for best accuracy
   (fits a ~6 GB card alongside the LLM).
3. Restart the brain. There should be **no** `cublas64_12.dll ... falling back to
   cpu` warning, and transcription should be fast + accurate.

**Verify it's on the GPU:** the brain log at warm-up should *not* contain
"falling back to cpu". A quick standalone check from the brain's venv:

```bash
python -c "from milo_brain.pipelines.asr import WhisperAsr; a=WhisperAsr('medium'); a.ensure_loaded(); print('device:', a._device_in_use)"
```

Expect `device: cuda`. If you see `device: cpu`, the CUDA libs didn't install
(check `pip show nvidia-cublas-cu12`) or your GPU/driver isn't visible to
ctranslate2.

**If `pip` ever seems to hang** installing the CUDA libs, it's the dependency
resolver backtracking on a version bound — the `full` extra pins cuDNN as `>=9`
with **no** upper bound for exactly this reason; the slow part is only the
download.

**On CPU-only machines:** `small` will still occasionally mis-hear short/unclear
speech. The `no_speech_prob` filter stops it *acting on* the worst phantoms, but
the accuracy ceiling is the model+CPU — a GPU with `medium` is the fix.
