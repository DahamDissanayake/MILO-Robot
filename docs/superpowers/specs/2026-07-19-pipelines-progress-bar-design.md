# Brain dashboard: Pipelines panel as a progress bar

Date: 2026-07-19

## Problem

The Pipelines panel added in the previous plan (2026-07-19-brain-dashboard-status)
shows a flat list of `NAME: status` lines (`ASR: ready`, `TTS: not_loaded`, ...).
Two gaps:

1. A pipeline sits at `not_loaded` for the *entire* duration of a slow first
   load (e.g. Whisper downloading a model) with no visible sign anything is
   happening — it looks identical to "hasn't started" the whole time.
2. There's no at-a-glance summary of overall startup progress; you have to
   read all five lines to know how far along things are.

## Goals

- A single combined progress bar showing overall pipeline-startup progress
  (N of however-many-are-currently-expected are resolved).
- A pipeline that's actively loading is visibly distinguishable from one
  that hasn't started yet.
- Once everything is resolved, the panel settles into a clear "ready" state.
- A failed pipeline still lets the bar complete (it's not coming back on its
  own) but is called out by name with its error.

## Non-goals

- MCP does not get a new mid-connect "loading" state. Its `connect()` call
  happens before a `RobotCognitionSession` exists and only enters
  `pipeline_status()` once it has already succeeded (a failure surfaces via
  the Connection panel's existing "handshaking"/"retrying" stage instead,
  from the prior plan). Giving MCP a comparable loading signal would mean
  reaching into `CognitionSessionFactory.handle()`'s pre-session code path —
  out of scope here. MCP keeps its current `"ready"` / `"not_loaded"` binary.
- No real percentage-based progress within a single pipeline's load (no
  underlying library here exposes a download/init percentage callback) —
  "progress" is step-count across pipelines, not smooth sub-progress within
  one.
- The panel's existing 3-entry-idle / 5-entry-active shape (from the prior
  plan) is unchanged.

## Design

### 1. `LazyLoad` gains a "loading" state (`pipelines/_lazy.py`)

```python
class LazyLoad:
    def __init__(self) -> None:
        self.status: str = "not_loaded"  # "not_loaded" | "loading" | "ready" | "error"
        self.error: str | None = None

    def _load(self) -> None:
        raise NotImplementedError

    def ensure_loaded(self) -> None:
        if self.status == "ready":
            return
        self.status, self.error = "loading", None
        try:
            self._load()
            self.status, self.error = "ready", None
        except Exception as exc:
            self.status, self.error = "error", str(exc)
            raise
```

`ensure_loaded()` already runs synchronously inside whatever thread the
caller is on (`asyncio.to_thread(self._asr.transcribe, ...)` etc.), so
setting `status="loading"` immediately before the blocking `_load()` call
makes it genuinely observable by a concurrent dashboard poll (Python's GIL
is released during the I/O/C-extension work `_load()` does). No other
change needed in `SileroSpeechDetector`/`WhisperAsr`/`PiperTts`/
`InsightFaceAnalyzer` — they already all route through `ensure_loaded()`.

### 2. Dashboard panel (`tui/dashboard.py`)

`PipelinesPanel` becomes a small composite widget instead of a bare
`Static`:

```python
class PipelinesPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("[b]Pipelines[/b]", id="pipelines-label")
        yield ProgressBar(total=1, show_eta=False, id="pipelines-bar")
        yield Static("", id="pipelines-detail")

    def render_pipelines(self, status: dict[str, tuple[str, str | None]]) -> None:
        bar = self.query_one("#pipelines-bar", ProgressBar)
        detail = self.query_one("#pipelines-detail", Static)
        if not status:
            bar.update(total=1, progress=0)
            detail.update("(unavailable)")
            return

        total = len(status)
        loading = [n.upper() for n, (s, _e) in status.items() if s == "loading"]
        pending = [n.upper() for n, (s, _e) in status.items() if s == "not_loaded"]
        errors = [(n.upper(), e) for n, (s, e) in status.items() if s == "error"]
        done = total - len(loading) - len(pending)

        bar.update(total=total, progress=done)

        if done < total:
            parts = [f"{done}/{total} ready"]
            if loading:
                parts.append(f"loading: {', '.join(loading)}")
            if pending:
                parts.append(f"pending: {', '.join(pending)}")
            detail.update(" — ".join(parts))
        elif errors:
            names = ", ".join(f"{n}: error — {msg}" for n, msg in errors)
            detail.update(f"Pipelines ready ({len(errors)} error{'s' if len(errors) > 1 else ''}) — {names}")
        else:
            detail.update("All pipelines ready")
```

Ordering within `loading`/`pending`/`errors` follows `_PIPELINE_ORDER`
(`"asr","tts","vision","vad","mcp"`) rather than dict order, same as
today's list rendering — the pseudocode above iterates `status.items()`
for brevity but the real implementation filters through `_PIPELINE_ORDER`
for deterministic output, matching the existing convention.

`DashboardScreen`'s CSS needs a rule for the new composite so it still
renders inside the existing bordered-panel look (today's `DashboardScreen
Static` rule only targets bare `Static` widgets; `PipelinesPanel` itself
is a `Vertical` now, so it needs its own border/padding rule, and its
child `Static`s should NOT get the bordered-panel styling meant for
top-level panels).

### 3. Testing

- `pipelines/_lazy.py`: extend the existing `LazyLoad` tests with a
  transition assertion — status is `"loading"` while `_load()` is running
  (verified via a fake `_load()` that asserts `self.status == "loading"`
  from inside itself before returning/raising).
- `tui/dashboard.py`: drive `PipelinesPanel` through `DashboardScreen`via
  `app.run_test()` (same harness the existing dashboard tests use) and
  assert `ProgressBar.progress`/`.total` plus the detail `Static`'s
  rendered text, covering: idle (3 total, all not_loaded), partial (some
  ready, one loading, rest pending), fully ready no errors, fully resolved
  with one error, and the `factory is None` unavailable case.

## Error handling

No new failure modes — this only changes how already-existing state
(`status`/`error` on each pipeline, `pipeline_status()`'s dict) is
rendered. Everything downstream of `_load()` raising is unchanged (it
still propagates so the caller's existing `except Exception: log.exception`
handling is untouched).
