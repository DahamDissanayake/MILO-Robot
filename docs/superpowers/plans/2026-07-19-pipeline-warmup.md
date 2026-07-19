# Pipeline Warm-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ASR + TTS warm up in the background when a robot connects, so the dashboard readiness bar reaches 5/5 on its own (without the operator speaking) and the first spoken interaction has no cold-load stall.

**Architecture:** Three cohesive pieces. (1) `LazyLoad` gets a thread-safe double-checked load lock so a background warm-up can't race a first real load. (2) `WhisperAsr` resolves its CUDA→CPU fallback at load time via a tiny probe inference (moved out of `transcribe`), so once loaded the device is settled. (3) `RobotCognitionSession.run()` spawns a background task that `ensure_loaded()`s ASR and TTS on connect.

**Tech Stack:** Python 3.14, faster-whisper, piper-tts, pytest + pytest-asyncio.

## Global Constraints

- `LazyLoad.status` values stay exactly `"not_loaded" | "loading" | "ready" | "error"`.
- No unit test performs a real model load / network / real inference — all faked/injected, matching the existing suite.
- Warm-up failures are non-fatal: caught and logged, pipeline left `status="error"`, session continues.
- Run `python -m pytest` from `brain/` after each task (baseline 156 before this plan).
- Commit messages: no AI co-author trailer.

---

### Task 1: Thread-safe LazyLoad load

**Files:**
- Modify: `brain/milo_brain/pipelines/_lazy.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Produces: `LazyLoad.ensure_loaded()` is now safe under concurrent calls from multiple threads — `_load()` runs at most once; a second concurrent caller blocks on the lock, then sees `status == "ready"` and returns. Public behavior for single-threaded callers is unchanged (still `not_loaded`→`loading`→`ready`/`error`, still re-raises on failure).

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_pipelines.py` in the `# --- LazyLoad` section:

```python
def test_lazyload_concurrent_ensure_loaded_loads_exactly_once():
    import threading
    import time

    class _SlowLoader(LazyLoad):
        def __init__(self):
            super().__init__()
            self.load_calls = 0

        def _load(self):
            self.load_calls += 1
            time.sleep(0.05)  # widen the race window

    loader = _SlowLoader()
    errors = []

    def call():
        try:
            loader.ensure_loaded()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert loader.load_calls == 1     # only one thread ran _load
    assert loader.status == "ready"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `brain/`): `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k concurrent_ensure_loaded -v`
Expected: FAIL — without the lock, several threads pass the `status != "ready"` check together and `load_calls > 1`.

- [ ] **Step 3: Write the implementation**

Replace the contents of `brain/milo_brain/pipelines/_lazy.py` with:

```python
"""Shared status tracking for pipeline classes that lazily load a heavy
model on first use (Silero VAD, Whisper, Piper, InsightFace). Subclasses
implement _load() (sets whatever model attribute they own, raises on
failure); callers use ensure_loaded() instead of hand-rolling
`if self._model is None: self._load()`, and the dashboard reads .status/
.error to show what's actually working -- including while _load() is
still running. A threading.Lock makes ensure_loaded() safe when a
background warm-up thread and a first-real-use thread call it at once
(both go through asyncio.to_thread, i.e. real OS threads).
"""

from __future__ import annotations

import threading


class LazyLoad:
    def __init__(self) -> None:
        self.status: str = "not_loaded"  # "not_loaded" | "loading" | "ready" | "error"
        self.error: str | None = None
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        raise NotImplementedError

    def ensure_loaded(self) -> None:
        if self.status == "ready":
            return
        with self._load_lock:
            if self.status == "ready":  # another thread finished while we waited
                return
            self.status, self.error = "loading", None
            try:
                self._load()
                self.status, self.error = "ready", None
            except Exception as exc:
                self.status, self.error = "error", str(exc)
                raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k lazyload -v`
Expected: all LazyLoad tests pass, including the new concurrency one and the existing `test_lazyload_*` (the single-threaded transitions are unchanged).

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

```bash
git add brain/milo_brain/pipelines/_lazy.py brain/tests/test_pipelines.py
git commit -m "feat(brain): make LazyLoad.ensure_loaded thread-safe for background warm-up"
```

---

### Task 2: Resolve Whisper's device at load time

**Files:**
- Modify: `brain/milo_brain/pipelines/asr.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Produces: `WhisperAsr._load()` now constructs the model AND runs a tiny probe inference, falling back to CPU during load (not during the first real `transcribe`). `transcribe()` simplifies to `ensure_loaded()` + inference (no fallback logic). `_device_in_use` is set to the resolved device after load.

- [ ] **Step 1: Update the two existing ASR tests + write the new one**

In `brain/tests/test_pipelines.py`, the two existing tests `test_whisper_asr_falls_back_to_cpu_when_the_configured_device_cant_run` and `test_whisper_asr_reraises_when_already_on_cpu` assume the fallback happens inside `transcribe`. Rewrite them for the load-time-probe behavior, and add a healthy-device test. Replace both existing tests with:

```python
def test_whisper_asr_falls_back_to_cpu_at_load_when_the_device_cant_run(monkeypatch):
    """The CUDA->CPU fallback now happens at load time via a probe inference,
    so once loaded the device is settled and transcribe() never rebuilds."""
    import faster_whisper
    from milo_brain.pipelines.asr import WhisperAsr

    class _Segment:
        def __init__(self, text):
            self.text = text
            self.avg_logprob = 0.0

    class _FakeModel:
        def __init__(self, model_size, device, compute_type):
            self.device = device
        def transcribe(self, audio, language, beam_size):
            if self.device != "cpu":
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            return [_Segment(" hello milo")], None

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeModel)

    asr = WhisperAsr(model_size="small", device="cuda")
    asr.ensure_loaded()                     # probe on cuda fails -> rebuild on cpu
    assert asr.status == "ready"
    assert asr._device_in_use == "cpu"

    result = asr.transcribe(np.zeros(1600, dtype=np.int16))  # no rebuild now
    assert result.text == "hello milo"


def test_whisper_asr_load_reraises_when_cpu_also_fails(monkeypatch):
    import faster_whisper
    from milo_brain.pipelines.asr import WhisperAsr

    class _FakeModel:
        def __init__(self, model_size, device, compute_type):
            pass
        def transcribe(self, audio, language, beam_size):
            raise RuntimeError("out of memory")

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeModel)

    asr = WhisperAsr(model_size="small", device="cpu")
    with pytest.raises(RuntimeError, match="out of memory"):
        asr.ensure_loaded()
    assert asr.status == "error"


def test_whisper_asr_healthy_device_loads_without_a_rebuild(monkeypatch):
    import faster_whisper
    from milo_brain.pipelines.asr import WhisperAsr

    class _Segment:
        def __init__(self, text):
            self.text = text
            self.avg_logprob = 0.0

    builds = {"n": 0}

    class _FakeModel:
        def __init__(self, model_size, device, compute_type):
            builds["n"] += 1
            self.device = device
        def transcribe(self, audio, language, beam_size):
            return [_Segment(" hi")], None

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeModel)

    asr = WhisperAsr(model_size="small", device="cpu")
    asr.ensure_loaded()
    assert asr.status == "ready" and asr._device_in_use == "cpu"
    assert builds["n"] == 1                 # constructed exactly once, no fallback rebuild
```

Keep the existing `test_whisper_asr_status_starts_not_loaded` unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k whisper_asr -v`
Expected: FAIL — the fallback is still in `transcribe`, so `ensure_loaded()` on `device="cuda"` currently does NOT fall back (it constructs the cuda model and returns), leaving `_device_in_use == "cuda"`, and the both-fail test doesn't raise from `ensure_loaded`.

- [ ] **Step 3: Write the implementation**

Replace the `_load` method and the `transcribe` method in `brain/milo_brain/pipelines/asr.py`. Change from the current `_load`/`transcribe`/`_run_transcribe` block (lines 30-68) to:

```python
    def _load(self) -> None:
        self._model, self._device_in_use = self._build_probed(self._device)

    def _build_model(self, device: str):
        from faster_whisper import WhisperModel

        return WhisperModel(self._model_size, device=device, compute_type="auto")

    def _build_probed(self, device: str):
        """Construct on ``device`` and validate with a tiny probe inference.
        ctranslate2 defers CUDA/cuBLAS init to the first inference, so a broken
        GPU runtime (e.g. a missing cublas64_12.dll) surfaces here, during load
        / warm-up, instead of on the operator's first utterance. Falls back to
        CPU once; a genuine CPU failure propagates."""
        model = self._build_model(device)
        try:
            self._probe(model)
            return model, device
        except Exception as exc:
            if device == "cpu":
                raise
            log.warning("whisper device %r failed (%s), falling back to cpu", device, exc)
            model = self._build_model("cpu")
            self._probe(model)
            return model, "cpu"

    def _probe(self, model) -> None:
        segments, _info = model.transcribe(
            np.zeros(1600, dtype=np.float32), language="en", beam_size=1
        )
        list(segments)  # consume the generator to actually run inference

    def transcribe(self, mono_int16: np.ndarray) -> Transcript:
        self.ensure_loaded()
        return self._run_transcribe(mono_int16.astype(np.float32) / 32768.0)

    def _run_transcribe(self, audio: np.ndarray) -> Transcript:
        segments, _info = self._model.transcribe(audio, language="en", beam_size=3)
        texts, probs = [], []
        for segment in segments:
            texts.append(segment.text.strip())
            probs.append(np.exp(segment.avg_logprob))
        if not texts:
            return Transcript(text="", confidence=0.0)
        return Transcript(text=" ".join(texts).strip(), confidence=float(np.mean(probs)))
```

(`__init__`, `Transcript`, imports, and `log` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k whisper_asr -v`
Expected: all pass.

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

```bash
git add brain/milo_brain/pipelines/asr.py brain/tests/test_pipelines.py
git commit -m "feat(brain): resolve Whisper's CPU fallback at load time so the first utterance never rebuilds"
```

---

### Task 3: Warm-up task on session start

**Files:**
- Modify: `brain/milo_brain/session.py`
- Test: `brain/tests/test_cognition_session.py`

**Interfaces:**
- Consumes: `self._asr.ensure_loaded` / `self._tts.ensure_loaded` (both `LazyLoad`, from Tasks 1-2 and the existing pipelines).
- Produces: `RobotCognitionSession._warm_up()` (async) that eagerly loads ASR + TTS in background threads, guarded; `run()` spawns it into `self._warmup_task` at start. `self._warmup_task` initialized to `None` in `__init__`.

- [ ] **Step 1: Update the test fakes and write the failing tests**

In `brain/tests/test_cognition_session.py`, the `FakeAsr` and `FakeTts` need an `ensure_loaded` recorder. Change:

```python
class FakeAsr:
    def transcribe(self, mono):
        return Transcript(text="hello milo", confidence=0.9)
```

to:

```python
class FakeAsr:
    def __init__(self):
        self.warmed = 0
    def ensure_loaded(self):
        self.warmed += 1
    def transcribe(self, mono):
        return Transcript(text="hello milo", confidence=0.9)
```

and change:

```python
class FakeTts:
    def synthesize(self, text):
        return b"\x00\x01" * FRAME * 2  # two frames of "speech"
```

to:

```python
class FakeTts:
    def __init__(self):
        self.warmed = 0
    def ensure_loaded(self):
        self.warmed += 1
    def synthesize(self, text):
        return b"\x00\x01" * FRAME * 2  # two frames of "speech"
```

`build_session` already constructs `FakeAsr()` / `FakeTts()` — confirm those call sites still pass no args (they do). Add these tests to the end of `brain/tests/test_cognition_session.py`:

```python
def test_warm_up_preloads_asr_and_tts():
    async def main():
        session, robot_sock, robot, mcp = build_session(lambda op, header: {})
        await session._warm_up()
        return session._asr.warmed, session._tts.warmed

    asr_warmed, tts_warmed = asyncio.run(main())
    assert asr_warmed == 1 and tts_warmed == 1


def test_warm_up_survives_a_pipeline_failure():
    async def main():
        session, robot_sock, robot, mcp = build_session(lambda op, header: {})

        def boom():
            raise RuntimeError("model download failed")
        session._asr.ensure_loaded = boom  # ASR can't warm

        await session._warm_up()            # must not raise
        return session._tts.warmed

    assert asyncio.run(main()) == 1         # TTS still warmed despite ASR failing


def test_run_spawns_the_warm_up_task():
    async def main():
        session, robot_sock, robot, mcp = build_session(lambda op, header: {})
        run_task = asyncio.create_task(session.run())
        await asyncio.sleep(0.05)           # let run() start and spawn warm-up
        spawned = session._warmup_task is not None
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        return spawned

    assert asyncio.run(main()) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_cognition_session.py -k warm_up -v` and `-k run_spawns`
Expected: FAIL — `_warm_up` / `_warmup_task` don't exist yet.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/session.py`, in `RobotCognitionSession.__init__`, after `self._segment_task: asyncio.Task | None = None`, add:

```python
        self._warmup_task: asyncio.Task | None = None
```

Change `run()` to spawn the warm-up. The current `run()` starts:

```python
    async def run(self) -> None:
        """Recv loop. ..."""
        log.info("cognition session started for %s", self._peer.name or self._peer.id)
        while True:
```

Insert the spawn right after the log line:

```python
    async def run(self) -> None:
        """Recv loop. ..."""
        log.info("cognition session started for %s", self._peer.name or self._peer.id)
        self._warmup_task = asyncio.create_task(self._warm_up())
        while True:
```

Add the `_warm_up` method (place it right after `run()`, before `_on_video`):

```python
    async def _warm_up(self) -> None:
        """Eagerly load the user-action-gated pipelines (ASR, TTS) as soon as we
        connect, so the readiness bar completes on its own and the first spoken
        interaction has no cold-load stall. Failures are non-fatal -- a pipeline
        that can't warm stays errored (shown on the dashboard) and the session
        runs without it."""
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_cognition_session.py -v`
Expected: all pass, including the pre-existing session tests (the added warm-up task is cancelled/harmless when those tests cancel `run()`).

- [ ] **Step 5: Run the full brain suite**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

- [ ] **Step 6: Manual verification (optional, needs the real robot)**

If a robot is reachable, this is verifiable live: connect the brain, and confirm the dashboard readiness bar climbs to 5/5 within a minute or two **without speaking**, and that the first spoken reply is prompt. If no robot is reachable, note that the automated tests are the authoritative check.

- [ ] **Step 7: Commit**

```bash
git add brain/milo_brain/session.py brain/tests/test_cognition_session.py
git commit -m "feat(brain): warm up ASR and TTS on connect so readiness completes without speaking"
```
