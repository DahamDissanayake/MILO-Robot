# Brain Dashboard Connection-Stage + Pipeline Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the brain TUI dashboard's binary "connected"/"no robot connected" status with the connector's actual live stage (idle/connecting/handshaking/connected/retrying) and per-pipeline (VAD/ASR/TTS/Vision/MCP) load status.

**Architecture:** `net/connector.py` grows a small state machine (`link_state`, `link_target`, `last_error`, `retry_at`) set at points that already exist in its connect/retry flow. A new `pipelines/_lazy.py::LazyLoad` mixin gives the four model-holding pipeline classes a uniform `status`/`error` pair, replacing their hand-rolled `if self._model is None: self._load()` guards. `CognitionSessionFactory` (currently discarded by `__main__.py` after construction) is threaded through to `MiloBrainApp` so the dashboard can read `factory.pipeline_status()`. `DashboardScreen.refresh_from()` (already polled every 1s) renders both.

**Tech Stack:** Python 3.14, Textual (TUI), pytest + pytest-asyncio.

## Global Constraints

- No new test coverage for `__main__.py` (it has none today; this plan doesn't add a first test file for a thin entrypoint).
- No unit test asserts real Silero/Whisper/Piper/InsightFace model loading — all `_load()` calls that hit torch.hub/network stay behind fakes/monkeypatches, exactly as the existing test suite already does.
- `link_state` values are exactly: `"idle" | "connecting" | "handshaking" | "connected" | "retrying"` (renames the old `"disconnected"` to `"idle"` everywhere).
- Every pipeline `status` value is exactly: `"not_loaded" | "ready" | "error"`.
- Run `python -m pytest` from `brain/` after every task (full suite, not just the new test) — this codebase is small enough (100+ tests, ~25s) that there's no reason to scope down.

---

### Task 1: `LazyLoad` mixin

**Files:**
- Create: `brain/milo_brain/pipelines/_lazy.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Produces: `LazyLoad` class with `self.status: str` (`"not_loaded"|"ready"|"error"`), `self.error: str | None`, and `ensure_loaded() -> None`. Subclasses implement `_load(self) -> None` (raises on failure, otherwise sets whatever model attribute it owns). `ensure_loaded()` is a no-op once `status == "ready"`; otherwise calls `_load()`, sets `status`/`error` on success or failure, and re-raises on failure.

- [ ] **Step 1: Write the failing tests**

Add to the end of `brain/tests/test_pipelines.py`:

```python
# --- LazyLoad ------------------------------------------------------------

from milo_brain.pipelines._lazy import LazyLoad


class _Loader(LazyLoad):
    def __init__(self, fail: bool = False):
        super().__init__()
        self.fail = fail
        self.load_calls = 0

    def _load(self) -> None:
        self.load_calls += 1
        if self.fail:
            raise RuntimeError("boom")


def test_lazyload_starts_not_loaded():
    loader = _Loader()
    assert loader.status == "not_loaded"
    assert loader.error is None


def test_lazyload_ensure_loaded_transitions_to_ready():
    loader = _Loader()
    loader.ensure_loaded()
    assert loader.status == "ready"
    assert loader.error is None
    assert loader.load_calls == 1


def test_lazyload_ensure_loaded_is_a_noop_once_ready():
    loader = _Loader()
    loader.ensure_loaded()
    loader.ensure_loaded()
    assert loader.load_calls == 1


def test_lazyload_ensure_loaded_transitions_to_error_and_reraises():
    loader = _Loader(fail=True)
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="boom"):
        loader.ensure_loaded()
    assert loader.status == "error"
    assert loader.error == "boom"


def test_lazyload_ensure_loaded_retries_after_a_previous_error():
    loader = _Loader(fail=True)
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        loader.ensure_loaded()
    loader.fail = False
    loader.ensure_loaded()
    assert loader.status == "ready"
    assert loader.error is None
    assert loader.load_calls == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `brain/`): `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k lazyload -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.pipelines._lazy'`

- [ ] **Step 3: Write the implementation**

Create `brain/milo_brain/pipelines/_lazy.py`:

```python
"""Shared status tracking for pipeline classes that lazily load a heavy
model on first use (Silero VAD, Whisper, Piper, InsightFace). Subclasses
implement _load() (sets whatever model attribute they own, raises on
failure); callers use ensure_loaded() instead of hand-rolling
`if self._model is None: self._load()`, and the dashboard reads .status/
.error to show what's actually working.
"""

from __future__ import annotations


class LazyLoad:
    def __init__(self) -> None:
        self.status: str = "not_loaded"  # "not_loaded" | "ready" | "error"
        self.error: str | None = None

    def _load(self) -> None:
        raise NotImplementedError

    def ensure_loaded(self) -> None:
        if self.status == "ready":
            return
        try:
            self._load()
            self.status, self.error = "ready", None
        except Exception as exc:
            self.status, self.error = "error", str(exc)
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k lazyload -v`
Expected: 5 passed

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing, no regressions

```bash
git add brain/milo_brain/pipelines/_lazy.py brain/tests/test_pipelines.py
git commit -m "feat(brain): add LazyLoad mixin for pipeline load-status tracking"
```

---

### Task 2: `SileroSpeechDetector` uses `LazyLoad`; `VadSegmenter` exposes status

**Files:**
- Modify: `brain/milo_brain/pipelines/vad.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Consumes: `LazyLoad` from Task 1.
- Produces: `SileroSpeechDetector.status`/`.error` (inherited from `LazyLoad`); `SileroSpeechDetector(model=...)` sets `status="ready"` immediately, bypassing `_load()`. `VadSegmenter.status: str` / `.error: str | None` properties delegating to whatever `is_speech` callable was injected (default `"ready"`/`None` if it doesn't define them).

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_pipelines.py`, after the existing `test_silero_detector_buffers_20ms_frames_to_the_models_minimum_chunk` test:

```python
def test_silero_detector_status_starts_not_loaded_without_injected_model():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    detector = SileroSpeechDetector()
    assert detector.status == "not_loaded"
    assert detector.error is None


def test_silero_detector_status_is_ready_immediately_when_model_injected():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    detector = SileroSpeechDetector(model=_FakeSileroModel())
    assert detector.status == "ready"


def test_vad_segmenter_status_defaults_to_ready_for_a_plain_fake_detector():
    seg = VadSegmenter(is_speech=energy_detector, min_silence_ms=60)
    assert seg.status == "ready"
    assert seg.error is None


def test_vad_segmenter_status_delegates_to_an_injected_silero_detector():
    from milo_brain.pipelines.vad import SileroSpeechDetector

    detector = SileroSpeechDetector(model=_FakeSileroModel())
    seg = VadSegmenter(is_speech=detector, min_silence_ms=60)
    assert seg.status == "ready"
    assert seg.error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k "silero_detector_status or vad_segmenter_status" -v`
Expected: FAIL — `SileroSpeechDetector`/`VadSegmenter` have no `status` attribute yet.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/pipelines/vad.py`, add the import and change the two classes:

```python
from ._lazy import LazyLoad
```//add near the top, after `import numpy as np`

Replace the `class SileroSpeechDetector:` line (line 33) through the end of its `__init__` (lines 33-52) with:

```python
class SileroSpeechDetector(LazyLoad):
    """Loads Silero VAD lazily (torch hub); callable(mono int16) -> bool.

    Silero's model rejects any chunk where sr / len(chunk) > 31.25 -- at
    16 kHz that's anything under 512 samples (32 ms). The wire protocol
    locks frames at 320 samples (20 ms, see
    bridge/milo_bridge/drivers/audio.py's FRAME_SAMPLES), so raw frames are
    buffered here and only handed to the model once 512 samples have
    accumulated; the decision from the last full window is reused for the
    frames in between.
    """

    REQUIRED_SAMPLES = 512  # sr / 31.25 at 16 kHz -- Silero's minimum chunk length

    def __init__(self, threshold: float = 0.5, model=None):
        super().__init__()
        self._threshold = threshold
        self._model = model
        self._torch = None
        self._buffer = np.empty(0, dtype=np.int16)
        self._last_speaking = False
        if model is not None:
            self.status = "ready"
```

Then replace the `__call__` method's first two lines (currently `if self._model is None:` / `    self._load()`, lines 68-69) with:

```python
    def __call__(self, mono: np.ndarray) -> bool:
        self.ensure_loaded()
        if self._torch is None:
```

(the rest of `__call__` — the `import torch` fallback and the buffering loop — is unchanged).

Then add `status`/`error` properties to `VadSegmenter`, right after its `__init__` (after line 108, before `def push`):

```python
    @property
    def status(self) -> str:
        return getattr(self._is_speech, "status", "ready")

    @property
    def error(self) -> str | None:
        return getattr(self._is_speech, "error", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -v`
Expected: all pass, including the pre-existing `test_silero_detector_buffers_20ms_frames_to_the_models_minimum_chunk`.

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/pipelines/vad.py brain/tests/test_pipelines.py
git commit -m "feat(brain): expose VAD load status via LazyLoad"
```

---

### Task 3: `WhisperAsr` uses `LazyLoad`

**Files:**
- Modify: `brain/milo_brain/pipelines/asr.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Consumes: `LazyLoad` from Task 1.
- Produces: `WhisperAsr.status`/`.error`.

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_pipelines.py`:

```python
def test_whisper_asr_status_starts_not_loaded():
    from milo_brain.pipelines.asr import WhisperAsr

    asr = WhisperAsr()
    assert asr.status == "not_loaded"
    assert asr.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k whisper_asr_status -v`
Expected: FAIL — `WhisperAsr` has no `status` attribute.

- [ ] **Step 3: Write the implementation**

Replace the full contents of `brain/milo_brain/pipelines/asr.py` with:

```python
"""Speech-to-text with faster-whisper. Model size follows the brain tier
(small on a 6 GB card, medium on the big box); loads lazily on first use."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._lazy import LazyLoad


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float  # mean segment probability, 0-1


class WhisperAsr(LazyLoad):
    def __init__(self, model_size: str = "small", device: str = "auto"):
        super().__init__()
        self._model_size = model_size
        self._device = device
        self._model = None

    def _load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self._model_size, device=self._device, compute_type="auto")

    def transcribe(self, mono_int16: np.ndarray) -> Transcript:
        self.ensure_loaded()
        audio = mono_int16.astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(audio, language="en", beam_size=3)
        texts, probs = [], []
        for segment in segments:
            texts.append(segment.text.strip())
            probs.append(np.exp(segment.avg_logprob))
        if not texts:
            return Transcript(text="", confidence=0.0)
        return Transcript(text=" ".join(texts).strip(), confidence=float(np.mean(probs)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k whisper_asr_status -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/pipelines/asr.py brain/tests/test_pipelines.py
git commit -m "feat(brain): expose ASR load status via LazyLoad"
```

---

### Task 4: `PiperTts` uses `LazyLoad`

**Files:**
- Modify: `brain/milo_brain/pipelines/tts.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Consumes: `LazyLoad` from Task 1.
- Produces: `PiperTts.status`/`.error`.

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_pipelines.py`:

```python
def test_piper_tts_status_starts_not_loaded():
    from milo_brain.pipelines.tts import PiperTts

    tts = PiperTts()
    assert tts.status == "not_loaded"
    assert tts.error is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k piper_tts_status -v`
Expected: FAIL — `PiperTts` has no `status` attribute.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/pipelines/tts.py`, add the import after `import numpy as np`:

```python
from ._lazy import LazyLoad
```

Replace the `PiperTts` class (currently):

```python
class PiperTts:
    def __init__(self, voice: str = "en_US-lessac-medium"):
        self._voice_name = voice
        self._voice = None

    def _load(self) -> None:
        from piper import PiperVoice

        self._voice = PiperVoice.load(self._voice_name)

    def synthesize(self, text: str) -> bytes:
        """16 kHz mono s16le for ``{"t":"tts"}`` frames."""
        if self._voice is None:
            self._load()
```

with:

```python
class PiperTts(LazyLoad):
    def __init__(self, voice: str = "en_US-lessac-medium"):
        super().__init__()
        self._voice_name = voice
        self._voice = None

    def _load(self) -> None:
        from piper import PiperVoice

        self._voice = PiperVoice.load(self._voice_name)

    def synthesize(self, text: str) -> bytes:
        """16 kHz mono s16le for ``{"t":"tts"}`` frames."""
        self.ensure_loaded()
```

(the rest of `synthesize` — the `for chunk in self._voice.synthesize(text):` loop onward — is unchanged).

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k piper_tts_status -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/pipelines/tts.py brain/tests/test_pipelines.py
git commit -m "feat(brain): expose TTS load status via LazyLoad"
```

---

### Task 5: `InsightFaceAnalyzer` uses `LazyLoad`; `FaceVision` exposes status

**Files:**
- Modify: `brain/milo_brain/pipelines/vision.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Consumes: `LazyLoad` from Task 1.
- Produces: `InsightFaceAnalyzer.status`/`.error` (inherited); `FaceVision.status: str` / `.error: str | None` properties delegating to `self._analyzer`.

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_pipelines.py`:

```python
def test_face_vision_status_defaults_to_ready_for_a_plain_fake_analyzer():
    vision = FaceVision(analyzer=lambda img: [], clock=lambda: 0.0)
    assert vision.status == "ready"
    assert vision.error is None


def test_face_vision_status_delegates_to_a_lazyload_analyzer():
    from milo_brain.pipelines._lazy import LazyLoad

    class FakeAnalyzerLoader(LazyLoad):
        def _load(self):
            pass

        def __call__(self, img):
            return []

    analyzer = FakeAnalyzerLoader()
    vision = FaceVision(analyzer=analyzer, clock=lambda: 0.0)
    assert vision.status == "not_loaded"
    analyzer.ensure_loaded()
    assert vision.status == "ready"


def test_insightface_analyzer_status_starts_not_loaded():
    from milo_brain.pipelines.vision import InsightFaceAnalyzer

    analyzer = InsightFaceAnalyzer()
    assert analyzer.status == "not_loaded"
    assert analyzer.error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k "face_vision_status or insightface_analyzer_status" -v`
Expected: FAIL — neither class has a `status` attribute yet.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/pipelines/vision.py`, add the import after `import numpy as np`:

```python
from ._lazy import LazyLoad
```

Replace the `InsightFaceAnalyzer` class's declaration and `__init__` (currently):

```python
class InsightFaceAnalyzer:
    def __init__(self, use_gpu: bool = True):
        self._use_gpu = use_gpu
        self._app = None

    def _load(self) -> None:
```

with:

```python
class InsightFaceAnalyzer(LazyLoad):
    def __init__(self, use_gpu: bool = True):
        super().__init__()
        self._use_gpu = use_gpu
        self._app = None

    def _load(self) -> None:
```

Replace `InsightFaceAnalyzer.__call__`'s first two lines (currently `if self._app is None:` / `    self._load()`) with:

```python
    def __call__(self, bgr_image: np.ndarray) -> list[FaceObservation]:
        self.ensure_loaded()
```

Add `status`/`error` properties to `FaceVision`, right after its `__init__` (before `def process_jpeg`):

```python
    @property
    def status(self) -> str:
        return getattr(self._analyzer, "status", "ready")

    @property
    def error(self) -> str | None:
        return getattr(self._analyzer, "error", None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/pipelines/vision.py brain/tests/test_pipelines.py
git commit -m "feat(brain): expose Vision load status via LazyLoad"
```

---

### Task 6: `MiloMcpClient.connected`

**Files:**
- Modify: `brain/milo_brain/mcp_client.py`
- Test: `brain/tests/test_mcp_client.py`

**Interfaces:**
- Produces: `MiloMcpClient.connected: bool` property (`True` once `connect()` has completed and before `close()`).

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_mcp_client.py`, after the existing imports:

```python
import asyncio

from milo_brain.mcp_client import MiloMcpClient
```

Then add at the end of the file:

```python
def test_connected_is_false_before_connect():
    client = MiloMcpClient("http://x", token="t", peer_id="p")
    assert client.connected is False


def test_connected_is_true_once_a_session_exists():
    client = MiloMcpClient("http://x", token="t", peer_id="p")
    client._session = object()  # simulate what a completed connect() leaves behind
    assert client.connected is True


def test_connected_is_false_after_close():
    client = MiloMcpClient("http://x", token="t", peer_id="p")
    client._session = object()
    asyncio.run(client.close())
    assert client.connected is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_mcp_client.py -k connected -v`
Expected: FAIL — `MiloMcpClient` has no `connected` attribute.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/mcp_client.py`, add this property to `MiloMcpClient` right after `__init__` (before `async def connect`):

```python
    @property
    def connected(self) -> bool:
        return self._session is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_mcp_client.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/mcp_client.py brain/tests/test_mcp_client.py
git commit -m "feat(brain): add MiloMcpClient.connected status property"
```

---

### Task 7: Connector connection-stage state machine

**Files:**
- Modify: `brain/milo_brain/net/connector.py`
- Test: `brain/tests/test_connector.py`

**Interfaces:**
- Produces (on `RobotConnectorManager`): `link_state: str` (`"idle"|"connecting"|"handshaking"|"connected"|"retrying"`, renamed from the old `"disconnected"` value), `link_target: tuple[str, int] | None`, `last_error: str | None`, `retry_at: float | None` (a `time.monotonic()` deadline, `None` when not retrying), `consecutive_drops: int` (renamed from the existing private `_consecutive_drops`, same semantics).

- [ ] **Step 1: Update existing tests for the `link_state` rename and `consecutive_drops` rename**

In `brain/tests/test_connector.py`:

Change `test_tick_waits_when_nothing_discovered`'s assertion from:
```python
    assert connector.link_state == "disconnected"
```
to:
```python
    assert connector.link_state == "idle"
```

Change `test_tick_connects_to_a_selected_robot_and_runs_the_session_handler`'s assertion from:
```python
        assert connector.link_state == "disconnected"
```
to:
```python
        assert connector.link_state == "idle"
```

In `test_consecutive_drops_counts_up_on_repeated_connect_failures`, change:
```python
    assert connector._consecutive_drops == 0
    asyncio.run(connector._tick())  # backoff is 1s on the first drop
    assert connector._consecutive_drops == 1
```
to:
```python
    assert connector.consecutive_drops == 0
    asyncio.run(connector._tick())  # backoff is 1s on the first drop
    assert connector.consecutive_drops == 1
    assert connector.link_state == "retrying"
    assert "getaddrinfo failed" in connector.last_error
```

In `test_consecutive_drops_resets_after_a_successful_connect`, change:
```python
        connector._consecutive_drops = 3  # simulate a prior run of failures
```
to:
```python
        connector.consecutive_drops = 3  # simulate a prior run of failures
```
and change:
```python
        assert connector._consecutive_drops == 0
```
to:
```python
        assert connector.consecutive_drops == 0
        assert connector.link_state == "idle"
```

- [ ] **Step 2: Write new failing tests for the stage transitions**

Add to `brain/tests/test_connector.py`, after `test_consecutive_drops_resets_after_a_successful_connect`:

```python
def test_tick_shows_connecting_then_handshaking_before_the_session_starts(tmp_path):
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        token = derive_token("123456", "milo-1", "brain-1")
        PairedStore(cfg.paired_path).add("milo-1", token)
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        robot_store.add("brain-1", token)

        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot

        async def handler(sock, peer):
            pass

        discovery = FakeDiscoveryWith(
            [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765)]
        )
        connector = RobotConnectorManager(
            cfg, session_handler=handler, discovery=discovery,
            connect=lambda url: _ConnectCM(raw_brain),
        )

        assert connector.link_state == "idle"
        tick_task = asyncio.create_task(connector._tick())
        await asyncio.sleep(0)  # let _tick() start; handshake hasn't completed yet
        assert connector.link_state in ("connecting", "handshaking")
        assert connector.link_target == ("10.0.0.9", 8765)

        robot_task = asyncio.create_task(
            robot_handshake(MiloSocket(raw_robot), "milo-1", "milo", robot_store, mcp_port=0)
        )
        await tick_task
        await robot_task

        assert connector.link_state == "idle"  # handler returned immediately -> session ended

    asyncio.run(main())


def test_tick_sets_idle_and_clears_target_when_nothing_is_discovered(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)

    async def handler(sock, peer):
        raise AssertionError("must never be reached -- nothing was discovered")

    connector = RobotConnectorManager(
        cfg, session_handler=handler, discovery=FakeDiscoveryEmpty(),
    )
    connector.link_state = "retrying"
    connector.link_target = ("10.0.0.9", 8765)

    asyncio.run(connector._tick())

    assert connector.link_state == "idle"
    assert connector.link_target is None


def test_handshake_failure_sets_idle_with_last_error(tmp_path):
    from milo_common import protocol

    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)
    discovery = FakeDiscoveryWith(
        [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765, pairing=True)]
    )
    # brain_handshake() always reads the robot's hello first (_expect at the
    # top of handshake.py); pre-seeding an "error" frame instead makes
    # _expect raise HandshakeError immediately instead of hanging on recv().
    raw_brain = FakeWebSocket()
    raw_brain.outbox.put_nowait(protocol.encode_header(protocol.T_ERROR, code="bad_pin"))
    connector = RobotConnectorManager(
        cfg, request_pin=lambda name: None, session_handler=lambda sock, peer: None,
        discovery=discovery, connect=lambda url: _ConnectCM(raw_brain),
    )

    asyncio.run(connector._tick())

    assert connector.link_state == "idle"
    assert connector.last_error is not None and "handshake failed" in connector.last_error
```

- [ ] **Step 3: Run tests to verify the new ones fail and the renamed ones fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_connector.py -v`
Expected: the renamed-assertion tests FAIL (`AttributeError` or wrong string), the three new tests FAIL (`AttributeError: 'RobotConnectorManager' object has no attribute 'link_target'` etc.)

- [ ] **Step 4: Write the implementation**

In `brain/milo_brain/net/connector.py`, add `import time` after `import logging`.

Replace `__init__`'s body (lines 62-89) with:

```python
    def __init__(
        self,
        cfg,
        *,
        request_pin: Callable[[str], Awaitable[str | None]] | None = None,
        session_handler: RobotHandler,
        discovery: RobotDiscovery | None = None,
        connect=None,
    ):
        self._cfg = cfg
        self._request_pin = request_pin
        self._session_handler = session_handler
        self._store = PairedStore(cfg.paired_path)
        self.discovery = discovery or RobotDiscovery()  # public: ConnectRobotsScreen reads .snapshot()
        self._connect = connect
        self.connected_robot: Peer | None = None
        # "idle" | "connecting" | "handshaking" | "connected" | "retrying" --
        # read by the dashboard every poll to show what's actually happening.
        self.link_state: str = "idle"
        self.link_target: tuple[str, int] | None = None  # host:port currently being dialed
        self.last_error: str | None = None  # most recent connect/handshake failure
        self.retry_at: float | None = None  # time.monotonic() deadline for the next attempt
        self._manual_target: str | None = None
        self._manual_host_target: tuple[str, int] | None = None
        # Last robot this process actually completed a handshake with --
        # lets the dashboard's one-key Reconnect redial immediately instead
        # of waiting for the next scheduled retry or a fresh discovery scan.
        self.last_connected: tuple[str, int] | None = None
        self._wake = asyncio.Event()
        # Consecutive connection drops since the last successful connect --
        # drives _drop_backoff_seconds(); reset to 0 as soon as a connect
        # succeeds again.
        self.consecutive_drops = 0
```

Replace `_tick()`'s body (lines 140-153) with:

```python
    async def _tick(self) -> None:
        manual_host_target, self._manual_host_target = self._manual_host_target, None
        if manual_host_target is not None:
            host, port = manual_host_target
            await self._connect_and_run(f"ws://{host}:{port}", offer_pairing=True)
            return

        manual_target, self._manual_target = self._manual_target, None
        choice = select_robot(self.discovery.snapshot(), self._store, manual_target=manual_target)
        if choice is None:
            self.link_state = "idle"
            self.link_target = None
            await self._wait_before_retry(self._cfg.reconnect_seconds)
            return
        record, needs_pairing = choice
        await self._connect_and_run(record.url, offer_pairing=needs_pairing)
```

Replace `_connect_and_run()`'s body (lines 166-201) with:

```python
    async def _connect_and_run(self, url: str, *, offer_pairing: bool) -> None:
        self.link_state = "connecting"
        self.link_target = _parse_host_port(url)
        self.last_error = None
        try:
            async with self._connect(url) as ws:
                sock = MiloSocket(ws)
                self.link_state = "handshaking"
                peer = await brain_handshake(
                    sock,
                    self._cfg.brain_id,
                    self._cfg.name,
                    self._cfg.tier,
                    self._store,
                    request_pin=self._request_pin if offer_pairing else None,
                )
                if peer.mcp_port:
                    host = ws.remote_address[0]  # websockets client connections expose this too
                    peer = replace(peer, mcp_url=f"http://{host}:{peer.mcp_port}")
                log.info("connected to robot %s (%s)", peer.name, peer.id)
                self.connected_robot = peer
                self.link_state = "connected"
                self.last_connected = _parse_host_port(url)
                self.consecutive_drops = 0
                try:
                    await self._session_handler(sock, peer)
                finally:
                    self.connected_robot = None
                    self.link_state = "idle"
        except HandshakeError as exc:
            self.link_state = "idle"
            self.last_error = f"handshake failed: {exc}"
            log.warning("handshake with %s failed: %s", url, exc)
            await self._wait_before_retry(self._cfg.reconnect_seconds)
        except Exception as exc:  # connection drop -> fail over on next tick
            self.consecutive_drops += 1
            backoff = _drop_backoff_seconds(self.consecutive_drops)
            self.link_state = "retrying"
            self.retry_at = time.monotonic() + backoff
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info(
                "robot link lost (%s: %s), rescanning in %.0fs",
                type(exc).__name__, exc, backoff,
            )
            await self._wait_before_retry(backoff)
            self.retry_at = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_connector.py -v`
Expected: all pass. If `test_handshake_failure_sets_idle_with_last_error` fails because `brain_handshake()` hangs instead of raising, fix the fake per that test's note in Step 2 and rerun.

- [ ] **Step 6: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/net/connector.py brain/tests/test_connector.py
git commit -m "feat(brain): track live connection stage on the connector"
```

---

### Task 8: `pipeline_status()` on the session and factory

**Files:**
- Modify: `brain/milo_brain/session.py`
- Test: `brain/tests/test_cognition_session.py`

**Interfaces:**
- Consumes: `VadSegmenter.status`/`.error` (Task 2), `WhisperAsr`/`PiperTts`/`FaceVision`'s `.status`/`.error` (Tasks 3-5), `MiloMcpClient.connected` (Task 6).
- Produces: `RobotCognitionSession.pipeline_status() -> dict[str, tuple[str, str | None]]` (keys: `"vad"`, plus `"mcp"` when the session has an MCP client). `CognitionSessionFactory.current_session: RobotCognitionSession | None` (set for the duration of `handle()`'s `session.run()`, `None` otherwise) and `CognitionSessionFactory.pipeline_status() -> dict[str, tuple[str, str | None]]` (keys: `"asr"`, `"tts"`, `"vision"`, plus `"vad"`/`"mcp"` merged in from `current_session.pipeline_status()` when one is active).

- [ ] **Step 1: Update the `FakeMcp` test fixture and write the failing tests**

In `brain/tests/test_cognition_session.py`, `FakeMcp.__init__` (currently):

```python
class FakeMcp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status = {"ok": True, "current_face": "happy"}
```

add a `connected` attribute so it satisfies the same interface the real `MiloMcpClient` now exposes:

```python
class FakeMcp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status = {"ok": True, "current_face": "happy"}
        self.connected = True
```

Add to the end of `brain/tests/test_cognition_session.py`:

```python
def test_session_pipeline_status_reports_vad_and_mcp():
    session, robot_sock, robot, mcp = build_session(lambda op, header: {})
    status = session.pipeline_status()
    assert status["vad"] == ("ready", None)  # energy_detector fake -> LazyLoad default
    assert status["mcp"] == ("ready", None)


def test_session_pipeline_status_omits_mcp_when_none():
    brain_sock, _robot_sock = socket_pair()
    graph = GraphClient(brain_sock)
    session = RobotCognitionSession(
        brain_sock,
        Peer(id="milo-1", name="milo"),
        vad=VadSegmenter(is_speech=energy_detector, min_silence_ms=60),
        asr=FakeAsr(),
        vision=FakeVision(),
        tts=FakeTts(),
        agent=CognitionAgent(FakeLlm(), graph, None),
        graph=graph,
        mcp=None,
    )
    assert "mcp" not in session.pipeline_status()


def test_factory_pipeline_status_before_any_session(tmp_path, monkeypatch):
    import milo_brain.pipelines.asr as asr_mod
    import milo_brain.pipelines.tts as tts_mod
    import milo_brain.pipelines.vision as vision_mod
    from milo_brain.config import BrainConfig
    from milo_brain.llm.token_rate import TokenRateTracker
    from milo_brain.session import CognitionSessionFactory

    class FakePipeline:
        def __init__(self, *a, **kw):
            self.status = "not_loaded"
            self.error = None

    monkeypatch.setattr(asr_mod, "WhisperAsr", FakePipeline)
    monkeypatch.setattr(tts_mod, "PiperTts", FakePipeline)
    monkeypatch.setattr(vision_mod, "FaceVision", FakePipeline)

    cfg = BrainConfig(brain_id="b", name="n", tier="small", data_dir=str(tmp_path))
    factory = CognitionSessionFactory(cfg, rate_tracker=TokenRateTracker())

    assert factory.current_session is None
    status = factory.pipeline_status()
    assert set(status) == {"asr", "tts", "vision"}
    assert status["asr"] == ("not_loaded", None)


def test_factory_current_session_is_tracked_during_handle_and_cleared_after(tmp_path, monkeypatch):
    import milo_brain.pipelines.asr as asr_mod
    import milo_brain.pipelines.tts as tts_mod
    import milo_brain.pipelines.vision as vision_mod
    from milo_brain.config import BrainConfig
    from milo_brain.llm.token_rate import TokenRateTracker
    from milo_brain.session import CognitionSessionFactory

    class FakePipeline:
        def __init__(self, *a, **kw):
            self.status = "ready"
            self.error = None

    monkeypatch.setattr(asr_mod, "WhisperAsr", FakePipeline)
    monkeypatch.setattr(tts_mod, "PiperTts", FakePipeline)
    monkeypatch.setattr(vision_mod, "FaceVision", FakePipeline)

    cfg = BrainConfig(brain_id="b", name="n", tier="small", data_dir=str(tmp_path))
    factory = CognitionSessionFactory(cfg, rate_tracker=TokenRateTracker())

    brain_sock, _robot_sock = socket_pair()
    peer = Peer(id="milo-1", name="milo")  # no mcp_url -> handle() skips MCP entirely

    async def main():
        handle_task = asyncio.create_task(factory.handle(brain_sock, peer))
        await asyncio.sleep(0)  # let handle() construct the session and reach session.run()'s recv()
        assert factory.current_session is not None
        status = factory.pipeline_status()
        assert "vad" in status
        assert "mcp" not in status
        handle_task.cancel()
        try:
            await handle_task
        except asyncio.CancelledError:
            pass
        assert factory.current_session is None

    asyncio.run(main())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_cognition_session.py -k "pipeline_status or current_session" -v`
Expected: FAIL — `RobotCognitionSession`/`CognitionSessionFactory` have no `pipeline_status`/`current_session`.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/session.py`, add this method to `RobotCognitionSession`, right after `__init__` (before `async def run`):

```python
    def pipeline_status(self) -> dict[str, tuple[str, str | None]]:
        status: dict[str, tuple[str, str | None]] = {"vad": (self._vad.status, self._vad.error)}
        if self._mcp is not None:
            status["mcp"] = ("ready" if self._mcp.connected else "not_loaded", None)
        return status
```

In `CognitionSessionFactory.__init__`, add after the existing `self._llm = ...` line:

```python
        self.current_session: RobotCognitionSession | None = None
```

Add this method to `CognitionSessionFactory`, right after `__init__` (before `async def handle`):

```python
    def pipeline_status(self) -> dict[str, tuple[str, str | None]]:
        status: dict[str, tuple[str, str | None]] = {
            "asr": (self._asr.status, self._asr.error),
            "tts": (self._tts.status, self._tts.error),
            "vision": (self._vision.status, self._vision.error),
        }
        if self.current_session is not None:
            status.update(self.current_session.pipeline_status())
        return status
```

Replace `CognitionSessionFactory.handle()`'s body from `session = RobotCognitionSession(` through `await session.run()` (currently):

```python
            agent = CognitionAgent(self._llm, graph, mcp)
            session = RobotCognitionSession(
                sock,
                peer,
                vad=VadSegmenter(),
                asr=self._asr,
                vision=self._vision,
                tts=self._tts,
                agent=agent,
                graph=graph,
                mcp=mcp,
                face_match_threshold=self._cfg.face_match_threshold,
            )
            await session.run()
```

with:

```python
            agent = CognitionAgent(self._llm, graph, mcp)
            session = RobotCognitionSession(
                sock,
                peer,
                vad=VadSegmenter(),
                asr=self._asr,
                vision=self._vision,
                tts=self._tts,
                agent=agent,
                graph=graph,
                mcp=mcp,
                face_match_threshold=self._cfg.face_match_threshold,
            )
            self.current_session = session
            try:
                await session.run()
            finally:
                self.current_session = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_cognition_session.py -v`
Expected: all pass, including `test_factory_handle_closes_mcp_client_when_connect_fails` (unaffected -- it never reaches session construction) and `test_factory_wires_rate_tracker_into_the_ollama_client`.

- [ ] **Step 5: Run the full suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing

```bash
git add brain/milo_brain/session.py brain/tests/test_cognition_session.py
git commit -m "feat(brain): add pipeline_status() to the cognition session and factory"
```

---

### Task 9: Thread the factory through `__main__.py`

**Files:**
- Modify: `brain/milo_brain/__main__.py`

**Interfaces:**
- Consumes: `CognitionSessionFactory.pipeline_status()` (Task 8) only indirectly -- this task just stops discarding the instance.
- Produces: `_build_handler(cfg, rate_tracker) -> tuple[CognitionSessionFactory | None, RobotHandler]` (was `-> RobotHandler`). `main()` passes the factory into `MiloBrainApp` (Task 10 adds the parameter it lands in).

No existing test covers `__main__.py`; this task is verified by the full suite still passing (no regression) plus a manual run in Task 11's manual verification step.

- [ ] **Step 1: Write the implementation**

Replace `_build_handler` in `brain/milo_brain/__main__.py` (currently):

```python
def _build_handler(cfg: BrainConfig, rate_tracker: TokenRateTracker) -> RobotHandler:
    try:  # full cognition pipeline; falls back to the debug handler without it
        from .session import CognitionSessionFactory

        return CognitionSessionFactory(cfg, rate_tracker=rate_tracker).handle
    except ImportError:
        from .net.connector import default_handler

        return default_handler
```

with:

```python
def _build_handler(cfg: BrainConfig, rate_tracker: TokenRateTracker):
    """Returns (factory_or_none, handler). factory is None in the
    ImportError fallback (full pipeline deps not installed) -- the
    dashboard's pipeline-status panel is omitted in that case."""
    try:  # full cognition pipeline; falls back to the debug handler without it
        from .session import CognitionSessionFactory

        factory = CognitionSessionFactory(cfg, rate_tracker=rate_tracker)
        return factory, factory.handle
    except ImportError:
        from .net.connector import default_handler

        return None, default_handler
```

Replace `main()`'s body from `session_handler = _build_handler(cfg, rate_tracker)` through the end of the function (currently):

```python
    session_handler = _build_handler(cfg, rate_tracker)

    connector = RobotConnectorManager(cfg, request_pin=_headless_request_pin, session_handler=session_handler)

    if args.headless:
        asyncio.run(connector.run_forever())
        return

    from .tui.app import MiloBrainApp

    MiloBrainApp(connector, cfg, rate_tracker, log_buffer).run()
```

with:

```python
    factory, session_handler = _build_handler(cfg, rate_tracker)

    connector = RobotConnectorManager(cfg, request_pin=_headless_request_pin, session_handler=session_handler)

    if args.headless:
        asyncio.run(connector.run_forever())
        return

    from .tui.app import MiloBrainApp

    MiloBrainApp(connector, cfg, rate_tracker, log_buffer, factory).run()
```

- [ ] **Step 2: Run the full suite**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing (this task has no dedicated new test; Task 10 adds the `MiloBrainApp` parameter this now passes into).

Note: this will fail until Task 10 adds the `factory` parameter to `MiloBrainApp.__init__` -- if running tasks out of order, do Task 10 first or accept this task is only fully verified once Task 10 lands.

- [ ] **Step 3: Commit**

```bash
git add brain/milo_brain/__main__.py
git commit -m "feat(brain): stop discarding the CognitionSessionFactory instance"
```

---

### Task 10: `MiloBrainApp` accepts and forwards the factory

**Files:**
- Modify: `brain/milo_brain/tui/app.py`
- Test: `brain/tests/test_tui_app.py`

**Interfaces:**
- Consumes: the `factory` from Task 9's `_build_handler`/`main()` (typed loosely -- `MiloBrainApp` never calls anything on it besides what `DashboardScreen.refresh_from` needs in Task 11).
- Produces: `MiloBrainApp.factory` attribute (`None` by default). `MiloBrainApp.__init__(self, connector, cfg, rate_tracker, log_buffer=None, factory=None)`.

- [ ] **Step 1: Write the failing tests**

Add to the end of `brain/tests/test_tui_app.py`:

```python
def test_factory_defaults_to_none_when_not_provided():
    app, _connector = make_app()
    assert app.factory is None


def test_provided_factory_is_used_as_is():
    connector = FakeConnector()
    cfg = BrainConfig(brain_id="b", name="n", tier="small")
    factory = object()
    app = MiloBrainApp(connector, cfg, TokenRateTracker(), factory=factory)
    assert app.factory is factory
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_app.py -k factory -v`
Expected: FAIL — `MiloBrainApp` has no `factory` attribute / no `factory` keyword argument.

- [ ] **Step 3: Write the implementation**

Replace `MiloBrainApp.__init__` in `brain/milo_brain/tui/app.py` (currently):

```python
    def __init__(
        self,
        connector: RobotConnectorManager,
        cfg: BrainConfig,
        rate_tracker: TokenRateTracker,
        log_buffer: RingBufferLogHandler | None = None,
    ):
        super().__init__()
        self.connector = connector
        self.cfg = cfg
        self.rate_tracker = rate_tracker
        self.log_buffer = log_buffer or RingBufferLogHandler()
        # Same pattern the old tray UI used (server._request_pin = ...),
        # just pointed at a modal screen instead of a QInputDialog.
        self.connector._request_pin = self.request_pin_from_user
```

with:

```python
    def __init__(
        self,
        connector: RobotConnectorManager,
        cfg: BrainConfig,
        rate_tracker: TokenRateTracker,
        log_buffer: RingBufferLogHandler | None = None,
        factory=None,
    ):
        super().__init__()
        self.connector = connector
        self.cfg = cfg
        self.rate_tracker = rate_tracker
        self.log_buffer = log_buffer or RingBufferLogHandler()
        self.factory = factory
        # Same pattern the old tray UI used (server._request_pin = ...),
        # just pointed at a modal screen instead of a QInputDialog.
        self.connector._request_pin = self.request_pin_from_user
```

Replace `_refresh_dashboard` (currently):

```python
    def _refresh_dashboard(self) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.refresh_from(self.connector, self.cfg, self.rate_tracker)
```

with:

```python
    def _refresh_dashboard(self) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.refresh_from(self.connector, self.cfg, self.rate_tracker, self.factory)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_app.py -v`
Expected: all pass. (`_refresh_dashboard`'s new 4th argument isn't exercised by these tests yet -- `DashboardScreen.refresh_from` still only takes 3 params until Task 11, so this file alone will fail its *own* suite until Task 11 lands. Run Task 11 before considering this task's full-suite check green, or run both together.)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/tui/app.py brain/tests/test_tui_app.py
git commit -m "feat(brain): thread the CognitionSessionFactory into MiloBrainApp"
```

---

### Task 11: Dashboard renders connection stage + pipeline health

**Files:**
- Modify: `brain/milo_brain/tui/dashboard.py`
- Test: `brain/tests/test_tui_dashboard.py`

**Interfaces:**
- Consumes: `connector.link_state`/`.link_target`/`.last_error`/`.retry_at`/`.consecutive_drops` (Task 7), `factory.pipeline_status()` (Task 8), `factory` (possibly `None`, Task 10).
- Produces: `ConnectionPanel.render_connection(robot_name, paired_count, last_connected, link_state, link_target, last_error, retry_in, attempt)` (was `(robot_name, paired_count, last_connected)`). New `PipelinesPanel.render_pipelines(status: dict[str, tuple[str, str | None]])`. `DashboardScreen.refresh_from(self, connector, cfg, rate_tracker, factory=None)` (was 3 params).

- [ ] **Step 1: Update existing dashboard tests for the new connector fields, and write new ones**

Replace `_FakeConnector` in `brain/tests/test_tui_dashboard.py` (currently):

```python
class _FakeConnector:
    def __init__(self, connected_robot=None, paired=(), last_connected=None):
        self.connected_robot = connected_robot
        self._paired = list(paired)
        self.last_connected = last_connected

    def paired_ids(self):
        return self._paired
```

with:

```python
class _FakeConnector:
    def __init__(
        self, connected_robot=None, paired=(), last_connected=None,
        link_state="idle", link_target=None, last_error=None,
        retry_at=None, consecutive_drops=0,
    ):
        self.connected_robot = connected_robot
        self._paired = list(paired)
        self.last_connected = last_connected
        self.link_state = link_state
        self.link_target = link_target
        self.last_error = last_error
        self.retry_at = retry_at
        self.consecutive_drops = consecutive_drops

    def paired_ids(self):
        return self._paired
```

In `test_refresh_from_renders_all_three_panels`, change the connector construction from:
```python
        connector = _FakeConnector(connected_robot=_FakePeer("milo-1"), paired=["milo-1"])
```
to:
```python
        connector = _FakeConnector(
            connected_robot=_FakePeer("milo-1"), paired=["milo-1"], link_state="connected",
        )
```

In `test_refresh_from_omits_reconnect_hint_once_actually_connected`, change:
```python
        connector = _FakeConnector(
            connected_robot=_FakePeer("milo-1"), paired=["milo-1"], last_connected=("10.0.0.9", 8765),
        )
```
to:
```python
        connector = _FakeConnector(
            connected_robot=_FakePeer("milo-1"), paired=["milo-1"], last_connected=("10.0.0.9", 8765),
            link_state="connected",
        )
```

`test_refresh_from_shows_no_robot_connected` and `test_refresh_from_hints_reconnect_when_a_previous_target_is_known` need no change -- their connectors' default `link_state="idle"` already matches what they're testing.

Add to the end of `brain/tests/test_tui_dashboard.py`:

```python
def test_refresh_from_shows_connecting_stage():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(link_state="connecting", link_target=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "connecting to 10.0.0.9:8765" in connection

    asyncio.run(scenario())


def test_refresh_from_shows_handshaking_stage():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(link_state="handshaking", link_target=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "handshaking" in connection
            assert "10.0.0.9:8765" in connection

    asyncio.run(scenario())


def test_refresh_from_shows_retrying_stage_with_countdown_attempt_and_error(monkeypatch):
    import milo_brain.tui.dashboard as dashboard_mod

    # A plain @contextlib.contextmanager (sync __enter__/__exit__) can't be
    # combined with `app.run_test()` in one `async with a, b:` statement --
    # that requires every item to be an async context manager. monkeypatch
    # (a normal pytest fixture, patch applied/reverted outside the async
    # block) sidesteps that entirely.
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: 100.0)

    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(
            link_state="retrying", retry_at=104.0, consecutive_drops=3,
            last_error="OSError: [Errno 11001] getaddrinfo failed",
        )
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "retrying in 4s" in connection
            assert "attempt 3" in connection
            assert "getaddrinfo failed" in connection

    asyncio.run(scenario())


def test_refresh_from_renders_pipelines_panel_when_factory_provided():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("ready", None),
                    "tts": ("not_loaded", None),
                    "vision": ("error", "no GPU found"),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            pipelines = str(screen.query_one(PipelinesPanel).content)
            assert "ASR: ready" in pipelines
            assert "TTS: not_loaded" in pipelines
            assert "VISION: error" in pipelines
            assert "no GPU found" in pipelines

    asyncio.run(scenario())


def test_refresh_from_omits_pipelines_when_factory_is_none():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            pipelines = str(screen.query_one(PipelinesPanel).content)
            assert "unavailable" in pipelines

    asyncio.run(scenario())
```

Update the import line at the top of the file from:
```python
from milo_brain.tui.dashboard import ConnectionPanel, DashboardScreen, IdentityPanel, ModelPanel
```
to:
```python
from milo_brain.tui.dashboard import (
    ConnectionPanel,
    DashboardScreen,
    IdentityPanel,
    ModelPanel,
    PipelinesPanel,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_dashboard.py -v`
Expected: FAIL — `PipelinesPanel` doesn't exist yet, `render_connection`/`refresh_from` don't accept the new parameters.

- [ ] **Step 3: Write the implementation**

Replace the full contents of `brain/milo_brain/tui/dashboard.py` with:

```python
"""Main dashboard screen: identity, connection, model, and pipeline panels."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


class IdentityPanel(Static):
    def render_identity(self, name: str, brain_id: str, tier: str, gpu: str) -> None:
        self.update(
            f"[b]Identity[/b]\n"
            f"Name: {name}\n"
            f"ID: {brain_id}\n"
            f"Tier: {tier}\n"
            f"GPU: {gpu or 'cpu'}"
        )


class ConnectionPanel(Static):
    def render_connection(
        self,
        robot_name: str | None,
        paired_count: int,
        last_connected: tuple[str, int] | None,
        link_state: str,
        link_target: tuple[str, int] | None,
        last_error: str | None,
        retry_in: float | None,
        attempt: int,
    ) -> None:
        lines = ["[b]Connection[/b]"]
        if link_state == "connected" and robot_name:
            lines.append(f"Robot: connected: {robot_name}")
        elif link_state == "connecting" and link_target:
            lines.append(f"Robot: connecting to {link_target[0]}:{link_target[1]}…")
        elif link_state == "handshaking" and link_target:
            lines.append(f"Robot: handshaking with {link_target[0]}:{link_target[1]}…")
        elif link_state == "retrying":
            countdown = f"{max(0, round(retry_in))}s" if retry_in is not None else "?"
            lines.append(f"Robot: retrying in {countdown} (attempt {attempt})")
            if last_error:
                lines.append(f"  last error: {last_error}")
        else:
            lines.append("Robot: no robot connected")
        lines.append(f"Paired robots: {paired_count}")
        if link_state != "connected" and last_connected is not None:
            host, port = last_connected
            lines.append(f"Last seen: {host}:{port}  [dim](r to reconnect)[/dim]")
        lines.append("[dim](c to connect a robot)[/dim]")
        self.update("\n".join(lines))


class ModelPanel(Static):
    def render_model(
        self, llm_model: str, whisper_model: str, piper_voice: str,
        tokens_per_sec_in: float, tokens_per_sec_out: float,
    ) -> None:
        self.update(
            f"[b]Model[/b]\n"
            f"LLM: {llm_model}\n"
            f"Whisper: {whisper_model}\n"
            f"Piper: {piper_voice}\n"
            f"Tokens/s  in: {tokens_per_sec_in:.1f} ^   out: {tokens_per_sec_out:.1f} v\n"
            f"[dim](m to change model)[/dim]"
        )


_PIPELINE_ORDER = ("asr", "tts", "vision", "vad", "mcp")


class PipelinesPanel(Static):
    def render_pipelines(self, status: dict[str, tuple[str, str | None]]) -> None:
        lines = ["[b]Pipelines[/b]"]
        if not status:
            lines.append("(unavailable)")
        else:
            for name in _PIPELINE_ORDER:
                if name not in status:
                    continue
                state, error = status[name]
                label = name.upper()
                if state == "error" and error:
                    lines.append(f"{label}: error — {error}")
                else:
                    lines.append(f"{label}: {state}")
        self.update("\n".join(lines))


class DashboardScreen(Screen):
    """The default screen: read-only panels, refreshed by MiloBrainApp's
    periodic timer calling refresh_from() -- not reactive watchers, matching
    milo-dashboard's existing TopBar.update_bar() convention."""

    CSS = """
    DashboardScreen Static {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    #credit {
        dock: bottom;
        height: 1;
        content-align: right middle;
        color: $text-muted;
        padding: 0 1;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal():
                yield IdentityPanel(id="identity-panel")
                yield ConnectionPanel(id="connection-panel")
            yield ModelPanel(id="model-panel")
            yield PipelinesPanel(id="pipelines-panel")
        yield Static("by DAMA", id="credit")
        yield Footer()

    def refresh_from(self, connector, cfg, rate_tracker, factory=None) -> None:
        robot = connector.connected_robot
        self.query_one(IdentityPanel).render_identity(cfg.name, cfg.brain_id, cfg.tier, cfg.gpu)
        retry_in = None
        if connector.retry_at is not None:
            retry_in = connector.retry_at - time.monotonic()
        self.query_one(ConnectionPanel).render_connection(
            robot.name if robot else None,
            len(connector.paired_ids()),
            connector.last_connected,
            connector.link_state,
            connector.link_target,
            connector.last_error,
            retry_in,
            connector.consecutive_drops,
        )
        self.query_one(ModelPanel).render_model(
            cfg.llm_model, cfg.whisper_model, cfg.piper_voice,
            rate_tracker.tokens_per_sec_in, rate_tracker.tokens_per_sec_out,
        )
        self.query_one(PipelinesPanel).render_pipelines(
            factory.pipeline_status() if factory is not None else {}
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_dashboard.py -v`
Expected: all pass.

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_app.py -v`
Expected: all pass now too (Task 10's 4-arg `refresh_from` call is satisfied).

- [ ] **Step 5: Run the full suite**

Run: `../.venv/Scripts/python.exe -m pytest`
Expected: all passing, no regressions anywhere in the repo.

- [ ] **Step 6: Manual verification**

From `brain/`, run the TUI for real and confirm the new panels render sensibly (this is a TUI — the test suite checks the rendered text, not the visual layout):

```bash
../.venv/Scripts/python.exe -m milo_brain
```

Confirm: the Connection panel shows a stage (not just connected/disconnected), and a Pipelines panel appears below the Model panel. Press `q` to quit.

- [ ] **Step 7: Commit**

```bash
git add brain/milo_brain/tui/dashboard.py brain/tests/test_tui_dashboard.py
git commit -m "feat(brain): show live connection stage and pipeline health on the dashboard"
```
