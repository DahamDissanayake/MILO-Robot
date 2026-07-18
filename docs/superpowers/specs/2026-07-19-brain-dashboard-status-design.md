# Brain TUI dashboard: live connection stage + pipeline health

Date: 2026-07-19

## Problem

The brain TUI's home screen (`DashboardScreen`) shows only a binary
"connected"/"no robot connected" in its Connection panel. Everything in
between — discovery, dialing, handshaking, retry backoff after a drop — and
whether the cognition pipelines (VAD, ASR, TTS, Vision, MCP) have actually
loaded is invisible unless you open the Logs screen and read raw log lines.
This surfaced directly: a real VAD bug (buffering fix, separate change) was
misread as a network flake because the dashboard gave no way to see *where*
in the connection lifecycle things were breaking, or that a pipeline had
failed to load at all.

## Goals

- The home screen shows the connector's actual current stage: idle,
  connecting (to a specific host:port), handshaking, connected, or retrying
  (with a countdown and the last error), live, via the existing 1s poll.
- The home screen shows whether VAD, ASR, TTS, Vision, and MCP have loaded,
  are still pending, or failed (with the error), reusing the same poll.

## Non-goals

- No push/event-driven status updates — polling every 1s (the existing
  `refresh_from()` cadence) is fine; pipeline status changes at most once
  per session.
- No change to `VadSegmenter`'s per-session lifecycle (a fresh
  `SileroSpeechDetector` is still built on every reconnect) — out of scope,
  not something this feature needs to fix.
- No new Logs-screen changes; this is additive on the dashboard only.

## Design

### 1. Connection-stage tracking (`net/connector.py`)

`RobotConnectorManager` gains state set at points that already exist in
`_connect_and_run`/`_tick` — no new control flow:

- `link_state: str` — `"idle" | "connecting" | "handshaking" | "connected" | "retrying"`
- `link_target: tuple[str, int] | None` — host:port currently being dialed; `None` when idle
- `last_error: str | None` — most recent connect/handshake failure; cleared on a successful connect
- `retry_at: float | None` — `time.monotonic()` deadline for the next attempt; the dashboard computes the remaining seconds itself each poll, no new timer in the connector

`_consecutive_drops` (already added for backoff) supplies the "attempt N" count.

Transitions:
- `_tick()`, nothing selected (empty discovery / no paired robot) → `link_state="idle"`, `link_target=None`
- `_connect_and_run()` entry → `link_state="connecting"`, `link_target=_parse_host_port(url)`, `last_error=None`
- socket obtained, before `brain_handshake()` → `link_state="handshaking"`
- handshake success (existing `connected_robot = peer` point) → `link_state="connected"`, `last_error=None`, `_consecutive_drops=0` (unchanged)
- `HandshakeError` → `link_state="idle"`, `last_error=f"handshake failed: {exc}"`
- generic `Exception` (drop) → `link_state="retrying"`, `last_error=f"{type(exc).__name__}: {exc}"`, `retry_at=time.monotonic() + backoff`

### 2. Pipeline health (`pipelines/*.py`, `session.py`)

New `pipelines/_lazy.py`:

```python
class LazyLoad:
    def __init__(self):
        self.status = "not_loaded"  # "not_loaded" | "ready" | "error"
        self.error: str | None = None

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

`SileroSpeechDetector`, `WhisperAsr`, `PiperTts`, `InsightFaceAnalyzer` extend
`LazyLoad`, replacing their hand-rolled `if self._model is None: self._load()`
guard with `self.ensure_loaded()`. `SileroSpeechDetector`'s existing
`model=` injection param (added for the buffering fix's tests) sets
`status="ready"` up front so injecting a fake model still skips the real
`_load()`/network call.

`FaceVision` and `VadSegmenter` (outer wrapper classes, not the model
holders) get read-only `status`/`error` properties delegating to the inner
analyzer/detector via `getattr(obj, "status", "ready")` / `getattr(obj,
"error", None)`, so test fakes without the attribute default to "ready"
rather than erroring.

`MiloMcpClient` gets `connected: bool` (`self._session is not None`).

`RobotCognitionSession.pipeline_status() -> dict[str, tuple[str, str | None]]`
returns `{"vad": (status, error)}` plus `{"mcp": (status, error)}` only when
`self._mcp is not None` (some robots don't advertise an MCP port).

`CognitionSessionFactory` tracks `self.current_session: RobotCognitionSession
| None`, set at the start of `handle()` and cleared in its `finally`.
`CognitionSessionFactory.pipeline_status()` returns its own persistent
`{"asr": ..., "tts": ..., "vision": ...}` merged with
`self.current_session.pipeline_status()` when a session is active — 3 entries
idle, 5 while connected.

### 3. Wiring (`__main__.py`, `tui/app.py`)

`__main__.py`'s `_build_handler()` currently constructs
`CognitionSessionFactory(...)` and keeps only the bound `.handle` method,
discarding the instance — the only place these pipeline objects live. It now
returns `(factory_or_none, handler)` so `main()` can pass the factory into
`MiloBrainApp` alongside `connector`/`cfg`/`rate_tracker`. `factory` is
`None` in the `ImportError` fallback path (full pipeline deps not
installed); `MiloBrainApp`/dashboard treat `None` as "pipeline status
unavailable" and omit the panel rather than erroring.

### 4. Dashboard (`tui/dashboard.py`)

`ConnectionPanel.render_connection()` reads the new connector fields instead
of only `connected_robot`:

```
[b]Connection[/b]
milo — handshaking (10.0.0.9:8765)…
Paired robots: 1
```

or, after a drop:

```
[b]Connection[/b]
retrying in 4s (attempt 3) — OSError: [Errno 11001] getaddrinfo failed
Paired robots: 1
```

New `PipelinesPanel` renders `factory.pipeline_status()`, one line per
entry, e.g. `VAD: ready`, `ASR: not_loaded`, `TTS: error — <msg>`.
`DashboardScreen.refresh_from()` gains a `factory` parameter and skips
updating/omits this panel when `factory is None`.

### 5. Testing

- `test_connector.py`: extend the existing tick tests (reusing the
  `_RaisingConnectCM` fake already added for the backoff feature) to assert
  `link_state`/`link_target`/`last_error`/`retry_at` at each stage.
- `test_pipelines.py`: unit test `LazyLoad.ensure_loaded()`'s ready/error
  transitions with a fake `_load` that raises.
- `test_cognition_session.py`: assert `factory.pipeline_status()`'s shape
  before, during, and after a session using the existing fakes.
- No unit test for the dashboard's Rich-text rendering itself — matches the
  existing convention (`ConnectionPanel`/`ModelPanel` aren't unit tested
  today either; verified by running the TUI).

## Error handling

Every new failure mode already surfaces through an existing exception path
(`_connect_and_run`'s `except Exception`, `LazyLoad.ensure_loaded`'s
`except Exception`) — nothing new to add here, this feature only makes
those existing failures *visible* on the home screen instead of only in the
log ring buffer.
