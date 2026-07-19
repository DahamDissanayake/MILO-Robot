# Pipelines Progress Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the brain TUI dashboard's flat `NAME: status` Pipelines list with a combined progress bar (N of however-many-are-currently-expected resolved) plus a detail line showing what's actively loading, what's still pending, and any errors.

**Architecture:** `LazyLoad` (brain/milo_brain/pipelines/_lazy.py) gains a `"loading"` status value, set immediately before the blocking `_load()` call so a concurrent dashboard poll can observe it. `PipelinesPanel` (brain/milo_brain/tui/dashboard.py) changes from a bare `Static` to a small composite (`Vertical` containing a label `Static`, a `textual.widgets.ProgressBar`, and a detail `Static`), still driven by the same `factory.pipeline_status()` call on the existing 1s poll.

**Tech Stack:** Python 3.14, Textual 8.2.8 (`textual.widgets.ProgressBar`), pytest + pytest-asyncio.

## Global Constraints

- `LazyLoad.status` values are exactly: `"not_loaded" | "loading" | "ready" | "error"`.
- MCP is NOT given a "loading" state — it keeps its existing `"ready"` / `"not_loaded"` binary from `RobotCognitionSession.pipeline_status()` (spec's explicit non-goal — see `docs/superpowers/specs/2026-07-19-pipelines-progress-bar-design.md`).
- An `"error"` pipeline counts as resolved for the progress bar (the bar can still reach 100%); the error is surfaced by name in the detail line below the bar, not by blocking the bar.
- Pipeline ordering in the detail line follows the existing `_PIPELINE_ORDER = ("asr", "tts", "vision", "vad", "mcp")` tuple already in dashboard.py, not dict iteration order.
- Run `python -m pytest` from `brain/` after each task (full suite, currently 144 tests before this plan).

---

### Task 1: `LazyLoad` gains a "loading" state

**Files:**
- Modify: `brain/milo_brain/pipelines/_lazy.py`
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Produces: `LazyLoad.status` now transitions `"not_loaded"` → `"loading"` → `"ready"` or `"error"` on each `ensure_loaded()` call (was `"not_loaded"` → `"ready"`/`"error"` directly, with no observable intermediate state). No change to the public method signatures (`ensure_loaded()`, `_load()`) or to `.error`.

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_pipelines.py`, in the `# --- LazyLoad` section, after `test_lazyload_ensure_loaded_retries_after_a_previous_error`:

```python
def test_lazyload_status_is_loading_while_load_runs():
    class _Loader(LazyLoad):
        def __init__(self):
            super().__init__()
            self.observed_status_during_load = None

        def _load(self) -> None:
            # Captures what a concurrent dashboard poll would see while
            # this (slow, blocking) call is still in progress.
            self.observed_status_during_load = self.status

    loader = _Loader()
    loader.ensure_loaded()
    assert loader.observed_status_during_load == "loading"
    assert loader.status == "ready"  # settles to ready once _load() returns
```

(`LazyLoad` is already imported in this file's LazyLoad section via `from milo_brain.pipelines._lazy import LazyLoad`.)

- [ ] **Step 2: Run test to verify it fails**

Run (from `brain/`): `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k loading_while_load_runs -v`
Expected: FAIL — `observed_status_during_load` is `None` (current code never sets `"loading"`, so `_load()` observes `"not_loaded"` — wait, actually check: current `ensure_loaded()` doesn't set anything before calling `_load()`, so `self.status` inside `_load()` would still read `"not_loaded"`, not `None`). Expected failure: `AssertionError: assert 'not_loaded' == 'loading'`.

- [ ] **Step 3: Write the implementation**

Replace the full contents of `brain/milo_brain/pipelines/_lazy.py` with:

```python
"""Shared status tracking for pipeline classes that lazily load a heavy
model on first use (Silero VAD, Whisper, Piper, InsightFace). Subclasses
implement _load() (sets whatever model attribute they own, raises on
failure); callers use ensure_loaded() instead of hand-rolling
`if self._model is None: self._load()`, and the dashboard reads .status/
.error to show what's actually working -- including while _load() is
still running, since it blocks synchronously on whatever thread the
caller is on (asyncio.to_thread(...)), making "loading" genuinely
observable by a concurrent dashboard poll on another thread.
"""

from __future__ import annotations


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

- [ ] **Step 4: Run test to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k loading_while_load_runs -v`
Expected: PASS

- [ ] **Step 5: Run the full pipelines test file, then the full suite, and commit**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -v`
Expected: all pass (including every existing `test_lazyload_*`, `test_silero_detector_*`, `test_whisper_asr_*`, `test_piper_tts_status_*`, `test_face_vision_status_*`, `test_insightface_analyzer_status_*` — none of them assert on an intermediate state, so all should be unaffected by adding one).

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all passing, no regressions

```bash
git add brain/milo_brain/pipelines/_lazy.py brain/tests/test_pipelines.py
git commit -m "feat(brain): add a 'loading' status to LazyLoad, observable mid-load"
```

---

### Task 2: `PipelinesPanel` becomes a progress bar

**Files:**
- Modify: `brain/milo_brain/tui/dashboard.py`
- Test: `brain/tests/test_tui_dashboard.py`

**Interfaces:**
- Consumes: `LazyLoad`'s new `"loading"` status (Task 1) flowing through unchanged `pipeline_status()` dicts (no changes needed in `session.py` — the dict shape `dict[str, tuple[str, str | None]]` is unchanged, only the set of possible `state` string values grows).
- Produces: `PipelinesPanel` is now a `Vertical` (not `Static`) with three children: `Static(id="pipelines-label")`, `ProgressBar(id="pipelines-bar")`, `Static(id="pipelines-detail")`. `PipelinesPanel.render_pipelines(status)` (same method name/signature as before) now updates the bar and detail line instead of a single block of text. `DashboardScreen.refresh_from(...)`'s call site is unchanged (`self.query_one(PipelinesPanel).render_pipelines(...)`).

**IMPORTANT — this task breaks two existing tests by construction:** `test_refresh_from_renders_pipelines_panel_when_factory_provided` and `test_refresh_from_omits_pipelines_when_factory_is_none` currently do `str(screen.query_one(PipelinesPanel).content)` — `.content` is a `Static`-only attribute, and `PipelinesPanel` will no longer be a `Static`. Both must be rewritten (not just left broken) as part of this task's Step 1.

- [ ] **Step 1: Rewrite the two now-broken tests and write the new failing tests**

In `brain/tests/test_tui_dashboard.py`, change the import block from:

```python
from milo_brain.tui.dashboard import (
    ConnectionPanel,
    DashboardScreen,
    IdentityPanel,
    ModelPanel,
    PipelinesPanel,
)
```

to:

```python
from textual.widgets import ProgressBar, Static as TextualStatic

from milo_brain.tui.dashboard import ConnectionPanel, DashboardScreen, IdentityPanel, ModelPanel
```

(`PipelinesPanel` is no longer referenced directly by any test below — they query its children by ID instead. `Static` is already imported at module scope in dashboard.py's own namespace; this test file doesn't currently import `Static` from `textual.widgets` at all, so it's added here aliased as `TextualStatic` to avoid any confusion with `IdentityPanel`/etc. which are themselves `Static` subclasses already imported by name.)

Replace `test_refresh_from_renders_pipelines_panel_when_factory_provided` (delete it) and `test_refresh_from_omits_pipelines_when_factory_is_none` (delete it) with these five tests in their place:

```python
def test_refresh_from_shows_progress_while_pipelines_are_loading():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("ready", None),
                    "tts": ("loading", None),
                    "vision": ("not_loaded", None),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 1  # only asr is resolved (ready)
            assert "1/3 ready" in detail
            assert "loading: TTS" in detail
            assert "pending: VISION" in detail

    asyncio.run(scenario())


def test_refresh_from_shows_all_ready_with_no_errors():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {"asr": ("ready", None), "tts": ("ready", None), "vision": ("ready", None)}

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 3
            assert detail == "All pipelines ready"

    asyncio.run(scenario())


def test_refresh_from_shows_ready_with_an_error_called_out():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("ready", None),
                    "tts": ("ready", None),
                    "vision": ("error", "no GPU found"),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 3  # errors count as resolved -- bar still completes
            assert "1 error" in detail
            assert "VISION: error — no GPU found" in detail

    asyncio.run(scenario())


def test_refresh_from_shows_ready_with_multiple_errors_pluralized():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("error", "boom"),
                    "tts": ("ready", None),
                    "vision": ("error", "no GPU found"),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 3
            assert "2 errors" in detail
            assert "ASR: error — boom" in detail
            assert "VISION: error — no GPU found" in detail

    asyncio.run(scenario())


def test_refresh_from_omits_pipelines_when_factory_is_none():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert "unavailable" in detail

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify the new/rewritten ones fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_dashboard.py -v`
Expected: FAIL on all 5 pipelines-related tests — `query_one("#pipelines-bar", ProgressBar)` raises `NoMatches` (no such widget exists yet; `PipelinesPanel` is still a bare `Static` with no children to query).

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/tui/dashboard.py`, change the import line from:

```python
from textual.widgets import Footer, Header, Static
```

to:

```python
from textual.widgets import Footer, Header, ProgressBar, Static
```

Replace the `PipelinesPanel` class (currently):

```python
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
```

with:

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

        ordered = [(name, status[name]) for name in _PIPELINE_ORDER if name in status]
        total = len(ordered)
        loading = [name.upper() for name, (state, _err) in ordered if state == "loading"]
        pending = [name.upper() for name, (state, _err) in ordered if state == "not_loaded"]
        errors = [(name.upper(), err) for name, (state, err) in ordered if state == "error"]
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
            names = ", ".join(f"{name}: error — {msg}" for name, msg in errors)
            plural = "s" if len(errors) > 1 else ""
            detail.update(f"Pipelines ready ({len(errors)} error{plural}) — {names}")
        else:
            detail.update("All pipelines ready")
```

Replace `DashboardScreen`'s `CSS` block (currently):

```python
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
```

with:

```python
    CSS = """
    DashboardScreen Static {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    PipelinesPanel {
        border: round $primary;
        padding: 0 1;
        width: 1fr;
        height: auto;
    }
    PipelinesPanel Static, PipelinesPanel ProgressBar {
        border: none;
        padding: 0;
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
```

(`PipelinesPanel` is a `Vertical` now, so the pre-existing `DashboardScreen Static` descendant-selector rule no longer matches the panel itself — but it WOULD still match `PipelinesPanel`'s own child `Static`s, since they're still descendants of `DashboardScreen`, giving each of them their own individual border. The new `PipelinesPanel` rule gives the whole composite one outer border/padding matching the other three panels; the `PipelinesPanel Static, PipelinesPanel ProgressBar` rule strips border/padding back off the children so only the outer container is bordered. Both new rules have equal-or-higher CSS specificity than `DashboardScreen Static` and come later in the stylesheet, so they win the cascade.)

`compose()`'s existing line `yield PipelinesPanel(id="pipelines-panel")` is unchanged — `PipelinesPanel` still takes an `id` kwarg the same way regardless of its new base class.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_dashboard.py -v`
Expected: all pass, 12 tests total in the file (the original 9 minus the 2 deleted pipelines tests, plus the 5 new ones from Step 1).

- [ ] **Step 5: Run the full suite**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all passing, no regressions (in particular `test_tui_app.py`, since `MiloBrainApp._refresh_dashboard()` calls `dashboard.refresh_from(..., self.factory)` every 1s and the app-level tests mount a real `DashboardScreen`).

- [ ] **Step 6: Manual verification**

From `brain/`, run the TUI for real:

```bash
../.venv/Scripts/python.exe -m milo_brain
```

Confirm the Pipelines panel shows a visible progress bar with a detail line beneath it instead of a flat status list, and that it still sits inside a bordered panel matching the other three panels' look. Press `q` to quit. If your environment can't drive an interactive TUI, say so honestly in the report — the test suite is the authoritative verification.

- [ ] **Step 7: Commit**

```bash
git add brain/milo_brain/tui/dashboard.py brain/tests/test_tui_dashboard.py
git commit -m "feat(brain): show pipeline startup as a progress bar instead of a status list"
```
