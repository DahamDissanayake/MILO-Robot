# Crash Log + Power Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistent crash log (full process crashes + silent background-task failures) surfaced in a new dashboard panel, plus slide-to-confirm Full Restart (Pi reboot) and Shutdown controls in the Tools zone.

**Architecture:** A new `CrashLog` class (JSON-lines file in `cfg.data_dir`) is fed from two capture points in `main.py`: `asyncio`'s `set_exception_handler` (background-task failures) and a `try/except` around `run()`'s top-level `asyncio.run(main())` (full process crashes). A new `api/system.py` exposes it read-only (`GET /api/crashes`) and adds the two action routes (`POST /api/system/restart`, `POST /api/system/shutdown`), both riding the existing session-auth middleware automatically. Two new frontend panels (`crashlog.js` in the `bridgeLog` zone, `power.js` in the `tools` zone) consume these.

**Tech Stack:** Python 3.11+ (bridge), aiohttp, vanilla JS (no framework, no build step, no existing JS test tooling — matches this codebase's established pattern throughout `webapp/static/js/`).

## Global Constraints

- `CrashLog.clear()` is called **only** by `POST /api/system/restart` — never automatically on boot. A crash followed by an accidental power cycle must still show in the history until a deliberate Full Restart.
- "Full Restart" reboots the **whole Pi** (`systemctl reboot`), not just the `milo-bridge` service — this is a *different*, complementary feature from the existing "Restart Bridge (I2C reset)" button in `servos.js` (which does `os._exit(0)` + systemd `Restart=always`, service-only). Do not remove or modify that existing button; the new "Full Restart" label must read distinctly (e.g. "Full Restart (Reboot Pi)") so the two are never confused in the UI.
- Every `/api/*` route is already behind `webapp/__init__.py`'s `_auth_middleware` — no new auth code needed anywhere in this plan.
- `sudo /usr/bin/systemctl reboot` / `poweroff` are the exact commands the sudoers scoping (done outside this plan, see below) permits — use these exact strings, not `reboot`/`shutdown` or relative paths.
- Match this codebase's fakes-over-mocks convention: injectable collaborators (`clock`, `sleep`, module-level function patching via `monkeypatch.setattr`), real file I/O against `tmp_path` in tests, no mocking frameworks.
- Commit after each task.

## Prerequisite (done outside this plan, before Task 1)

Sudoers scoping on the Pi — replacing `/etc/sudoers.d/90-cloud-init-users`'s blanket `NOPASSWD: ALL` with a scoped grant covering `systemctl reboot`, `systemctl poweroff`, and the `systemctl {restart,stop,start} milo-bridge` / `daemon-reload` commands this project's deployment docs already rely on. Done by the controller directly (not a subagent) using the backup-validate-verify-from-a-second-session procedure from the design spec (`docs/superpowers/specs/2026-07-18-crashlog-power-controls-design.md`), given the lockout risk of a sudoers mistake. This plan's tasks assume it's already in place.

---

### Task 1: CrashLog module

**Files:**
- Create: `bridge/milo_bridge/crashlog.py`
- Test: `bridge/tests/test_crashlog.py`

**Interfaces:**
- Produces: `CrashLog(path: Path)` with `.record(kind: str, exc: BaseException, context: str = "") -> None`, `.entries(n: int = 50) -> list[dict]`, `.count() -> int`, `.clear() -> None`. Consumed by Task 2 (`main.py` wiring) and Task 3 (`api/system.py`).

- [ ] **Step 1: Write the failing tests**

```python
# bridge/tests/test_crashlog.py
from milo_bridge.crashlog import CrashLog


def test_record_and_entries_round_trip(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    try:
        raise ValueError("boom")
    except ValueError as exc:
        crash_log.record("process", exc, context="test")
    entries = crash_log.entries()
    assert len(entries) == 1
    assert entries[0]["kind"] == "process"
    assert entries[0]["context"] == "test"
    assert entries[0]["error"] == "ValueError: boom"
    assert "Traceback" in entries[0]["traceback"]
    assert isinstance(entries[0]["t"], float)


def test_count_matches_number_of_records(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    assert crash_log.count() == 0
    for i in range(3):
        try:
            raise RuntimeError(f"err{i}")
        except RuntimeError as exc:
            crash_log.record("task", exc)
    assert crash_log.count() == 3


def test_entries_returns_most_recent_n(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    for i in range(5):
        try:
            raise RuntimeError(f"err{i}")
        except RuntimeError as exc:
            crash_log.record("task", exc)
    entries = crash_log.entries(n=2)
    assert len(entries) == 2
    assert entries[-1]["error"] == "RuntimeError: err4"


def test_clear_resets_count_and_entries(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    try:
        raise ValueError("boom")
    except ValueError as exc:
        crash_log.record("process", exc)
    assert crash_log.count() == 1
    crash_log.clear()
    assert crash_log.count() == 0
    assert crash_log.entries() == []


def test_entries_on_nonexistent_file_returns_empty_list(tmp_path):
    crash_log = CrashLog(tmp_path / "does-not-exist.log")
    assert crash_log.entries() == []
    assert crash_log.count() == 0


def test_entries_skips_corrupted_lines(tmp_path):
    path = tmp_path / "crashes.log"
    crash_log = CrashLog(path)
    try:
        raise ValueError("good")
    except ValueError as exc:
        crash_log.record("process", exc)
    with path.open("a", encoding="utf-8") as f:
        f.write("not valid json\n")
    entries = crash_log.entries()
    assert len(entries) == 1
    assert entries[0]["error"] == "ValueError: good"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest bridge/tests/test_crashlog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.crashlog'`

- [ ] **Step 3: Implement**

```python
# bridge/milo_bridge/crashlog.py
"""Persistent crash/error log: unhandled exceptions -- both full process
crashes and background-task failures asyncio would otherwise only log once
and forget -- survive here across restarts. Cleared only by a deliberate
Full Restart from the dashboard (webapp/api/system.py), not automatically
on every boot, so a crash followed by an accidental power cycle still shows
up until someone deliberately clears it.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

log = logging.getLogger(__name__)


class CrashLog:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, exc: BaseException, context: str = "") -> None:
        entry = {
            "t": time.time(),
            "kind": kind,
            "context": context,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            log.warning("failed to write crash log entry", exc_info=True)

    def entries(self, n: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        out: list[dict] = []
        for line in self._path.read_text(encoding="utf-8").splitlines()[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def count(self) -> int:
        if not self._path.exists():
            return 0
        with self._path.open(encoding="utf-8") as f:
            return sum(1 for _ in f)

    def clear(self) -> None:
        self._path.write_text("", encoding="utf-8")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest bridge/tests/test_crashlog.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/crashlog.py bridge/tests/test_crashlog.py
git commit -m "feat(bridge): add CrashLog for persistent crash/error tracking"
```

---

### Task 2: Wire crash capture into main.py + WebDeps

**Files:**
- Modify: `bridge/milo_bridge/main.py`
- Modify: `bridge/milo_bridge/webapp/deps.py`
- Modify: `bridge/tests/webapp/fakes.py`
- Test: `bridge/tests/test_main_crashlog.py`

**Interfaces:**
- Consumes: `CrashLog` (Task 1).
- Produces: `WebDeps.crash_log: Any` (new required field). `main._make_crash_exception_handler(crash_log)` (module-level, testable in isolation). `run()` records a full-process crash before re-raising. Consumed by Task 3 (`api/system.py` reads `deps.crash_log`).

- [ ] **Step 1: Write the failing tests**

```python
# bridge/tests/test_main_crashlog.py
"""main.py's two crash-capture points: the asyncio exception handler
(background-task failures) and run()'s top-level crash handling (full
process crashes)."""

from __future__ import annotations

import asyncio

import pytest

from milo_bridge.crashlog import CrashLog
from milo_bridge.main import _make_crash_exception_handler


def test_crash_exception_handler_records_task_failures_and_calls_default(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    default_calls = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.default_exception_handler = default_calls.append
        loop.set_exception_handler(_make_crash_exception_handler(crash_log))
        loop.call_exception_handler({
            "message": "Task exception was never retrieved",
            "exception": RuntimeError("background task failure"),
        })

    asyncio.run(scenario())
    assert crash_log.count() == 1
    entry = crash_log.entries()[0]
    assert entry["kind"] == "task"
    assert entry["context"] == "Task exception was never retrieved"
    assert entry["error"] == "RuntimeError: background task failure"
    assert len(default_calls) == 1
    assert default_calls[0]["message"] == "Task exception was never retrieved"


def test_crash_exception_handler_ignores_context_without_an_exception(tmp_path):
    """The context dict doesn't always carry an 'exception' key (e.g. a
    plain warning-level context) -- must not crash trying to record None,
    and must still forward to the default handler."""
    crash_log = CrashLog(tmp_path / "crashes.log")
    default_calls = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.default_exception_handler = default_calls.append
        loop.set_exception_handler(_make_crash_exception_handler(crash_log))
        loop.call_exception_handler({"message": "some non-exception warning"})

    asyncio.run(scenario())
    assert crash_log.count() == 0
    assert len(default_calls) == 1


def test_run_records_a_full_process_crash_before_reraising(monkeypatch, tmp_path):
    from milo_bridge import main as main_mod
    from milo_bridge.config import BridgeConfig

    cfg = BridgeConfig(robot_id="r", robot_name="milo", data_dir=str(tmp_path))
    monkeypatch.setattr(main_mod.BridgeConfig, "load", staticmethod(lambda: cfg))

    async def failing_main():
        raise RuntimeError("boot exploded")

    monkeypatch.setattr(main_mod, "main", failing_main)

    with pytest.raises(RuntimeError, match="boot exploded"):
        main_mod.run()

    crash_log = main_mod.CrashLog(tmp_path / "crashes.log")
    entries = crash_log.entries()
    assert len(entries) == 1
    assert entries[0]["kind"] == "process"
    assert entries[0]["error"] == "RuntimeError: boot exploded"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest bridge/tests/test_main_crashlog.py -v`
Expected: FAIL with `ImportError: cannot import name '_make_crash_exception_handler' from 'milo_bridge.main'`

- [ ] **Step 3: Implement**

In `bridge/milo_bridge/main.py`, add the import near the top (alongside the existing relative imports, after `from .config import BridgeConfig`):

```python
from .crashlog import CrashLog
```

Add this factory function right after `_make_control_change_handler` (matching that function's existing style/placement):

```python
def _make_crash_exception_handler(crash_log: CrashLog):
    """asyncio's default handler for a background task's unhandled exception
    just logs it once and forgets -- this also persists it to CrashLog so it
    survives in the dashboard's Crash Log panel, without changing the
    existing journal-visible logging behavior (default_exception_handler
    still runs)."""
    def handler(loop, context):
        exc = context.get("exception")
        if exc is not None:
            crash_log.record("task", exc, context.get("message", ""))
        loop.default_exception_handler(context)
    return handler
```

In `main()`, right after the existing block:
```python
    log_buffer = RingBufferLogHandler()
    logging.getLogger().addHandler(log_buffer)
```
add:
```python
    crash_log = CrashLog(Path(cfg.data_dir) / "crashes.log")
    asyncio.get_running_loop().set_exception_handler(_make_crash_exception_handler(crash_log))
```

In the `WebDeps(...)` construction inside `main()`, add `crash_log=crash_log,` (e.g. right after `log_buffer=log_buffer,`):
```python
    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=motion_servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
        graph_api=graph_api, graph_store=graph,
        broker=broker, media_hub=hub, log_buffer=log_buffer, crash_log=crash_log,
        hardware_status=hardware_status,
        get_link_state=lambda: manager.link_state if manager is not None else "disconnected",
    )
```

Replace `run()`'s body:
```python
def run() -> None:
    asyncio.run(main())
```
with:
```python
def run() -> None:
    cfg = BridgeConfig.load()
    crash_log = CrashLog(Path(cfg.data_dir) / "crashes.log")
    try:
        asyncio.run(main())
    except BaseException as exc:
        crash_log.record("process", exc)
        raise
```

In `bridge/milo_bridge/webapp/deps.py`, add the new field to `WebDeps` (right after `log_buffer`):
```python
    log_buffer: Any | None # RingBufferLogHandler (Task 7)
    crash_log: Any          # CrashLog -- always constructed, never None
```

In `bridge/tests/webapp/fakes.py`, add the import near the top (alongside the other `milo_bridge` imports):
```python
from milo_bridge.crashlog import CrashLog
```
and add a default in `make_deps()`'s `WebDeps(...)` construction (right after `log_buffer=None,`), using a fresh temp directory per call so existing tests that don't care about crash_log stay isolated:
```python
import tempfile
```
(add this stdlib import near the top of `fakes.py` too), then:
```python
        log_buffer=None,
        crash_log=CrashLog(Path(tempfile.mkdtemp()) / "crashes.log"),
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest bridge/tests/test_main_crashlog.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full bridge suite**

Run: `pytest bridge/tests -q`
Expected: PASS (all tests — the `WebDeps` field addition must not break any existing test that constructs `WebDeps` via `make_deps()`, since that function's default now supplies `crash_log` automatically)

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/main.py bridge/milo_bridge/webapp/deps.py bridge/tests/webapp/fakes.py bridge/tests/test_main_crashlog.py
git commit -m "feat(bridge): wire CrashLog into main.py (task + process crash capture)"
```

---

### Task 3: api/system.py

**Files:**
- Create: `bridge/milo_bridge/webapp/api/system.py`
- Modify: `bridge/milo_bridge/webapp/api/__init__.py`
- Test: `bridge/tests/webapp/test_system.py`

**Interfaces:**
- Consumes: `deps.crash_log` (Task 2).
- Produces: `GET /api/crashes`, `POST /api/system/restart`, `POST /api/system/shutdown`. Consumed by Task 4 (`crashlog.js`) and Task 5 (`power.js`).

- [ ] **Step 1: Write the failing tests**

```python
# bridge/tests/webapp/test_system.py
import asyncio

import milo_bridge.webapp.api.system as system_mod

from .client_helpers import authed_client
from .fakes import make_deps


async def test_get_crashes_returns_count_and_entries():
    deps = make_deps()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        deps.crash_log.record("process", exc)
    client = await authed_client(deps)
    try:
        data = await (await client.get("/api/crashes")).json()
        assert data["count"] == 1
        assert data["entries"][0]["error"] == "ValueError: boom"
    finally:
        await client.close()


async def test_post_restart_clears_crash_log_and_schedules_reboot(monkeypatch):
    calls = []

    async def fake_run(*args):
        calls.append(args)

    monkeypatch.setattr(system_mod, "_run", fake_run)
    monkeypatch.setattr(system_mod, "REBOOT_DELAY_S", 0)

    deps = make_deps()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        deps.crash_log.record("process", exc)
    assert deps.crash_log.count() == 1

    client = await authed_client(deps)
    try:
        resp = await client.post("/api/system/restart")
        assert await resp.json() == {"ok": True}
        assert deps.crash_log.count() == 0  # cleared immediately, not deferred
        await asyncio.sleep(0.05)  # let the deferred task run (delay set to 0)
        assert calls == [("sudo", "/usr/bin/systemctl", "reboot")]
    finally:
        await client.close()


async def test_post_shutdown_does_not_clear_crash_log(monkeypatch):
    calls = []

    async def fake_run(*args):
        calls.append(args)

    monkeypatch.setattr(system_mod, "_run", fake_run)
    monkeypatch.setattr(system_mod, "REBOOT_DELAY_S", 0)

    deps = make_deps()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        deps.crash_log.record("process", exc)
    assert deps.crash_log.count() == 1

    client = await authed_client(deps)
    try:
        resp = await client.post("/api/system/shutdown")
        assert await resp.json() == {"ok": True}
        assert deps.crash_log.count() == 1  # NOT cleared
        await asyncio.sleep(0.05)
        assert calls == [("sudo", "/usr/bin/systemctl", "poweroff")]
    finally:
        await client.close()


async def test_deferred_system_command_logs_and_swallows_a_failing_command(monkeypatch, caplog):
    async def fake_run(*args):
        raise OSError("command not found")

    monkeypatch.setattr(system_mod, "_run", fake_run)
    monkeypatch.setattr(system_mod, "REBOOT_DELAY_S", 0)

    await system_mod._deferred_system_command("sudo", "/usr/bin/systemctl", "reboot")
    # must not raise -- a failed system command shouldn't crash the caller
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest bridge/tests/webapp/test_system.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.webapp.api.system'`

- [ ] **Step 3: Implement**

```python
# bridge/milo_bridge/webapp/api/system.py
"""System-level actions: crash log visibility, full restart, shutdown.

Restart/shutdown are deferred by a short delay after the response is sent
(REBOOT_DELAY_S), so the HTTP response actually reaches the browser before
the Pi goes down -- otherwise the client just sees a dropped connection
with no confirmation. Matches the same delay-then-act idiom motion.py's
existing "Restart Bridge (I2C reset)" button already uses for its own
os._exit(0) (RESTART_DELAY_S there) -- this is a different, complementary
action (a full Pi reboot/poweroff via systemctl, not a service-only exit).
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

log = logging.getLogger(__name__)

REBOOT_DELAY_S = 0.3


async def _run(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(*args)
    await proc.wait()


async def get_crashes(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    crash_log = deps.crash_log
    return web.json_response({"count": crash_log.count(), "entries": crash_log.entries(50)})


async def _deferred_system_command(*args: str) -> None:
    await asyncio.sleep(REBOOT_DELAY_S)
    try:
        await _run(*args)
    except Exception:
        log.exception("system command failed: %s", args)


async def post_restart(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    deps.crash_log.clear()
    asyncio.create_task(_deferred_system_command("sudo", "/usr/bin/systemctl", "reboot"))
    return web.json_response({"ok": True})


async def post_shutdown(request: web.Request) -> web.Response:
    asyncio.create_task(_deferred_system_command("sudo", "/usr/bin/systemctl", "poweroff"))
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_get("/api/crashes", get_crashes)
    app.router.add_post("/api/system/restart", post_restart)
    app.router.add_post("/api/system/shutdown", post_shutdown)
```

In `bridge/milo_bridge/webapp/api/__init__.py`, change:
```python
from . import auth, graph, imu, logs, media, motion_meta, speak, status


def register_routes(app: web.Application) -> None:
    auth.register(app)
    status.register(app)
    media.register(app)
    speak.register(app)
    graph.register(app)
    motion_meta.register(app)
    logs.register(app)
    imu.register(app)
```
to:
```python
from . import auth, graph, imu, logs, media, motion_meta, speak, status, system


def register_routes(app: web.Application) -> None:
    auth.register(app)
    status.register(app)
    media.register(app)
    speak.register(app)
    graph.register(app)
    motion_meta.register(app)
    logs.register(app)
    imu.register(app)
    system.register(app)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest bridge/tests/webapp/test_system.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full bridge suite**

Run: `pytest bridge/tests -q`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/api/system.py bridge/milo_bridge/webapp/api/__init__.py bridge/tests/webapp/test_system.py
git commit -m "feat(bridge): add /api/crashes and /api/system/{restart,shutdown} routes"
```

---

### Task 4: crashlog.js frontend panel

**Files:**
- Create: `bridge/milo_bridge/webapp/static/js/panels/crashlog.js`
- Modify: `bridge/milo_bridge/webapp/static/js/registry.js`

**Interfaces:**
- Consumes: `GET /api/crashes` (Task 3).
- Produces: a panel registered in the `bridgeLog` zone, alongside the existing `log` panel.

No automated test (this codebase has no JS test infrastructure anywhere — confirmed, matches the design spec's stated non-goal). Verify manually per Step 3.

- [ ] **Step 1: Implement the panel**

```javascript
// bridge/milo_bridge/webapp/static/js/panels/crashlog.js
export default {
  id: "crashlog", title: "Crash Log",
  mount(el) {
    el.innerHTML = `
      <div id="crash-count" style="font-weight:600;margin-bottom:6px">Crashes since last restart: —</div>
      <div id="crash-entries" style="font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere"></div>`;
    const countEl = el.querySelector("#crash-count");
    const entriesEl = el.querySelector("#crash-entries");

    function render(data) {
      countEl.textContent = `Crashes since last restart: ${data.count}`;
      if (data.entries.length === 0) {
        entriesEl.textContent = "No crashes recorded.";
        return;
      }
      entriesEl.innerHTML = data.entries.slice().reverse().map((e) => {
        const t = new Date(e.t * 1000).toLocaleString();
        return `<div style="margin-bottom:6px;border-bottom:1px solid var(--line);padding-bottom:4px">
          <div><b>${e.kind}</b> — ${t}</div>
          <div>${e.error}</div>
        </div>`;
      }).join("");
    }

    fetch("/api/crashes").then((r) => r.json()).then(render).catch(() => {
      countEl.textContent = "Crashes since last restart: (failed to load)";
    });
  },
};
```

- [ ] **Step 2: Register it in the bridgeLog zone**

In `bridge/milo_bridge/webapp/static/js/registry.js`, change:
```javascript
import log from "./panels/log.js";

export const registry = {
  cockpitMove: [move],
  cockpitCamera: [camera, poses],
  cockpitSide: [comm, sensors],
  bridgeLog: [log],
  graph: [graph],
  tools: [servos],
};
```
to:
```javascript
import log from "./panels/log.js";
import crashlog from "./panels/crashlog.js";

export const registry = {
  cockpitMove: [move],
  cockpitCamera: [camera, poses],
  cockpitSide: [comm, sensors],
  bridgeLog: [log, crashlog],
  graph: [graph],
  tools: [servos],
};
```

- [ ] **Step 3: Manually verify**

Run `bridge/tools/webdev.py` (off-Pi dev server with fake drivers — see `brain/README.md`'s and `bridge/milo_bridge/README.md`'s existing docs for this tool), open the dashboard, and confirm:
- The Crash Log panel appears next to the Bridge Log panel.
- With no crashes recorded, it shows "Crashes since last restart: 0" and "No crashes recorded."
- (Optional deeper check) temporarily call `deps.crash_log.record(...)` from a Python REPL against the same data dir, or trigger a real background-task failure, and confirm an entry renders with kind/timestamp/error.

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/crashlog.js bridge/milo_bridge/webapp/static/js/registry.js
git commit -m "feat(bridge): add Crash Log panel to the dashboard"
```

---

### Task 5: power.js frontend panel (slide-to-confirm Restart/Shutdown)

**Files:**
- Create: `bridge/milo_bridge/webapp/static/js/panels/power.js`
- Modify: `bridge/milo_bridge/webapp/static/js/registry.js`

**Interfaces:**
- Consumes: `POST /api/system/restart`, `POST /api/system/shutdown` (Task 3).
- Produces: a panel registered in the `tools` zone, alongside the existing `servos` panel.

No automated test (same reasoning as Task 4). Verify manually per Step 3.

- [ ] **Step 1: Implement the panel**

```javascript
// bridge/milo_bridge/webapp/static/js/panels/power.js
function slideConfirm(el, { label, onConfirm }) {
  el.innerHTML = `
    <div style="margin-bottom:10px">
      <div style="margin-bottom:4px">${label}</div>
      <input type="range" min="0" max="100" value="0" class="slide-confirm" style="width:100%;accent-color:var(--danger)">
      <div class="slide-status" style="font-size:11px;color:var(--muted)">Slide to confirm</div>
    </div>`;
  const slider = el.querySelector(".slide-confirm");
  const status = el.querySelector(".slide-status");
  let fired = false;
  const ctl = { setStatus: (text) => { status.textContent = text; } };
  slider.oninput = () => {
    if (fired) return;
    if (Number(slider.value) >= 100) {
      fired = true;
      slider.disabled = true;
      ctl.setStatus("Confirmed — sending…");
      onConfirm(ctl);
    }
  };
  slider.onchange = () => {
    if (!fired) {
      slider.value = 0;
      ctl.setStatus("Slide to confirm");
    }
  };
  return ctl;
}

async function postAction(path, ctl, pendingText) {
  try {
    const r = await fetch(path, { method: "POST" });
    const data = await r.json();
    ctl.setStatus(data.ok ? pendingText : `Failed: ${data.error || "unknown error"}`);
  } catch {
    ctl.setStatus("Failed: request error");
  }
}

export default {
  id: "power", title: "Power",
  mount(el) {
    el.innerHTML = `<div id="restart-slot"></div><div id="shutdown-slot"></div>`;
    slideConfirm(el.querySelector("#restart-slot"), {
      label: "Full Restart (reboot the Pi)",
      onConfirm: (ctl) => postAction("/api/system/restart", ctl, "Rebooting…"),
    });
    slideConfirm(el.querySelector("#shutdown-slot"), {
      label: "Shutdown (power off the Pi)",
      onConfirm: (ctl) => postAction("/api/system/shutdown", ctl, "Shutting down…"),
    });
  },
};
```

- [ ] **Step 2: Register it in the tools zone**

By this point Task 4 has already changed `bridge/milo_bridge/webapp/static/js/registry.js` to:
```javascript
// Adding a panel = create js/panels/<name>.js + add it to the right zone below.
import camera from "./panels/camera.js";
import move from "./panels/move.js";
import comm from "./panels/comm.js";
import sensors from "./panels/sensors.js";
import graph from "./panels/graph.js";
import poses from "./panels/poses.js";
import servos from "./panels/servos.js";
import log from "./panels/log.js";
import crashlog from "./panels/crashlog.js";

export const registry = {
  cockpitMove: [move],
  cockpitCamera: [camera, poses],
  cockpitSide: [comm, sensors],
  bridgeLog: [log, crashlog],
  graph: [graph],
  tools: [servos],
};
```
Change it to:
```javascript
// Adding a panel = create js/panels/<name>.js + add it to the right zone below.
import camera from "./panels/camera.js";
import move from "./panels/move.js";
import comm from "./panels/comm.js";
import sensors from "./panels/sensors.js";
import graph from "./panels/graph.js";
import poses from "./panels/poses.js";
import servos from "./panels/servos.js";
import log from "./panels/log.js";
import crashlog from "./panels/crashlog.js";
import power from "./panels/power.js";

export const registry = {
  cockpitMove: [move],
  cockpitCamera: [camera, poses],
  cockpitSide: [comm, sensors],
  bridgeLog: [log, crashlog],
  graph: [graph],
  tools: [servos, power],
};
```

- [ ] **Step 3: Manually verify**

Using the same `bridge/tools/webdev.py` off-Pi dev server as Task 4:
- The Power panel appears in Tools, next to Servo Test, clearly labeled "Full Restart (reboot the Pi)" and "Shutdown (power off the Pi)" — distinct from the existing "Restart Bridge (I2C reset)" button in the Servo Test panel.
- Dragging a slider partway and releasing snaps it back to 0 with no request sent (check the Network tab / dev server logs — no POST should fire).
- Dragging a slider fully to the end fires the POST once, disables that slider, and shows a status message (the off-Pi dev server has no real `sudo systemctl`, so expect this to either fail cleanly with a visible "Failed: ..." message, or if the fake `_run` used by `webdev.py`'s environment doesn't exist, expect a real subprocess failure — either way, confirm the UI shows *something* rather than hanging silently or throwing an unhandled JS error in the console).

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/power.js bridge/milo_bridge/webapp/static/js/registry.js
git commit -m "feat(bridge): add Power panel (slide-to-confirm restart/shutdown) to Tools"
```

---

### Task 6: Deploy and verify on the Pi

**Files:** none (deployment only).

No automated test — this is the real-hardware verification pass.

- [ ] **Step 1: Push and pull**

```bash
git push origin main
```
Then on the Pi:
```bash
cd ~/MILO-Robot && git pull
```

- [ ] **Step 2: Run the full suite on the Pi**

```bash
source ~/.venvs/milo/bin/activate
python -m pytest bridge/tests -q
```
Expected: all tests pass (matches the local run from every prior task).

- [ ] **Step 3: Restart and verify the service comes up clean**

```bash
sudo systemctl restart milo-bridge
sudo systemctl status milo-bridge --no-pager -l
```
Expected: `active (running)`, no crash-loop.

- [ ] **Step 4: Verify the sudoers scoping actually works for the new feature**

From the Pi itself (not through the webapp yet), non-destructively:
```bash
sudo -n -l
```
Expected: the output lists `(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, ...` exactly as installed in the Prerequisite step — confirms the rule parsed correctly and covers the exact commands `api/system.py` will invoke, without actually running them. Then confirm the mechanism itself works (not just the listing) with an already-covered, harmless command:
```bash
sudo -n /usr/bin/systemctl daemon-reload
echo "exit code: $?"
```
Expected: exit code 0, no password prompt.

- [ ] **Step 5: Verify in the real dashboard**

Open `http://milo.local` in a browser, log in, and confirm:
- The Crash Log panel is visible next to the Bridge Log panel and loads without error.
- The Power panel is visible in Tools, next to Servo Test.
- (Do not actually confirm-slide Restart/Shutdown during this verification pass unless you specifically intend to reboot/power off the robot right now — confirm the panels render and the sliders are interactive, that's sufficient to close this task.)

- [ ] **Step 6: Report final state**

No commit for this task (deployment only) — report the verification results back.
