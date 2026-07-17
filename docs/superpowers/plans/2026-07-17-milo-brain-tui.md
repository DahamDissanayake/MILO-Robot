# Milo Brain TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `brain/`'s PyQt6 tray with a Textual TUI (dashboard, pairing-PIN modal, live-model picker, live LLM token throughput), branded MILO / by DAMA, running on the same asyncio loop as the server.

**Architecture:** `MiloBrainApp` (Textual `App`) owns the existing `BrainServer` and starts `serve_forever()` as a background worker in `on_mount()` -- same event loop as the UI, no separate thread. `Advertiser.start/stop/update` keep using `asyncio.to_thread(...)` (already fixed) since zeroconf's sync API still needs a thread-hop regardless of tray vs. TUI. Pairing-PIN requests become a direct `await self.push_screen_wait(PairingPinScreen(...))` from within that same worker's call chain -- verified empirically that this requires worker context (`NoActiveWorker` otherwise).

**Tech Stack:** Python 3.11+, Textual >=0.60 (verified against 8.2.8), httpx (already a dependency), plain `pytest` -- this package has no `pytest-asyncio`; every existing async test wraps `asyncio.run(...)` inside a plain `def test_...():`, and this plan follows that same convention throughout.

## Global Constraints

- Cross-platform: Linux and Windows, no platform-specific code in the TUI layer (Textual handles rendering differences).
- No `pytest-asyncio` -- async test bodies run via `asyncio.run(scenario())` inside sync `def test_...():` functions, matching `brain/tests/test_server_integration.py` and `brain/tests/test_agent.py`.
- Any call into `Advertiser` (`.start`, `.stop`, `.update`) from a coroutine already running on an event-loop thread MUST go through `asyncio.to_thread(...)` -- this is the exact deadlock class fixed earlier today (`zeroconf._exceptions.EventLoopBlocked`); the TUI must not reintroduce it.
- Injectable dependencies for testability (fakes over mocks-of-internals), matching this codebase's existing `FakeLlm`/`FakeMcp`/`NullAdvertiser` convention.
- Commit after each task.

---

### Task 1: Move `textual` to base dependencies, drop PyQt6

**Files:**
- Modify: `brain/pyproject.toml`

**Interfaces:**
- Produces: `textual` importable from a base (non-`[full]`) install of `brain/`.

- [ ] **Step 1: Edit the dependency lists**

In `brain/pyproject.toml`, change:

```toml
dependencies = [
    "milo-common",
    "numpy>=1.26",
    "websockets>=12",
    "zeroconf>=0.130",
    "pyyaml>=6",
    "httpx>=0.27",
    "mcp>=1.9",
]

[project.optional-dependencies]
# The full model stack -- needs a GPU machine.
full = [
    "faster-whisper>=1.0",
    "insightface>=0.7",
    "onnxruntime-gpu>=1.17; platform_system != 'Darwin'",
    "piper-tts>=1.2",
    "PyQt6>=6.6",
    "torch>=2.2",          # Silero VAD
    "opencv-python>=4.9",
]
```

to:

```toml
dependencies = [
    "milo-common",
    "numpy>=1.26",
    "websockets>=12",
    "zeroconf>=0.130",
    "pyyaml>=6",
    "httpx>=0.27",
    "mcp>=1.9",
    "textual>=0.60",
]

[project.optional-dependencies]
# The full model stack -- needs a GPU machine.
full = [
    "faster-whisper>=1.0",
    "insightface>=0.7",
    "onnxruntime-gpu>=1.17; platform_system != 'Darwin'",
    "piper-tts>=1.2",
    "torch>=2.2",          # Silero VAD
    "opencv-python>=4.9",
]
```

- [ ] **Step 2: Reinstall and verify**

Run: `pip install -e ./brain`
Expected: succeeds, `python -c "import textual; import PyQt6"` -- the `textual` import succeeds, the `PyQt6` import fails with `ModuleNotFoundError` (confirms it's no longer pulled in).

- [ ] **Step 3: Commit**

```bash
git add brain/pyproject.toml
git commit -m "build(brain): move textual to base deps, drop PyQt6"
```

---

### Task 2: TokenRateTracker

**Files:**
- Create: `brain/milo_brain/llm/token_rate.py`
- Test: `brain/tests/test_token_rate.py`

**Interfaces:**
- Produces: `TokenRateTracker(clock=time.monotonic)` with `.record_output_token()`, `.record_prompt_eval(token_count: int, duration_ns: int)`, and properties `.tokens_per_sec_out: float`, `.tokens_per_sec_in: float`. Consumed by Task 3 (`OllamaClient`) and Task 7/8 (dashboard refresh).

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_token_rate.py
from milo_brain.llm.token_rate import TokenRateTracker


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_tokens_per_sec_out_counts_tokens_within_the_window():
    clock = FakeClock()
    tracker = TokenRateTracker(clock=clock)
    for _ in range(4):
        tracker.record_output_token()
        clock.advance(0.1)
    assert tracker.tokens_per_sec_out == 2.0  # 4 tokens / 2.0s window


def test_tokens_per_sec_out_drops_tokens_older_than_the_window():
    clock = FakeClock()
    tracker = TokenRateTracker(clock=clock)
    tracker.record_output_token()
    clock.advance(3.0)  # older than WINDOW_S (2.0s)
    tracker.record_output_token()
    assert tracker.tokens_per_sec_out == 1 / TokenRateTracker.WINDOW_S


def test_tokens_per_sec_out_is_zero_with_no_tokens_recorded():
    tracker = TokenRateTracker(clock=FakeClock())
    assert tracker.tokens_per_sec_out == 0.0


def test_tokens_per_sec_in_reflects_the_last_prompt_eval():
    tracker = TokenRateTracker(clock=FakeClock())
    tracker.record_prompt_eval(token_count=150, duration_ns=300_000_000)  # 0.3s
    assert tracker.tokens_per_sec_in == 500.0
    tracker.record_prompt_eval(token_count=10, duration_ns=1_000_000_000)  # 1s
    assert tracker.tokens_per_sec_in == 10.0


def test_tokens_per_sec_in_is_zero_before_any_exchange():
    tracker = TokenRateTracker(clock=FakeClock())
    assert tracker.tokens_per_sec_in == 0.0


def test_record_prompt_eval_handles_zero_duration_without_dividing_by_zero():
    tracker = TokenRateTracker(clock=FakeClock())
    tracker.record_prompt_eval(token_count=5, duration_ns=0)
    assert tracker.tokens_per_sec_in == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest brain/tests/test_token_rate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.llm.token_rate'`

- [ ] **Step 3: Implement**

```python
# brain/milo_brain/llm/token_rate.py
"""Live tokens/sec tracking for the TUI's model panel."""

from __future__ import annotations

import time
from collections import deque


class TokenRateTracker:
    """Tracks LLM token throughput for the dashboard's up/down indicator.

    ``tokens_per_sec_out`` is a genuinely live rolling rate over WINDOW_S,
    fed by one ``record_output_token()`` call per streamed chunk -- Ollama's
    streaming granularity for /api/chat is one token per non-empty content
    chunk during generation. Ollama evaluates the prompt synchronously
    before the first token, so there's no per-chunk signal for the "up"
    side -- ``tokens_per_sec_in`` is just the most recently measured
    prompt-eval rate, updated once per exchange rather than continuously.
    """

    WINDOW_S = 2.0

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._output_times: deque[float] = deque()
        self._last_prompt_rate = 0.0

    def record_output_token(self) -> None:
        self._output_times.append(self._clock())
        self._trim()

    def record_prompt_eval(self, token_count: int, duration_ns: int) -> None:
        self._last_prompt_rate = token_count / (duration_ns / 1e9) if duration_ns else 0.0

    def _trim(self) -> None:
        cutoff = self._clock() - self.WINDOW_S
        while self._output_times and self._output_times[0] < cutoff:
            self._output_times.popleft()

    @property
    def tokens_per_sec_out(self) -> float:
        self._trim()
        return len(self._output_times) / self.WINDOW_S

    @property
    def tokens_per_sec_in(self) -> float:
        return self._last_prompt_rate
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest brain/tests/test_token_rate.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/llm/token_rate.py brain/tests/test_token_rate.py
git commit -m "feat(brain): add TokenRateTracker for live LLM token throughput"
```

---

### Task 3: Stream OllamaClient.chat(), feed TokenRateTracker

**Files:**
- Modify: `brain/milo_brain/llm/agent.py:56-82` (the `OllamaClient` class)
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Consumes: `TokenRateTracker` from Task 2 (`.record_output_token()`, `.record_prompt_eval(count, duration_ns)`).
- Produces: `OllamaClient(base_url=..., model=..., rate_tracker: TokenRateTracker | None = None)`. `.chat(...)` return shape unchanged (`{"role": ..., "content": ...}`, plus `"tool_calls"` key only when non-empty -- exactly matching today's shape, verified against the existing exact-equality test).

- [ ] **Step 1: Write the failing/updated tests**

Replace the two existing streaming-unaware tests and add two new ones in `brain/tests/test_agent.py`. Add `import json as json_lib` near the top (alongside the existing `import httpx`), and replace `test_chat_without_tools_requests_json_format`, `_FakeResponse`, and `test_chat_with_tools_omits_json_format_and_forwards_tools` with:

```python
class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


def test_chat_without_tools_requests_json_format(monkeypatch):
    captured = {}

    def fake_stream(self, method, url, json):
        captured.update(json)
        return _FakeStreamCtx([
            json_lib.dumps({"message": {"role": "assistant", "content": "hi"}, "done": False}),
            json_lib.dumps({
                "message": {"role": "assistant", "content": ""}, "done": True,
                "prompt_eval_count": 5, "prompt_eval_duration": 100_000_000,
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert captured["format"] == "json"
    assert "tools" not in captured
    assert message == {"role": "assistant", "content": "hi"}


def test_chat_with_tools_omits_json_format_and_forwards_tools(monkeypatch):
    captured = {}

    def fake_stream(self, method, url, json):
        captured.update(json)
        return _FakeStreamCtx([
            json_lib.dumps({
                "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "walk", "arguments": {"vx": 0.1}}}
                ]},
                "done": False,
            }),
            json_lib.dumps({
                "message": {"role": "assistant", "content": ""}, "done": True,
                "prompt_eval_count": 8, "prompt_eval_duration": 200_000_000,
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()
    tools = [{"type": "function", "function": {"name": "walk", "description": "", "parameters": {}}}]
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "walk forward"}], tools=tools)
    assert "format" not in captured
    assert captured["tools"] == tools
    assert message["tool_calls"][0]["function"]["name"] == "walk"


def test_chat_feeds_token_rate_tracker_from_streamed_chunks(monkeypatch):
    def fake_stream(self, method, url, json):
        return _FakeStreamCtx([
            json_lib.dumps({"message": {"role": "assistant", "content": "Hel"}, "done": False}),
            json_lib.dumps({"message": {"role": "assistant", "content": "lo"}, "done": False}),
            json_lib.dumps({
                "message": {"role": "assistant", "content": ""}, "done": True,
                "prompt_eval_count": 100, "prompt_eval_duration": 200_000_000,  # -> 500 tok/s
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    tracker = TokenRateTracker()
    client = OllamaClient(rate_tracker=tracker)
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert message["content"] == "Hello"
    assert tracker.tokens_per_sec_out > 0
    assert tracker.tokens_per_sec_in == 500.0


def test_chat_without_a_rate_tracker_still_works(monkeypatch):
    def fake_stream(self, method, url, json):
        return _FakeStreamCtx([
            json_lib.dumps({
                "message": {"role": "assistant", "content": "ok"}, "done": True,
                "prompt_eval_count": 1, "prompt_eval_duration": 1_000_000,
            }),
        ])

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    client = OllamaClient()  # no rate_tracker
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert message == {"role": "assistant", "content": "ok"}
```

Add the import at the top of the file:

```python
import asyncio
import json as json_lib

import httpx

from milo_brain.llm.agent import (
    SYSTEM_PROMPT,
    VALID_FACES,
    CognitionAgent,
    OllamaClient,
    extract_name,
    parse_llm_json,
    sanitize,
)
from milo_brain.llm.token_rate import TokenRateTracker
```

(Keep `test_system_prompt_face_list_is_derived_from_valid_faces`, `asyncio_run_chat`, `FakeLlm`, `FakeMcp`, and everything below unchanged.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest brain/tests/test_agent.py -v`
Expected: the two updated tests fail (`AttributeError` or similar -- `OllamaClient.chat` still calls `.post`, not `.stream`, so `monkeypatch.setattr(httpx.AsyncClient, "stream", ...)` is never hit and the real network call fails/hangs); the two new tests fail similarly.

- [ ] **Step 3: Implement streaming in OllamaClient**

In `brain/milo_brain/llm/agent.py`, add the import near the top (after the existing `import re`):

```python
from .token_rate import TokenRateTracker
```

Replace the `OllamaClient` class body (lines 56-82 today) with:

```python
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "llama3.2:3b",
        rate_tracker: TokenRateTracker | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._rate_tracker = rate_tracker

    async def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Returns the raw assistant message dict (``content`` and, if the
        model requested one or more tool calls, ``tool_calls``). Ollama's
        strict JSON-format mode and its tool-calling mode aren't used
        together, so ``format: "json"`` is only requested when no tools are
        offered -- the final tool-calling turn's JSON-ness instead relies on
        SYSTEM_PROMPT's instructions plus parse_llm_json's existing
        tolerance for stray/non-strict text.

        Streams the response (rather than one blocking POST) so a
        TokenRateTracker, if given, reports a live tokens/sec rate to the
        TUI while the model is still generating, not just a number after
        the fact."""
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        else:
            payload["format"] = "json"

        role = "assistant"
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    message = chunk.get("message") or {}
                    role = message.get("role", role)
                    piece = message.get("content", "")
                    if piece:
                        content_parts.append(piece)
                        if self._rate_tracker is not None:
                            self._rate_tracker.record_output_token()
                    if message.get("tool_calls"):
                        tool_calls = message["tool_calls"]
                    if chunk.get("done") and self._rate_tracker is not None:
                        count = chunk.get("prompt_eval_count")
                        duration = chunk.get("prompt_eval_duration")
                        if count is not None and duration is not None:
                            self._rate_tracker.record_prompt_eval(count, duration)

        result: dict = {"role": role, "content": "".join(content_parts)}
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest brain/tests/test_agent.py -v`
Expected: PASS (all tests, including the pre-existing `CognitionAgent`-level ones below, which use `FakeLlm` and are unaffected by this change)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat(brain): stream OllamaClient.chat, feed TokenRateTracker live"
```

---

### Task 4: Wire rate_tracker through CognitionSessionFactory

**Files:**
- Modify: `brain/milo_brain/session.py:24, 168-181`
- Test: `brain/tests/test_cognition_session.py`

**Interfaces:**
- Consumes: `TokenRateTracker` (Task 2), `OllamaClient(..., rate_tracker=...)` (Task 3).
- Produces: `CognitionSessionFactory(cfg, rate_tracker: TokenRateTracker | None = None)`.

- [ ] **Step 1: Write the failing test**

Append to `brain/tests/test_cognition_session.py`:

```python
def test_factory_wires_rate_tracker_into_the_ollama_client(tmp_path, monkeypatch):
    import milo_brain.pipelines.asr as asr_mod
    import milo_brain.pipelines.tts as tts_mod
    import milo_brain.pipelines.vision as vision_mod
    from milo_brain.config import BrainConfig
    from milo_brain.llm.token_rate import TokenRateTracker
    from milo_brain.session import CognitionSessionFactory

    monkeypatch.setattr(asr_mod, "WhisperAsr", lambda *a, **kw: object())
    monkeypatch.setattr(vision_mod, "FaceVision", lambda *a, **kw: object())
    monkeypatch.setattr(tts_mod, "PiperTts", lambda *a, **kw: object())

    cfg = BrainConfig(brain_id="b", name="n", tier="small", data_dir=str(tmp_path))
    tracker = TokenRateTracker()
    factory = CognitionSessionFactory(cfg, rate_tracker=tracker)
    assert factory._llm._rate_tracker is tracker
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest brain/tests/test_cognition_session.py -k rate_tracker -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'rate_tracker'`

- [ ] **Step 3: Implement**

In `brain/milo_brain/session.py`, change line 24 from:

```python
from .llm.agent import AgentResult, CognitionAgent, OllamaClient
```

to:

```python
from .llm.agent import AgentResult, CognitionAgent, OllamaClient
from .llm.token_rate import TokenRateTracker
```

And change the `__init__` (lines 168-181) from:

```python
    def __init__(self, cfg: BrainConfig):
        from milo_common.auth import PairedStore

        from .llm.agent import OllamaClient
        from .pipelines.asr import WhisperAsr
        from .pipelines.tts import PiperTts
        from .pipelines.vision import FaceVision

        self._cfg = cfg
        self._store = PairedStore(cfg.paired_path)
        self._asr = WhisperAsr(cfg.whisper_model)
        self._vision = FaceVision(analysis_fps=cfg.vision_fps)
        self._tts = PiperTts(cfg.piper_voice)
        self._llm = OllamaClient(cfg.ollama_url, cfg.llm_model)
```

to:

```python
    def __init__(self, cfg: BrainConfig, rate_tracker: TokenRateTracker | None = None):
        from milo_common.auth import PairedStore

        from .llm.agent import OllamaClient
        from .pipelines.asr import WhisperAsr
        from .pipelines.tts import PiperTts
        from .pipelines.vision import FaceVision

        self._cfg = cfg
        self._store = PairedStore(cfg.paired_path)
        self._asr = WhisperAsr(cfg.whisper_model)
        self._vision = FaceVision(analysis_fps=cfg.vision_fps)
        self._tts = PiperTts(cfg.piper_voice)
        self._llm = OllamaClient(cfg.ollama_url, cfg.llm_model, rate_tracker=rate_tracker)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest brain/tests/test_cognition_session.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/session.py brain/tests/test_cognition_session.py
git commit -m "feat(brain): wire TokenRateTracker through CognitionSessionFactory"
```

---

### Task 5: PairingPinScreen

**Files:**
- Create: `brain/milo_brain/tui/__init__.py`
- Create: `brain/milo_brain/tui/pairing.py`
- Test: `brain/tests/test_tui_pairing.py`

**Interfaces:**
- Produces: `PairingPinScreen(robot_name: str)`, a `ModalScreen[str | None]` -- submitting the PIN input dismisses with its (stripped) value or `None` if blank; Escape dismisses with `None`. Consumed by Task 8 (`MiloBrainApp.request_pin_from_user`).

- [ ] **Step 1: Create the package init**

```python
# brain/milo_brain/tui/__init__.py
"""MiloBrainApp: the Textual TUI that replaces the PyQt6 tray."""
```

- [ ] **Step 2: Write the failing tests**

```python
# brain/tests/test_tui_pairing.py
"""PairingPinScreen submit/cancel behavior, driven headlessly via Textual's Pilot."""

from __future__ import annotations

import asyncio

from textual.app import App

from milo_brain.tui.pairing import PairingPinScreen


class _HostApp(App):
    def __init__(self):
        super().__init__()
        self.result: str | None = "not-set"

    async def run_pairing(self) -> None:
        self.result = await self.push_screen_wait(PairingPinScreen("milo-1"))


def test_submitting_the_pin_dismisses_with_its_value():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_pairing())
            await pilot.pause()
            await pilot.click("#pin-input")
            await pilot.press(*"123456")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "123456"


def test_escape_dismisses_with_none():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_pairing())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None


def test_submitting_blank_input_dismisses_with_none():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_pairing())
            await pilot.pause()
            await pilot.click("#pin-input")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest brain/tests/test_tui_pairing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.tui.pairing'`

- [ ] **Step 4: Implement**

```python
# brain/milo_brain/tui/pairing.py
"""Modal PIN entry when a robot requests pairing."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class PairingPinScreen(ModalScreen[str | None]):
    """Shown when a robot requests pairing. Submitting the input dismisses
    with the typed PIN (or None if blank); Escape dismisses with None
    (declines pairing)."""

    DEFAULT_CSS = """
    PairingPinScreen {
        align: center middle;
    }
    #pairing-box {
        width: 50;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, robot_name: str):
        super().__init__()
        self.robot_name = robot_name

    def compose(self) -> ComposeResult:
        with Vertical(id="pairing-box"):
            yield Static(f"Robot [b]{self.robot_name}[/b] wants to pair.")
            yield Static("Enter the 6-digit PIN shown on its face:")
            yield Input(placeholder="123456", id="pin-input", max_length=6)

    def on_mount(self) -> None:
        self.query_one("#pin-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest brain/tests/test_tui_pairing.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add brain/milo_brain/tui/ brain/tests/test_tui_pairing.py
git commit -m "feat(brain): add PairingPinScreen (TUI modal for pairing PIN entry)"
```

---

### Task 6: ModelPickerScreen

**Files:**
- Create: `brain/milo_brain/tui/model_picker.py`
- Test: `brain/tests/test_tui_model_picker.py`

**Interfaces:**
- Produces: `ModelPickerScreen(ollama_url: str, fetch_model_names=None)`, a `ModalScreen[str | None]` -- selecting a model dismisses with its name; Escape or an empty list's implicit no-op leaves the result `None`. Also produces `_fetch_model_names_via_http(ollama_url) -> list[str]` (GET `{ollama_url}/api/tags`, not a subprocess call to `ollama list`). Consumed by Task 8 (`MiloBrainApp.action_pick_model`).

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_tui_model_picker.py
"""ModelPickerScreen selection/cancel behavior, and the /api/tags fetch helper."""

from __future__ import annotations

import asyncio

import httpx
from textual.app import App

from milo_brain.tui.model_picker import ModelPickerScreen, _fetch_model_names_via_http


class _HostApp(App):
    def __init__(self, fetch_model_names):
        super().__init__()
        self.fetch_model_names = fetch_model_names
        self.result: str | None = "not-set"

    async def run_picker(self) -> None:
        self.result = await self.push_screen_wait(
            ModelPickerScreen("http://127.0.0.1:11434", fetch_model_names=self.fetch_model_names)
        )


def test_selecting_the_first_item_dismisses_with_its_name():
    async def fake_fetch(url):
        return ["llama3.2:3b", "llama3.1:8b"]

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            # ListView starts with nothing highlighted; one "down" highlights
            # the first item (verified empirically -- it does NOT skip to
            # the second item).
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "llama3.2:3b"


def test_selecting_the_second_item_dismisses_with_its_name():
    async def fake_fetch(url):
        return ["llama3.2:3b", "llama3.1:8b"]

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "llama3.1:8b"


def test_escape_dismisses_with_none():
    async def fake_fetch(url):
        return ["llama3.2:3b"]

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None


def test_empty_model_list_shows_a_message_instead_of_crashing():
    async def fake_fetch(url):
        return []

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            label = app.screen.query_one("Label")
            return str(label.renderable)

    assert "No models found" in asyncio.run(scenario())


def test_fetch_model_names_via_http_parses_the_tags_response(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "llama3.2:3b"}, {"name": "llama3.1:8b"}]}

    async def fake_get(self, url):
        return _FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    names = asyncio.run(_fetch_model_names_via_http("http://127.0.0.1:11434"))
    assert names == ["llama3.2:3b", "llama3.1:8b"]


def test_fetch_model_names_via_http_returns_empty_list_on_error(monkeypatch):
    async def fake_get(self, url):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    names = asyncio.run(_fetch_model_names_via_http("http://127.0.0.1:11434"))
    assert names == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest brain/tests/test_tui_model_picker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.tui.model_picker'`

- [ ] **Step 3: Implement**

```python
# brain/milo_brain/tui/model_picker.py
"""Modal model picker: lists installed Ollama models via GET /api/tags."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class ModelPickerScreen(ModalScreen[str | None]):
    """Lists models installed in Ollama (GET {ollama_url}/api/tags -- not a
    subprocess call to the `ollama` CLI, consistent with how OllamaClient
    already talks to Ollama over HTTP, and it works whether or not `ollama`
    itself is on PATH). Picking one dismisses with its name; Escape
    dismisses with None (no change)."""

    DEFAULT_CSS = """
    ModelPickerScreen {
        align: center middle;
    }
    #model-box {
        width: 60;
        height: auto;
        max-height: 20;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, ollama_url: str, fetch_model_names=None):
        super().__init__()
        self.ollama_url = ollama_url.rstrip("/")
        self._fetch_model_names = fetch_model_names or _fetch_model_names_via_http
        self._names: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="model-box"):
            yield Static("Select a model (installed in Ollama):")
            yield ListView(id="model-list")

    async def on_mount(self) -> None:
        list_view = self.query_one("#model-list", ListView)
        self._names = await self._fetch_model_names(self.ollama_url)
        if not self._names:
            await list_view.append(ListItem(Label("No models found -- is Ollama running?")))
            return
        for name in self._names:
            await list_view.append(ListItem(Label(name)))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not self._names:
            return
        self.dismiss(self._names[event.list_view.index])

    def action_cancel(self) -> None:
        self.dismiss(None)


async def _fetch_model_names_via_http(ollama_url: str) -> list[str]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError:
        return []
    return [m["name"] for m in data.get("models", [])]
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest brain/tests/test_tui_model_picker.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/tui/model_picker.py brain/tests/test_tui_model_picker.py
git commit -m "feat(brain): add ModelPickerScreen (live Ollama model list)"
```

---

### Task 7: DashboardScreen + Advertiser.advertised_ip

**Files:**
- Modify: `brain/milo_brain/server.py` (cache the advertised IP on `Advertiser`)
- Create: `brain/milo_brain/tui/dashboard.py`
- Test: `brain/tests/test_tui_dashboard.py`

**Interfaces:**
- Consumes: `BrainServer`/`Advertiser` (existing), `BrainConfig` (existing), `TokenRateTracker` (Task 2).
- Produces: `Advertiser.advertised_ip: str` (empty until `start()`/`update()` has run once). `DashboardScreen` with `.refresh_from(server, cfg, rate_tracker)`, updating four panels. Consumed by Task 8 (`MiloBrainApp`'s refresh timer).

- [ ] **Step 1: Cache the advertised IP on Advertiser**

In `brain/milo_brain/server.py`, change `Advertiser.__init__` from:

```python
    def __init__(self, cfg: BrainConfig):
        self._cfg = cfg
        self._zc = None
        self._info = None
        self.busy = False
        self.pairing = False
```

to:

```python
    def __init__(self, cfg: BrainConfig):
        self._cfg = cfg
        self._zc = None
        self._info = None
        self.busy = False
        self.pairing = False
        self.advertised_ip = ""
```

And in `_service_info()`, change:

```python
        host = _local_ip()
        return ServiceInfo(
```

to:

```python
        host = _local_ip()
        self.advertised_ip = host
        return ServiceInfo(
```

- [ ] **Step 2: Write the failing dashboard test**

```python
# brain/tests/test_tui_dashboard.py
"""DashboardScreen.refresh_from renders identity/connection/model/pairing panels."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.dashboard import (
    ConnectionPanel,
    DashboardScreen,
    IdentityPanel,
    ModelPanel,
    PairingPanel,
)


class _FakeAdvertiser:
    def __init__(self):
        self.pairing = False
        self.advertised_ip = "192.168.1.14"


class _FakePeer:
    def __init__(self, name):
        self.name = name


class _FakeServer:
    def __init__(self, connected_robot=None, pairing=False):
        self.advertiser = _FakeAdvertiser()
        self.advertiser.pairing = pairing
        self.connected_robot = connected_robot


class _HostApp(App):
    def compose(self) -> ComposeResult:
        yield DashboardScreen()


def test_refresh_from_renders_all_four_panels():
    async def scenario():
        cfg = BrainConfig(
            brain_id="brain-abc", name="my-laptop", tier="small", gpu="RTX 4050",
            port=8765, llm_model="llama3.2:3b", whisper_model="small", piper_voice="en_US-lessac-medium",
        )
        server = _FakeServer(connected_robot=_FakePeer("milo-1"), pairing=True)
        tracker = TokenRateTracker()
        tracker.record_prompt_eval(100, 200_000_000)  # 500 tok/s

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(server, cfg, tracker)

            identity = str(screen.query_one(IdentityPanel).renderable)
            connection = str(screen.query_one(ConnectionPanel).renderable)
            model = str(screen.query_one(ModelPanel).renderable)
            pairing = str(screen.query_one(PairingPanel).renderable)

            assert "my-laptop" in identity and "brain-abc" in identity and "RTX 4050" in identity
            assert "8765" in connection and "192.168.1.14" in connection and "milo-1" in connection
            assert "llama3.2:3b" in model and "500.0" in model
            assert "ON" in pairing

    asyncio.run(scenario())


def test_refresh_from_shows_no_robot_connected():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        server = _FakeServer(connected_robot=None, pairing=False)
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(server, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).renderable)
            pairing = str(screen.query_one(PairingPanel).renderable)
            assert "no robot connected" in connection
            assert "OFF" in pairing

    asyncio.run(scenario())
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest brain/tests/test_tui_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.tui.dashboard'`

- [ ] **Step 4: Implement**

```python
# brain/milo_brain/tui/dashboard.py
"""Main dashboard screen: identity, connection, model, and pairing panels."""

from __future__ import annotations

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
    def render_connection(self, port: int, advertised_ip: str, robot_name: str | None) -> None:
        status = f"connected: {robot_name}" if robot_name else "no robot connected"
        self.update(
            f"[b]Connection[/b]\n"
            f"Listening: :{port}\n"
            f"Advertised: {advertised_ip or 'not yet advertising'}\n"
            f"Robot: {status}"
        )


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


class PairingPanel(Static):
    def render_pairing(self, enabled: bool) -> None:
        state = "[b green]ON[/b green]" if enabled else "[b red]OFF[/b red]"
        self.update(f"[b]Pairing[/b]\nMode: {state}\n[dim](p to toggle)[/dim]")


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
            with Horizontal():
                yield ModelPanel(id="model-panel")
                yield PairingPanel(id="pairing-panel")
        yield Static("by DAMA", id="credit")
        yield Footer()

    def refresh_from(self, server, cfg, rate_tracker) -> None:
        robot = server.connected_robot
        self.query_one(IdentityPanel).render_identity(cfg.name, cfg.brain_id, cfg.tier, cfg.gpu)
        self.query_one(ConnectionPanel).render_connection(
            cfg.port, server.advertiser.advertised_ip, robot.name if robot else None
        )
        self.query_one(ModelPanel).render_model(
            cfg.llm_model, cfg.whisper_model, cfg.piper_voice,
            rate_tracker.tokens_per_sec_in, rate_tracker.tokens_per_sec_out,
        )
        self.query_one(PairingPanel).render_pairing(server.advertiser.pairing)
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest brain/tests/test_tui_dashboard.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Run the full brain suite to confirm the Advertiser change didn't break anything**

Run: `pytest brain/tests -v`
Expected: PASS (all tests, including `test_server_integration.py`'s `NullAdvertiser`-based tests -- `NullAdvertiser` doesn't define `advertised_ip`, but nothing in those tests reads it, so this is safe)

- [ ] **Step 7: Commit**

```bash
git add brain/milo_brain/server.py brain/milo_brain/tui/dashboard.py brain/tests/test_tui_dashboard.py
git commit -m "feat(brain): add DashboardScreen, cache Advertiser.advertised_ip"
```

---

### Task 8: MiloBrainApp

**Files:**
- Create: `brain/milo_brain/tui/app.py`
- Test: `brain/tests/test_tui_app.py`

**Interfaces:**
- Consumes: `BrainServer` (existing), `BrainConfig` (existing), `TokenRateTracker` (Task 2), `DashboardScreen` (Task 7), `PairingPinScreen` (Task 5), `ModelPickerScreen` (Task 6).
- Produces: `MiloBrainApp(server, cfg, rate_tracker)`. Consumed by Task 9 (`__main__.py`).

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_tui_app.py
"""MiloBrainApp wiring: server startup, dashboard push, pairing/model actions."""

from __future__ import annotations

import asyncio

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.app import MiloBrainApp
from milo_brain.tui.dashboard import DashboardScreen


class FakeAdvertiser:
    def __init__(self):
        self.pairing = False
        self.busy = False
        self.advertised_ip = "192.168.1.14"
        self.updates: list[dict] = []

    def start(self):
        pass

    def update(self, **kw):
        self.updates.append(kw)
        for key, value in kw.items():
            if value is not None:
                setattr(self, key, value)

    def stop(self):
        pass


class FakeServer:
    def __init__(self):
        self.advertiser = FakeAdvertiser()
        self.connected_robot = None
        self._request_pin = None
        self.served = asyncio.Event()

    async def serve_forever(self):
        self.served.set()
        await asyncio.Future()  # run until cancelled, like the real one


def make_app() -> tuple[MiloBrainApp, FakeServer]:
    server = FakeServer()
    cfg = BrainConfig(brain_id="b", name="n", tier="small")
    app = MiloBrainApp(server, cfg, TokenRateTracker())
    return app, server


def test_dashboard_is_pushed_and_server_starts_on_mount():
    async def scenario():
        app, server = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, DashboardScreen)
            await asyncio.wait_for(server.served.wait(), timeout=5)

    asyncio.run(scenario())


def test_request_pin_is_wired_to_the_app_on_construction():
    app, server = make_app()
    assert server._request_pin == app.request_pin_from_user


def test_toggle_pairing_action_flips_the_advertiser():
    async def scenario():
        app, server = make_app()
        async with app.run_test():
            assert server.advertiser.pairing is False
            await app.action_toggle_pairing()
            assert server.advertiser.pairing is True
            await app.action_toggle_pairing()
            assert server.advertiser.pairing is False

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest brain/tests/test_tui_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.tui.app'`

- [ ] **Step 3: Implement**

```python
# brain/milo_brain/tui/app.py
"""MiloBrainApp: the TUI's Textual App -- owns the BrainServer, runs it as a
background worker on the app's own event loop (no separate thread, unlike
the old tray UI), and wires pairing-PIN requests to a modal screen."""

from __future__ import annotations

import asyncio

from textual.app import App

from ..config import BrainConfig
from ..llm.token_rate import TokenRateTracker
from ..server import BrainServer
from .dashboard import DashboardScreen
from .model_picker import ModelPickerScreen
from .pairing import PairingPinScreen

REFRESH_INTERVAL_S = 1.0


class MiloBrainApp(App):
    TITLE = "MILO"
    SUB_TITLE = "Brain"
    BINDINGS = [
        ("p", "toggle_pairing", "Pairing"),
        ("m", "pick_model", "Model"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, server: BrainServer, cfg: BrainConfig, rate_tracker: TokenRateTracker):
        super().__init__()
        self.server = server
        self.cfg = cfg
        self.rate_tracker = rate_tracker
        # Same pattern the tray UI used (server._request_pin = ...), just
        # pointed at a modal screen instead of a QInputDialog.
        self.server._request_pin = self.request_pin_from_user

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())
        self.run_worker(self.server.serve_forever(), name="brain-server")
        self.set_interval(REFRESH_INTERVAL_S, self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.refresh_from(self.server, self.cfg, self.rate_tracker)

    def _dashboard(self) -> DashboardScreen | None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    async def request_pin_from_user(self, robot_name: str) -> str | None:
        return await self.push_screen_wait(PairingPinScreen(robot_name))

    async def action_toggle_pairing(self) -> None:
        # Advertiser.update() is zeroconf's synchronous API -- calling it
        # directly here (this coroutine's own loop thread, same as
        # BrainServer.serve_forever()) would deadlock exactly like the bug
        # fixed in Advertiser.start/stop. Same fix: hop to a worker thread.
        await asyncio.to_thread(
            self.server.advertiser.update, pairing=not self.server.advertiser.pairing
        )

    def action_pick_model(self) -> None:
        self.run_worker(self._pick_model())

    async def _pick_model(self) -> None:
        chosen = await self.push_screen_wait(ModelPickerScreen(self.cfg.ollama_url))
        if chosen:
            self.cfg.llm_model = chosen
            self.cfg.save()
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest brain/tests/test_tui_app.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/tui/app.py brain/tests/test_tui_app.py
git commit -m "feat(brain): add MiloBrainApp wiring server, dashboard, pairing, model picker"
```

---

### Task 9: Rewire __main__.py, delete the tray

**Files:**
- Modify: `brain/milo_brain/__main__.py`
- Delete: `brain/milo_brain/ui/tray.py` (and the now-empty `brain/milo_brain/ui/` directory)

**Interfaces:**
- Consumes: `MiloBrainApp` (Task 8), `TokenRateTracker` (Task 2), `CognitionSessionFactory` (Task 4).
- Produces: `main(argv)` -- default launches the TUI; `--headless` unchanged in behavior; `--pairing` unchanged.

This task has no dedicated automated test: `main()`'s branching was never under test before this change either (confirmed: no existing test imports `milo_brain.__main__`), and the two real code paths it wires together (`BrainServer.serve_forever()`, `MiloBrainApp`) are already covered by `test_server_integration.py` and `test_tui_app.py`. Verify manually instead (Step 3).

- [ ] **Step 1: Delete the tray**

Delete `brain/milo_brain/ui/tray.py`. If `brain/milo_brain/ui/__init__.py` exists and `ui/` is now empty, delete the whole `brain/milo_brain/ui/` directory.

- [ ] **Step 2: Rewrite __main__.py**

Replace the full contents of `brain/milo_brain/__main__.py` with:

```python
"""Entry point: ``python -m milo_brain`` (TUI) or ``--headless``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import BrainConfig
from .llm.token_rate import TokenRateTracker
from .server import BrainServer, RobotHandler


async def _headless_request_pin(robot_name: str) -> str | None:
    print(f"\nRobot '{robot_name}' wants to pair. Enter the PIN shown on its face.")
    return await asyncio.to_thread(input, "PIN: ")


def _build_handler(cfg: BrainConfig, rate_tracker: TokenRateTracker) -> RobotHandler:
    try:  # full cognition pipeline; falls back to the debug handler without it
        from .session import CognitionSessionFactory

        return CognitionSessionFactory(cfg, rate_tracker=rate_tracker).handle
    except ImportError:
        from .server import default_handler

        return default_handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="milo-brain")
    parser.add_argument("--headless", action="store_true", help="run without the TUI")
    parser.add_argument("--pairing", action="store_true", help="start with pairing mode on")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = BrainConfig.load()
    rate_tracker = TokenRateTracker()
    handler = _build_handler(cfg, rate_tracker)

    server = BrainServer(cfg, handler=handler, request_pin=_headless_request_pin)
    if args.pairing:
        server.advertiser.pairing = True

    if args.headless:
        asyncio.run(server.serve_forever())
        return

    from .tui.app import MiloBrainApp

    MiloBrainApp(server, cfg, rate_tracker).run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Manually verify both modes**

Run: `pip install -e ./brain` (picks up the deleted `ui/` package and the `textual` base dependency from Task 1)
Run: `python -m milo_brain --headless`
Expected: logs `milo-brain '<name>' (<tier> tier) listening on :<port>` and stays running (Ctrl+C to stop) -- same behavior as before this plan.
Run: `python -m milo_brain`
Expected: the TUI launches showing the MILO/Brain header, four dashboard panels, "by DAMA" in the bottom-right corner, and a footer showing the `p`/`m`/`q` keybindings. Press `q` to quit cleanly.

- [ ] **Step 4: Run the full brain suite**

Run: `pytest brain/tests -v`
Expected: PASS (all tests, across every task in this plan)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/__main__.py
git rm -r brain/milo_brain/ui
git commit -m "feat(brain): launch the TUI by default, remove the PyQt6 tray"
```

---

### Task 10: Update brain/README.md for the TUI

**Files:**
- Modify: `brain/README.md`

No test (documentation only). Self-verify by reading the finished section against Task 9's actual `--headless`/default behavior.

- [ ] **Step 1: Update the "Running it" section**

Replace:

```markdown
## Running it

```powershell
.venv\Scripts\Activate.ps1     # Windows, if not already active
# or: source .venv/bin/activate    (Linux)

python -m milo_brain           # tray UI (needs PyQt6 -- included in the [full] extra, or `pip install PyQt6` on its own)
python -m milo_brain --headless   # no tray, just logs -- for headless/server boxes
python -m milo_brain --pairing    # start with pairing mode already enabled (skips the tray toggle)
```

The tray UI works out of the box on native Windows and on Linux with a
desktop session -- use `--headless` on a server box or anywhere without a
GUI session running.
```

with:

```markdown
## Running it

```powershell
.venv\Scripts\Activate.ps1     # Windows, if not already active
# or: source .venv/bin/activate    (Linux)

python -m milo_brain           # TUI: dashboard, pairing, model picker
python -m milo_brain --headless   # no TUI, just logs -- for headless/server boxes
python -m milo_brain --pairing    # start with pairing mode already enabled
```

The TUI runs in any terminal on Windows or Linux -- no GUI session or system
tray required. Keybindings: `p` toggles pairing mode, `m` opens the model
picker (lists whatever's installed in Ollama), `q` quits. Use `--headless`
on a genuinely headless box (no terminal attached at all, e.g. run under a
service manager).
```

- [ ] **Step 2: Update the "Requirements"/tier table row for the tray**

Find the `| **Full** | ...` row in the install-tier table and remove the trailing `` `PyQt6` (tray UI) `` mention (the tray no longer exists; `textual` is now in the base/light install, not the full one).

- [ ] **Step 3: Update "Pairing with the robot"**

Replace:

```markdown
1. Make sure `milo-bridge` is running on the robot (see the
   [top-level README](../README.md) or
   [`docs/SOFTWARE-SETUP.md`](../docs/SOFTWARE-SETUP.md)).
2. Start the brain with `--pairing` (or enable pairing mode from the tray
   icon).
3. Milo's face shows a **6-digit PIN**.
4. Type it into the brain (tray dialog, or the `--headless` prompt in the
   terminal).
```

with:

```markdown
1. Make sure `milo-bridge` is running on the robot (see the
   [top-level README](../README.md) or
   [`docs/SOFTWARE-SETUP.md`](../docs/SOFTWARE-SETUP.md)).
2. Start the brain with `--pairing` (or press `p` in the TUI to enable
   pairing mode).
3. Milo's face shows a **6-digit PIN**.
4. Type it into the brain -- a modal appears in the TUI asking for it (or
   the `--headless` prompt in the terminal).
```

- [ ] **Step 4: Update "How it works internally"**

In the component list, replace:

```markdown
- **`ui/tray.py`** -- optional PyQt6 system tray (connection state, pairing
  toggle, PIN entry dialog). Falls back to headless automatically if PyQt6
  isn't installed.
```

with:

```markdown
- **`tui/app.py`** -- `MiloBrainApp`, the Textual TUI. Runs `BrainServer` as
  a background worker on its own event loop (no separate thread), so the
  pairing-PIN flow is a direct `await` on a modal screen rather than
  cross-thread signaling.
- **`tui/dashboard.py`** -- the main screen: identity, connection, model
  (with live tokens/sec), and pairing panels.
- **`tui/pairing.py`**, **`tui/model_picker.py`** -- modal screens for PIN
  entry and picking an installed Ollama model.
```

- [ ] **Step 5: Update Troubleshooting**

Remove the `**\`PyQt6 not installed — running headless\`**` entry entirely (that failure mode no longer exists).

- [ ] **Step 6: Commit**

```bash
git add brain/README.md
git commit -m "docs(brain): update README for the TUI, remove tray references"
```
