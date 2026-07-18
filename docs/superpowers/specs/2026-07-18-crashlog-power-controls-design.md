# Crash Log + Power Controls Design

## Goal

Add a persistent crash log (visible alongside the existing bridge log in the web dashboard) and slide-to-confirm Full Restart / Shutdown controls (in the existing Tools zone), so instability is visible without SSHing in, and a full reboot/poweroff can be triggered from the dashboard.

## Context

Today's debugging session needed manual SSH + `systemctl show ... NRestarts` + `journalctl` to discover the service had crash-looped 6 times, and separately found a background-task failure (`FaceDisplay._idle_loop()` `OSError`) that never surfaces anywhere in the dashboard today — it only exists in the systemd journal. This feature makes both classes of failure visible in the UI directly, and gives a safe, explicit way to reboot/power off without SSH.

## Non-Goals

- No new authentication mechanism — every `/api/*` route is already covered by the existing session-auth middleware (`webapp/__init__.py`'s `_auth_middleware`), and the new routes ride that automatically.
- Not touching the MCP server, gait engine, or any motion-control code.
- Not attempting to auto-recover from crashes or auto-mitigate — purely visibility + manual control.

## 1. Sudoers scoping (prerequisite, done once, outside the app)

`/etc/sudoers.d/90-cloud-init-users` currently grants `dama` `(ALL) NOPASSWD: ALL` (a cloud-init default from provisioning, not project-added). Replaced with:

```
dama ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart milo-bridge, /usr/bin/systemctl stop milo-bridge, /usr/bin/systemctl start milo-bridge, /usr/bin/systemctl daemon-reload
```

`reboot`/`shutdown` are symlinks to `/usr/bin/systemctl` on this system (confirmed via `ls -la /sbin/reboot`), so `systemctl reboot`/`systemctl poweroff` are the canonical invocations — used consistently in both the sudoers rule and the Python subprocess calls (exact string match required for sudoers to permit a NOPASSWD command).

**Safety procedure** (manual, on the Pi, before any code changes):
1. `sudo cp /etc/sudoers.d/90-cloud-init-users /etc/sudoers.d/90-cloud-init-users.bak` — sudoers ignores dotted filenames under `sudoers.d`, so this is inert as a policy file but recoverable as a backup.
2. Write the new content to a temp file, validate with `sudo visudo -c -f /tmp/milo-sudoers-new`.
3. Install: `sudo cp /tmp/milo-sudoers-new /etc/sudoers.d/90-cloud-init-users && sudo chmod 440 /etc/sudoers.d/90-cloud-init-users`.
4. Verify from a **second, independent SSH session** (keep the first open) that `sudo -n /usr/bin/systemctl daemon-reload` still works before trusting the change made in the first session.
5. If broken: restore from the `.bak` file via the still-open first session.

## 2. Crash capture — `bridge/milo_bridge/crashlog.py` (new module)

```python
class CrashLog:
    def __init__(self, path: Path): ...
    def record(self, kind: str, exc: BaseException, context: str = "") -> None: ...
    def entries(self, n: int = 50) -> list[dict]: ...
    def count(self) -> int: ...
    def clear(self) -> None: ...
```

- Storage: JSON-lines file at `Path(cfg.data_dir) / "crashes.log"`. Each line: `{"t": <unix ts>, "kind": "process" | "task", "context": <str>, "error": "<ExcType>: <message>", "traceback": "<full formatted traceback>"}`.
- `record()` appends one line and does not raise on its own I/O failure (best-effort logging must never be the thing that crashes the crash logger). `entries()` skips any line that fails to parse as JSON rather than raising — a partially-written line from a crash mid-write (the crash logger itself running during a real process crash) must not make the whole crash log unreadable.
- **Capture point 1 — background task failures**: `main()` calls `asyncio.get_running_loop().set_exception_handler(handler)` early in startup. `handler(loop, context)` calls `crash_log.record("task", context.get("exception"), context.get("message", ""))` if `context.get("exception")` is not None, **then** calls `loop.default_exception_handler(context)` so the existing "Task exception was never retrieved" journal logging is preserved unchanged, not replaced.
- **Capture point 2 — full process crashes**: `run()` (the `[project.scripts] milo-bridge` entrypoint) wraps `asyncio.run(main())` in `try/except BaseException`, calls `crash_log.record("process", exc)`, then re-raises so the process still exits non-zero and systemd's existing `Restart=` policy still fires exactly as today.
- **Count/reset semantics**: `crash_log.clear()` is called **only** from the restart API handler (Section 3), never automatically on boot. A crash followed by an accidental power cycle must still show in the history — only a deliberate Full Restart button press clears it.

## 3. New API — `bridge/milo_bridge/webapp/api/system.py` (new module)

```python
async def get_crashes(request: web.Request) -> web.Response:
    # {"count": crash_log.count(), "entries": crash_log.entries(50)}

async def post_restart(request: web.Request) -> web.Response:
    # crash_log.clear(); respond {"ok": True}; schedule a deferred
    # `sudo /usr/bin/systemctl reboot` (via asyncio.create_task + a short
    # sleep) so the HTTP response actually reaches the browser before the
    # Pi goes down.

async def post_shutdown(request: web.Request) -> web.Response:
    # same deferred-task pattern, `sudo /usr/bin/systemctl poweroff`,
    # no crash-log clear.

def register(app: web.Application) -> None:
    app.router.add_get("/api/crashes", get_crashes)
    app.router.add_post("/api/system/restart", post_restart)
    app.router.add_post("/api/system/shutdown", post_shutdown)
```

Registered the same way every other `api/*.py` module is (via `api/__init__.py`'s `register_routes`), so it's automatically behind the existing session-auth middleware — no new auth code.

`deps.crash_log` (a `CrashLog` instance) is added to `WebDeps` alongside the existing `deps.log_buffer`, constructed once in `main.py` and threaded through the same way.

## 4. Frontend

### `crashlog.js` (new panel → `bridgeLog` zone, alongside `log`)

- On mount: `fetch("/api/crashes")`, render count + entries (timestamp, kind badge, error line; traceback available but collapsed/truncated to keep the panel scannable, matching the existing log panel's plain, dense style).
- No live WebSocket push for this one (crashes are rare; a fresh fetch on mount/panel-visible is enough — avoids adding a new WS message type for something that isn't a streaming concern).

### `power.js` (new panel → `tools` zone, alongside `servos`)

- Two slide-to-confirm controls (Restart, Shutdown), each an `<input type="range" min="0" max="100">` — same primitive the servos panel already uses for angle sliders, styled distinctly (e.g. red-tinted track) to signal destructiveness.
- On `input` event reaching `value === "100"`: fire the POST (`/api/system/restart` or `/api/system/shutdown`), disable the control, show a "Rebooting…" / "Shutting down…" status line.
- On `change` (release) before reaching 100: snap back to 0 (no action fires on a partial drag).

## Testing

- `crashlog.py`: unit tests for `record`/`entries`/`count`/`clear` against a `tmp_path` file — real file I/O, no mocks, matching this codebase's established convention.
- The `set_exception_handler` hook: a test that installs the handler on a real event loop, schedules a task that raises, lets it get garbage-collected/checked, and asserts `crash_log.record` was called with `kind="task"` — plus asserts `loop.default_exception_handler` still ran (e.g. via a spy) so existing journal logging isn't silently dropped.
- `api/system.py`: `aiohttp` test-client style tests (matching `bridge/tests/webapp/`'s existing pattern) with a **fake** subprocess runner injected (never actually invoke `sudo systemctl reboot` in a test) — assert `post_restart` clears the crash log and calls the fake runner with the exact expected command; assert `post_shutdown` does not clear the crash log.
- Frontend: no existing JS test infrastructure in this codebase (confirmed — panels are untested via any JS test runner today), so `power.js`/`crashlog.js` follow that same existing convention (no new JS test tooling introduced for this feature).
