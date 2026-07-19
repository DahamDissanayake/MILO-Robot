# Pipeline warm-up on connect

Date: 2026-07-19

## Problem

Live session against a real robot: the dashboard's pipeline readiness bar sat
at 3/5 ("pending: ASR, TTS") for 8+ minutes and looked frozen. It wasn't stuck
— ASR (Whisper) and TTS (Piper) are **lazy**: they only load the first time
they're actually needed. ASR loads when the first completed speech segment
reaches `transcribe()` (i.e. when the operator finishes speaking a phrase); TTS
loads when the robot first replies. Until the operator speaks, ASR has no reason
to load, so the bar cannot advance — it presents "waiting for you to speak"
identically to "actively loading."

Two consequences:
1. The readiness bar reads as broken/stuck when nothing is wrong.
2. When ASR finally does load on the first utterance, that utterance stalls
   several seconds on a cold model load **plus** a wasted CUDA→CPU rebuild: on
   this machine `device="auto"` picks CUDA, `WhisperModel` constructs fine, then
   the first *inference* fails on a missing `cublas64_12.dll` and the current
   code rebuilds the model on CPU mid-utterance.

## Goals

- The readiness bar reaches 5/5 on its own within a minute or two of
  connecting, without requiring the operator to speak.
- The first spoken interaction has no cold-load stall — models (and the
  CUDA→CPU device resolution) are warm before the operator talks.
- Warming a pipeline in the background is safe against a concurrent first real
  use of the same pipeline.

## Non-goals

- Whisper transcription quality / VAD tuning.
- Vision and VAD are already effectively eager (they load on the first
  video/audio frame, which arrives immediately on connect), and MCP connects at
  session start — none need explicit warm-up. Only ASR and TTS do.
- No change to the readiness-bar rendering itself (the existing "loading" state
  already animates correctly as warm-up flips ASR/TTS `not_loaded`→`loading`→`ready`).

## Design

### A. `LazyLoad` thread-safe load (`brain/milo_brain/pipelines/_lazy.py`)

Add a `threading.Lock` with double-checked locking so a background warm-up
thread and a first real-use thread can't both run `_load()`:

```python
import threading

class LazyLoad:
    def __init__(self) -> None:
        self.status = "not_loaded"  # "not_loaded" | "loading" | "ready" | "error"
        self.error = None
        self._load_lock = threading.Lock()

    def ensure_loaded(self) -> None:
        if self.status == "ready":
            return
        with self._load_lock:
            if self.status == "ready":   # another thread finished while we waited
                return
            self.status, self.error = "loading", None
            try:
                self._load()
                self.status, self.error = "ready", None
            except Exception as exc:
                self.status, self.error = "error", str(exc)
                raise
```

`ensure_loaded()` runs inside worker threads (`asyncio.to_thread`), so a
`threading.Lock` (not `asyncio.Lock`) is correct. This also closes the
pre-existing re-entrancy gap noted in earlier reviews.

### B. Resolve Whisper's device at load time (`brain/milo_brain/pipelines/asr.py`)

Move the CUDA→CPU fallback out of `transcribe()` and into `_load()`, validated
by a tiny probe inference — so once loaded the device is settled and the first
real utterance never rebuilds:

```python
def _load(self) -> None:
    self._model, self._device_in_use = self._build_probed(self._device)

def _build_probed(self, device):
    model = self._build_model(device)
    try:
        self._probe(model)
        return model, device
    except Exception as exc:
        if device == "cpu":
            raise                     # nowhere left to fall back to
        log.warning("whisper device %r failed (%s), falling back to cpu", device, exc)
        model = self._build_model("cpu")
        self._probe(model)            # let a genuine CPU failure propagate
        return model, "cpu"

def _probe(self, model) -> None:
    # ctranslate2 defers CUDA/cuBLAS init to the first inference, so force one
    # here (during load / warm-up) — a broken GPU runtime surfaces now, not on
    # the operator's first utterance.
    segments, _info = model.transcribe(np.zeros(1600, dtype=np.float32),
                                       language="en", beam_size=1)
    list(segments)                    # consume the generator to actually run inference

def transcribe(self, mono_int16) -> Transcript:
    self.ensure_loaded()
    return self._run_transcribe(mono_int16.astype(np.float32) / 32768.0)
```

Real transcribes are already serialized by the session (`_idle(self._segment_task)`
gates one segment at a time), and the probe runs inside the `LazyLoad` load-lock,
so no concurrent inference on one model occurs.

### C. Warm-up task on session start (`brain/milo_brain/session.py`)

`RobotCognitionSession.run()` spawns a background warm-up that eagerly loads the
two user-gated pipelines:

```python
async def run(self):
    log.info("cognition session started for %s", ...)
    self._warmup_task = asyncio.create_task(self._warm_up())
    while True:
        ...

async def _warm_up(self):
    """Eagerly load the user-action-gated pipelines (ASR, TTS) so the readiness
    bar completes on its own and the first interaction has no cold-load stall.
    Failures are non-fatal — a pipeline that can't warm stays errored (shown on
    the dashboard) and the session runs without it."""
    async def warm(name, fn):
        try:
            await asyncio.to_thread(fn)
        except Exception:
            log.warning("warm-up of %s failed", name, exc_info=True)
    await asyncio.gather(
        warm("asr", self._asr.ensure_loaded),
        warm("tts", self._tts.ensure_loaded),
    )
```

`ensure_loaded` is idempotent (early-returns once `ready`), so warming on every
session start is harmless — a reconnect finds already-warm pipelines. The task
runs in the background; it operates on the process-shared factory pipeline
objects, so even if a session ends mid-warm the work still benefits the next
connection. `self._warmup_task = None` is initialized in `__init__` and the
reference is retained so the task isn't garbage-collected.

## Error handling

- A warm-up load failure is caught in `warm()` and logged once; the pipeline's
  `LazyLoad.status` becomes `"error"`, which the readiness bar already renders.
  The session continues; the existing runtime degrade paths (ASR would raise and
  be caught by `_segment_guarded`; TTS `synthesize` returns `b""`) are unchanged.
- The load-lock never deadlocks: `ensure_loaded` acquires it, runs `_load`,
  releases; no nested acquisition.

## Testing

- `_lazy.py`: concurrent `ensure_loaded()` from two threads loads exactly once
  (a `_load` that sleeps briefly + counts calls; assert one call, both callers
  see `ready`).
- `asr.py`: `_load` on a device whose probe fails rebuilds on CPU
  (`_device_in_use == "cpu"`, status `ready`); a device+CPU both-fail raises and
  status is `error`; a healthy device loads without a rebuild. Update the two
  existing ASR fallback tests to the load-time-probe behavior.
- `session.py`: `await session._warm_up()` calls `ensure_loaded` on both ASR and
  TTS (recording fakes); a warm that raises is swallowed and the other pipeline
  still warms; `run()` sets `_warmup_task`.
