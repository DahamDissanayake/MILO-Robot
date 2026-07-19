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
  connect (see warm-up). A per-segment **`no_speech_prob > 0.6` filter** drops
  Whisper's phantom "Bye."/"Thank you." hallucinations before they reach the LLM.
  On a machine with no working CUDA it runs on **CPU** (slower, lower accuracy —
  see *GPU acceleration* below).

- **LLM (reply)** — `llm/agent.py`, Ollama. Milo **always** replies via the LLM;
  identity (face match / a name you gave this session) is just context, never a
  gate. **Tool-calling is OFF by default** (`llm_use_tools`) because small models
  (e.g. `llama3.2:3b`) call tools unreliably and it broke the spoken reply into
  raw JSON / "Hmm."; with tools off, Ollama's strict JSON mode gives clean
  conversational replies. An LLM error degrades to a spoken fallback (no
  traceback spam). The robot still turns toward your voice and animates its face
  while speaking — those are direct, not LLM-driven.

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
| `llm_use_tools` | `false` | Let the LLM autonomously call movement/face tools. Only turn on with a capable large model — small models break on it. |
| `whisper_model` | `small` | `tiny`/`base`/`small`/`medium` (or `.en` variants). Bigger = more accurate, slower. `medium` is great on GPU, slow on CPU. |
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
mis-hearing. Your NVIDIA GPU can run it ~5–10× faster *and* more accurately (and
lets you use the `medium` model), but faster-whisper/ctranslate2 needs the CUDA
12 runtime libraries, which aren't bundled.

**Enable it (one-time, ~1.3 GB download):**

```bash
# from the brain's venv:
python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Then, once installed:
- Set `whisper_model: medium` in `~/.milo-brain/config.yaml` for best accuracy
  (fits your ~6 GB VRAM; falls back to CPU automatically if the GPU can't load).
- Restart the brain. The log should stop showing the `cublas64_12.dll ... falling
  back to cpu` warning, and transcription should be fast + accurate.

If `pip` seems to hang with no output, it's the dependency resolver — install the
two packages **without version constraints** (as above), which resolves instantly;
the slow part is just the download.

**Known limit until GPU is on:** `small` on CPU will still occasionally mis-hear
short/unclear speech. The `no_speech_prob` filter stops it *acting on* the worst
phantoms, but the accuracy ceiling is the model+CPU — GPU `medium` is the fix.
