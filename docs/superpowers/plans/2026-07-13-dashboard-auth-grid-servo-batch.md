# Dashboard Auth, Masonry Grid, and Servo Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add password-gated login to the Milo web dashboard, replace the static CSS-grid card layout with a live auto-packing (masonry) layout, and fix the Servo Test "Center All" button so all 8 channels move together via a real batch command.

**Architecture:** Auth is a `hashlib.scrypt`-hashed credential in `BridgeConfig`, an in-memory `SessionStore`/`LoginThrottle`, and an aiohttp middleware gating every route except `/login`/`/api/login`/`/static/*`. The grid becomes a JS-driven bin-packing compaction pass over absolutely-positioned cards, re-run after every layout mutation. The servo fix adds one new WS message type that reuses the driver's existing staggered `set_pose()` instead of 8 separate per-channel round trips.

**Tech Stack:** Python ≥3.11 stdlib only (`hashlib.scrypt`, `hmac`, `secrets` — no new dependency), existing `aiohttp` webapp, vanilla JS (no build step).

**Spec:** `docs/superpowers/specs/2026-07-13-dashboard-auth-grid-servo-batch-design.md`

## Global Constraints

- No new Python dependencies — hashing/sessions use stdlib only (`hashlib.scrypt`, `hmac.compare_digest`, `secrets.token_urlsafe`).
- Default seeded credentials: username `dama`, password `MILO@gate` — seeded into `~/.milo/config.json` only when `web_password_hash` is empty, exactly like the existing `robot_id` auto-seed pattern in `BridgeConfig.load()`.
- Session cookie name `milo_session`, `HttpOnly`, `SameSite=Strict`, `Path=/`, **no `Max-Age`/`Expires`** (dies when the browser closes), **no `Secure` flag** (plain HTTP on LAN — documented, accepted trade-off).
- Auth allow-list (no cookie required): exact paths `/login`, `/api/login`; prefix `/static/`. Everything else requires a valid session.
- Unauthenticated page request → `303 See Other` to `/login`. Unauthenticated `/api/*`, `/ws`, `/stream/camera` → `401 {"error": "unauthorized"}` JSON.
- Login throttle: 5 failed attempts within 60s from the same source IP → refuse further attempts from that IP for 30s.
- `servo_batch` rejects the whole batch (no partial writes) if any channel name is unrecognized; reuses `ServoDriver.set_pose(angles, stagger=True)` — never bypasses the hardware-mandated stagger.
- Grid: 12 logical columns, 80px row unit (unchanged from the existing implementation); below 700px container width, effective columns drop to 2 and each card's `w` is clamped to `min(w, 2)` for that layout pass only (not persisted). `localStorage` key stays `milo.layout.v1`, schema `{order, sizes, hidden}` unchanged.
- Run tests from repo root: `python -m pytest bridge/tests -q`, output pristine (0 warnings). Commit per task, no co-author trailer.

---

### Task 1: Password hashing (`webapp/auth.py`)

**Files:**
- Create: `bridge/milo_bridge/webapp/auth.py`
- Test: `bridge/tests/webapp/test_auth.py`

**Interfaces:**
- Produces: `hash_password(password: str) -> str` (format `"<salt_hex>$<hash_hex>"`), `verify_password(password: str, stored: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_auth.py`:

```python
from milo_bridge.webapp.auth import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    stored = hash_password("MILO@gate")
    assert verify_password("MILO@gate", stored)


def test_wrong_password_rejected():
    stored = hash_password("MILO@gate")
    assert not verify_password("wrong", stored)


def test_same_password_hashes_differently_each_time():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b  # random salt per call
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_hash_format_is_salt_dollar_hash():
    stored = hash_password("x")
    assert stored.count("$") == 1
    salt_hex, hash_hex = stored.split("$")
    assert all(c in "0123456789abcdef" for c in salt_hex)
    assert all(c in "0123456789abcdef" for c in hash_hex)


def test_verify_rejects_malformed_stored_value():
    assert not verify_password("anything", "not-a-valid-stored-hash")
    assert not verify_password("anything", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_bridge.webapp.auth'`

- [ ] **Step 3: Write implementation**

`bridge/milo_bridge/webapp/auth.py`:

```python
"""Password hashing for the dashboard login. Stdlib only (hashlib.scrypt).

Stored format is ``"<salt_hex>$<hash_hex>"`` so the salt travels with the
hash in a single config string, matching how ``~/.milo/config.json``
already stores everything else as plain JSON scalars.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16
_KEY_LEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_LEN)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=len(expected))
    return hmac.compare_digest(candidate, expected)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_auth.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/auth.py bridge/tests/webapp/test_auth.py
git commit -m "feat(web): scrypt password hashing for dashboard login"
```

---

### Task 2: Config fields + seeding

**Files:**
- Modify: `bridge/milo_bridge/config.py`
- Test: `bridge/tests/test_config.py` (create if it doesn't already exist — check first with `Glob` for `bridge/tests/test_config.py`; if it exists, add to it following its existing style instead of overwriting)

**Interfaces:**
- Consumes: `hash_password` from Task 1.
- Produces: `BridgeConfig.web_username: str` (default `"dama"`), `BridgeConfig.web_password_hash: str` (default `""`, seeded on first `load()`).

- [ ] **Step 1: Write the failing test**

Check first whether `bridge/tests/test_config.py` exists. If it does, read it and add the test below in its existing style (same imports, same `tmp_path`-based pattern other tests there use). If it does not exist, create it with just this test:

```python
from milo_bridge.config import BridgeConfig
from milo_bridge.webapp.auth import verify_password


def test_load_seeds_web_credentials_on_first_run(tmp_path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.web_username == "dama"
    assert cfg.web_password_hash != ""
    assert verify_password("MILO@gate", cfg.web_password_hash)

    # Second load reads the saved file back — must NOT re-seed/re-hash.
    cfg2 = BridgeConfig.load(path)
    assert cfg2.web_password_hash == cfg.web_password_hash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest bridge/tests/test_config.py::test_load_seeds_web_credentials_on_first_run -v`
Expected: FAIL — `AttributeError: 'BridgeConfig' object has no attribute 'web_username'`

- [ ] **Step 3: Modify `config.py`**

In `bridge/milo_bridge/config.py`, add two fields to the `BridgeConfig` dataclass, right after the existing `web_port: int = 80` line:

```python
    web_username: str = "dama"
    web_password_hash: str = ""   # scrypt "<salt_hex>$<hash_hex>"; seeded on first load()
```

Then modify `load()` — it currently seeds `robot_id` when empty; add the same pattern for the web password directly below that block:

```python
    @classmethod
    def load(cls, path: Path | None = None) -> "BridgeConfig":
        path = path or DEFAULT_DIR / "config.json"
        if path.exists():
            cfg = cls(**json.loads(path.read_text(encoding="utf-8")))
        else:
            cfg = cls()
        if not cfg.robot_id:
            cfg.robot_id = f"milo-{uuid.uuid4().hex[:12]}"
            cfg.save(path)
        if not cfg.web_password_hash:
            from .webapp.auth import hash_password
            cfg.web_password_hash = hash_password("MILO@gate")
            cfg.save(path)
        return cfg
```

(The `hash_password` import is local to `load()`, not top-level, so `config.py` doesn't gain a hard import-time dependency on `webapp/` — mirrors how the rest of the bridge keeps `config.py` dependency-light.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/test_config.py -v`
Expected: all tests in the file pass, including the new one.

Also run the full config-adjacent suite to confirm no regression: `python -m pytest bridge/tests -q` — all green, 0 warnings.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/config.py bridge/tests/test_config.py
git commit -m "feat(web): seed dashboard login credentials into BridgeConfig on first run"
```

---

### Task 3: SessionStore + LoginThrottle

**Files:**
- Create: `bridge/milo_bridge/webapp/session_auth.py`
- Test: `bridge/tests/webapp/test_session_auth.py`

**Interfaces:**
- Produces: `SessionStore()` with `create(username: str) -> str`, `is_valid(token: str) -> bool`, `revoke(token: str) -> None`. `LoginThrottle(now: Callable[[], float] = time.monotonic)` with `allow(ip: str) -> bool`, `record_failure(ip: str) -> None`, `record_success(ip: str) -> None` (clears any prior failure count for that IP).

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_session_auth.py`:

```python
from milo_bridge.webapp.session_auth import LoginThrottle, SessionStore


def test_session_create_and_validate():
    store = SessionStore()
    token = store.create("dama")
    assert store.is_valid(token)
    assert not store.is_valid("not-a-real-token")


def test_session_revoke():
    store = SessionStore()
    token = store.create("dama")
    store.revoke(token)
    assert not store.is_valid(token)


def test_session_tokens_are_unique():
    store = SessionStore()
    a = store.create("dama")
    b = store.create("dama")
    assert a != b


def test_throttle_allows_under_the_limit():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(4):
        assert throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")


def test_throttle_blocks_at_five_failures_within_60s():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(5):
        assert throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert not throttle.allow("1.2.3.4")


def test_throttle_unblocks_after_cooldown():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(5):
        throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert not throttle.allow("1.2.3.4")
    clock[0] = 31.0  # past the 30s cooldown
    assert throttle.allow("1.2.3.4")


def test_throttle_is_per_ip():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(5):
        throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert not throttle.allow("1.2.3.4")
    assert throttle.allow("5.6.7.8")


def test_throttle_success_clears_failure_count():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(4):
        throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    throttle.record_success("1.2.3.4")
    for _ in range(4):
        assert throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_session_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'milo_bridge.webapp.session_auth'`

- [ ] **Step 3: Write implementation**

`bridge/milo_bridge/webapp/session_auth.py`:

```python
"""In-memory session tokens and per-IP login throttling.

Neither structure is persisted across a bridge restart — a restart simply
requires re-login, which is fine and keeps this simple (no session-store
file, no cleanup-on-boot logic).
"""

from __future__ import annotations

import secrets
import time
from typing import Callable

TOKEN_BYTES = 32
FAILURE_WINDOW_S = 60.0
FAILURE_LIMIT = 5
COOLDOWN_S = 30.0


class SessionStore:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}  # token -> username

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._tokens[token] = username
        return token

    def is_valid(self, token: str) -> bool:
        return token in self._tokens

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)


class LoginThrottle:
    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def allow(self, ip: str) -> bool:
        locked_until = self._locked_until.get(ip)
        if locked_until is not None and self._now() < locked_until:
            return False
        return True

    def record_failure(self, ip: str) -> None:
        now = self._now()
        window_start = now - FAILURE_WINDOW_S
        recent = [t for t in self._failures.get(ip, []) if t >= window_start]
        recent.append(now)
        self._failures[ip] = recent
        if len(recent) >= FAILURE_LIMIT:
            self._locked_until[ip] = now + COOLDOWN_S

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._locked_until.pop(ip, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_session_auth.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/session_auth.py bridge/tests/webapp/test_session_auth.py
git commit -m "feat(web): SessionStore and per-IP LoginThrottle for dashboard login"
```

---

### Task 4: Login/logout API + auth middleware + retrofit every existing route test

**Files:**
- Create: `bridge/milo_bridge/webapp/api/auth.py`
- Modify: `bridge/milo_bridge/webapp/api/__init__.py`
- Modify: `bridge/milo_bridge/webapp/__init__.py`
- Modify: `bridge/tests/webapp/fakes.py`
- Create: `bridge/tests/webapp/client_helpers.py`
- Modify: `bridge/tests/webapp/test_status.py`
- Modify: `bridge/tests/webapp/test_media_endpoints.py`
- Modify: `bridge/tests/webapp/test_graph_api.py`
- Modify: `bridge/tests/webapp/test_logs.py`
- Modify: `bridge/tests/webapp/test_ws.py`
- Test: `bridge/tests/webapp/test_auth_api.py`

**Interfaces:**
- Consumes: `hash_password`/`verify_password` (Task 1), `BridgeConfig.web_username`/`web_password_hash` (Task 2), `SessionStore`/`LoginThrottle` (Task 3).
- Produces: `app["sessions"]: SessionStore`, `app["login_throttle"]: LoginThrottle`, cookie name constant `SESSION_COOKIE = "milo_session"` (exported from `webapp/api/auth.py`), test helper `client_helpers.authed_client(deps) -> TestClient` used by every later test file in this plan and by every existing test file this task retrofits.

**Why this task is large:** the auth middleware gates every route, so it cannot ship in the same commit as anything that still calls the old "build an unauthenticated `TestClient`" pattern — the whole test suite would go red. This task ships the middleware and fixes every call site in the same commit, which is why it touches five existing test files.

- [ ] **Step 1: Add test credentials to the shared fakes**

`bridge/tests/webapp/fakes.py` — add two constants and use them in `make_deps()`. First add the import at the top of the file (with the other `milo_bridge` imports):

```python
from milo_bridge.webapp.auth import hash_password
```

Add near the top of the file, after the imports, before the class definitions:

```python
TEST_USERNAME = "tester"
TEST_PASSWORD = "test-pw-12345"
```

In `make_deps()`, add these two lines to the `BridgeConfig(...)` construction (inside the existing call, as additional keyword arguments):

```python
def make_deps(**overrides) -> WebDeps:
    store = GraphStore(":memory:")
    deps = WebDeps(
        config=BridgeConfig(
            robot_id="milo-test", robot_name="milo",
            web_username=TEST_USERNAME, web_password_hash=hash_password(TEST_PASSWORD),
        ),
        runner=FakeRunner(),
        ...  # rest unchanged
```

(Only the `config=BridgeConfig(...)` line changes; every other line in `make_deps()` stays exactly as it is today.)

- [ ] **Step 2: Write the failing tests for login/logout + middleware**

`bridge/tests/webapp/test_auth_api.py`:

```python
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from .fakes import TEST_PASSWORD, TEST_USERNAME, make_deps


async def _raw_client(deps):
    """A client that has NOT logged in — for testing the gate itself."""
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_unauthenticated_root_redirects_to_login():
    client = await _raw_client(make_deps())
    try:
        resp = await client.get("/", allow_redirects=False)
        assert resp.status == 303
        assert resp.headers["Location"] == "/login"
    finally:
        await client.close()


async def test_unauthenticated_api_returns_401_json():
    client = await _raw_client(make_deps())
    try:
        resp = await client.get("/api/status")
        assert resp.status == 401
        data = await resp.json()
        assert data["error"] == "unauthorized"
    finally:
        await client.close()


async def test_unauthenticated_ws_handshake_rejected():
    client = await _raw_client(make_deps())
    try:
        import aiohttp
        try:
            await client.ws_connect("/ws")
            raised = False
        except aiohttp.WSServerHandshakeError:
            raised = True
        assert raised
    finally:
        await client.close()


async def test_login_page_and_static_reachable_without_auth():
    client = await _raw_client(make_deps())
    try:
        resp = await client.get("/login")
        assert resp.status == 200
        resp2 = await client.get("/static/js/bus.js")
        assert resp2.status == 200
    finally:
        await client.close()


async def test_login_wrong_password_fails():
    client = await _raw_client(make_deps())
    try:
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": "wrong"})
        assert resp.status == 200
        data = await resp.json()
        assert data["error"] == "invalid credentials"
        assert "milo_session" not in client.session.cookie_jar.filter_cookies("http://127.0.0.1")
    finally:
        await client.close()


async def test_login_correct_password_sets_cookie_and_unlocks_api():
    client = await _raw_client(make_deps())
    try:
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert resp.status == 200
        data = await resp.json()
        assert data == {"ok": True}
        # cookie now present; subsequent requests on the same client succeed
        resp2 = await client.get("/api/status")
        assert resp2.status == 200
    finally:
        await client.close()


async def test_logout_revokes_session():
    client = await _raw_client(make_deps())
    try:
        await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert (await client.get("/api/status")).status == 200
        await client.post("/api/logout")
        assert (await client.get("/api/status")).status == 401
    finally:
        await client.close()


async def test_login_throttled_after_five_failures():
    client = await _raw_client(make_deps())
    try:
        for _ in range(5):
            await client.post("/api/login", json={"username": TEST_USERNAME, "password": "wrong"})
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert resp.status == 429
    finally:
        await client.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_auth_api.py -q`
Expected: FAIL — every test either 404s (`/api/login` doesn't exist) or gets 200 instead of 303/401 (no middleware yet).

- [ ] **Step 4: Write `webapp/api/auth.py`**

```python
"""Login/logout endpoints for the dashboard's session cookie."""

from __future__ import annotations

from aiohttp import web

from ..auth import verify_password

SESSION_COOKIE = "milo_session"


def _client_ip(request: web.Request) -> str:
    peername = request.transport.get_extra_info("peername") if request.transport else None
    return peername[0] if peername else "unknown"


async def post_login(request: web.Request) -> web.Response:
    app = request.app
    deps = app["deps"]
    throttle = app["login_throttle"]
    ip = _client_ip(request)
    if not throttle.allow(ip):
        return web.json_response({"error": "too many attempts, try again shortly"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid request"}, status=400)
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    ok = username == deps.config.web_username and verify_password(password, deps.config.web_password_hash)
    if not ok:
        throttle.record_failure(ip)
        return web.json_response({"error": "invalid credentials"})
    throttle.record_success(ip)
    token = app["sessions"].create(username)
    resp = web.json_response({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", path="/")
    return resp


async def post_logout(request: web.Request) -> web.Response:
    app = request.app
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        app["sessions"].revoke(token)
    resp = web.json_response({"ok": True})
    resp.del_cookie(SESSION_COOKIE, path="/")
    return resp


def register(app: web.Application) -> None:
    app.router.add_post("/api/login", post_login)
    app.router.add_post("/api/logout", post_logout)
```

- [ ] **Step 5: Register the auth routes**

`bridge/milo_bridge/webapp/api/__init__.py` — add `auth` to the import and call it first (so login/logout are registered before anything else, matching the order other modules already follow):

```python
"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import auth, graph, logs, media, motion_meta, speak, status


def register_routes(app: web.Application) -> None:
    auth.register(app)
    status.register(app)
    media.register(app)
    speak.register(app)
    graph.register(app)
    motion_meta.register(app)
    logs.register(app)
```

- [ ] **Step 6: Add the auth middleware and `/login` route to `webapp/__init__.py`**

Replace the full contents of `bridge/milo_bridge/webapp/__init__.py` with:

```python
"""Milo web dashboard: aiohttp app factory."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .api import register_routes
from .api.auth import SESSION_COOKIE
from .deps import WebDeps
from .session_auth import LoginThrottle, SessionStore

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

_AUTH_ALLOWLIST_PATHS = {"/login", "/api/login"}
_JSON_401_PATHS_PREFIXES = ("/api/",)
_JSON_401_EXACT_PATHS = {"/ws", "/stream/camera"}


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _login_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "login.html")


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    path = request.path
    if path in _AUTH_ALLOWLIST_PATHS or path.startswith("/static/"):
        return await handler(request)
    token = request.cookies.get(SESSION_COOKIE)
    sessions: SessionStore = request.app["sessions"]
    if token and sessions.is_valid(token):
        return await handler(request)
    if path.startswith(_JSON_401_PATHS_PREFIXES) or path in _JSON_401_EXACT_PATHS:
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.HTTPSeeOther(location="/login")


@web.middleware
async def _json_error_middleware(request: web.Request, handler):
    """Return JSON errors for /api/* requests instead of aiohttp's HTML pages."""
    if not request.path.startswith("/api/"):
        return await handler(request)
    try:
        return await handler(request)
    except web.HTTPException as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    except Exception:
        log.exception("unhandled error in %s %s", request.method, request.path)
        return web.json_response({"error": "internal error"}, status=500)


def create_app(deps: WebDeps) -> web.Application:
    app = web.Application(
        client_max_size=2 * 1024 * 1024,
        middlewares=[_auth_middleware, _json_error_middleware],
    )
    app["deps"] = deps
    app["ws_clients"] = set()
    app["sessions"] = SessionStore()
    app["login_throttle"] = LoginThrottle()
    register_routes(app)
    from .ws import register_ws
    register_ws(app)
    if deps.log_buffer is not None:
        from .ws import broadcast_json
        deps.log_buffer.on_line = lambda line: broadcast_json(app, {"t": "log", "line": line})
    app.router.add_get("/", _index)
    app.router.add_get("/login", _login_page)
    app.router.add_static("/static", STATIC_DIR)
    return app
```

- [ ] **Step 7: Add the shared authenticated-test-client helper**

`bridge/tests/webapp/client_helpers.py`:

```python
"""Shared test helper: an aiohttp TestClient that's already logged in.

Every route in the real app is gated by the auth middleware, so every test
that exercises an HTTP/WS route needs a client that has already completed
the login handshake. aiohttp's TestClient keeps a cookie jar for its
lifetime, so logging in once here makes every subsequent request/ws_connect
on the same client carry the session cookie automatically.
"""

from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app

from .fakes import TEST_PASSWORD, TEST_USERNAME


async def authed_client(deps) -> TestClient:
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body == {"ok": True}, body
    return client
```

- [ ] **Step 8: Retrofit `test_status.py`**

In `bridge/tests/webapp/test_status.py`, replace the local `_client` helper and its import. Change:

```python
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from .fakes import make_deps


async def _client(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client
```

to:

```python
from .client_helpers import authed_client
from .fakes import make_deps


_client = authed_client
```

Every call site in the file (`client = await _client(deps)`) is unchanged — this is a one-block edit at the top of the file. No test body changes.

- [ ] **Step 9: Retrofit `test_media_endpoints.py`**

Same pattern. Change:

```python
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.media_hub import MediaHub
from .fakes import FakeCamera, make_deps


async def _client(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client
```

to:

```python
import asyncio

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.media_hub import MediaHub
from .client_helpers import authed_client
from .fakes import FakeCamera, make_deps


_client = authed_client
```

- [ ] **Step 10: Retrofit `test_graph_api.py`**

Change:

```python
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from .fakes import make_deps


async def _client(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client
```

to:

```python
from .client_helpers import authed_client
from .fakes import make_deps


_client = authed_client
```

- [ ] **Step 11: Retrofit `test_logs.py`**

Change the imports:

```python
import logging

from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from milo_bridge.webapp.logbuf import RingBufferLogHandler
from .fakes import make_deps
```

to:

```python
import logging

from milo_bridge.webapp.logbuf import RingBufferLogHandler
from .client_helpers import authed_client
from .fakes import make_deps
```

And in `test_logs_endpoint`, change:

```python
    deps = make_deps(log_buffer=h)
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
```

to:

```python
    deps = make_deps(log_buffer=h)
    client = await authed_client(deps)
```

- [ ] **Step 12: Retrofit `test_ws.py`**

Change:

```python
import asyncio
import json

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.media_hub import MediaHub
from .fakes import FakeAudio, make_deps


async def _ws(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    ws = await client.ws_connect("/ws")
    return client, ws
```

to:

```python
import asyncio
import json

import aiohttp

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.media_hub import MediaHub
from .client_helpers import authed_client
from .fakes import FakeAudio, make_deps


async def _ws(deps):
    client = await authed_client(deps)
    ws = await client.ws_connect("/ws")
    return client, ws
```

- [ ] **Step 13: Run every retrofitted test file plus the new auth tests**

Run: `python -m pytest bridge/tests/webapp/test_auth_api.py bridge/tests/webapp/test_status.py bridge/tests/webapp/test_media_endpoints.py bridge/tests/webapp/test_graph_api.py bridge/tests/webapp/test_logs.py bridge/tests/webapp/test_ws.py -q`
Expected: all pass, 0 warnings.

- [ ] **Step 14: Run the full suite**

Run: `python -m pytest bridge/tests -q`
Expected: all pass, 0 warnings (this confirms no other test file anywhere in the bridge package builds a raw `TestClient` against the real app — if one is found failing here that step 8-12 didn't cover, add the same one-block retrofit to it before proceeding).

- [ ] **Step 15: Commit**

```bash
git add bridge/milo_bridge/webapp bridge/tests/webapp
git commit -m "feat(web): login/logout API, auth middleware gating every route, retrofit test clients"
```

---

### Task 5: Login frontend

**Files:**
- Create: `bridge/milo_bridge/webapp/static/login.html`
- Create: `bridge/milo_bridge/webapp/static/js/login.js`
- Modify: `bridge/milo_bridge/webapp/static/index.html`
- Modify: `bridge/milo_bridge/webapp/static/js/main.js`
- Modify: `bridge/tests/webapp/test_static_integrity.py`

**Interfaces:**
- Consumes: `POST /api/login`, `POST /api/logout` (Task 4).
- Produces: nothing consumed by later tasks — leaf UI.

- [ ] **Step 1: Extend the static-integrity test**

Read `bridge/tests/webapp/test_static_integrity.py` first (it currently checks files referenced by `index.html` and imports in `registry.js`). Add one more test function to the same file, following its existing style:

```python
def test_login_page_references_exist():
    html = (STATIC / "login.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert (STATIC / ref).exists(), f"login.html references missing {ref}"
```

(Reuse whatever `STATIC`/`re` names the existing file already imports/defines at module level — do not redefine them if they already exist in the file.)

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest bridge/tests/webapp/test_static_integrity.py::test_login_page_references_exist -v`
Expected: FAIL — `login.html` doesn't exist yet.

- [ ] **Step 3: Write `static/login.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MILO — Sign in</title>
<link rel="stylesheet" href="/static/css/theme.css">
<style>
  body { display: flex; align-items: center; justify-content: center; min-height: 100%; }
  .login-box {
    width: 280px; padding: 24px; border: 1px solid var(--line);
    border-radius: 8px; background: var(--surface);
  }
  .login-box h1 {
    font-size: 14px; letter-spacing: 0.18em; text-transform: uppercase;
    margin: 0 0 18px;
  }
  .login-box label { display: block; font-size: 12px; color: var(--muted); margin: 12px 0 4px; }
  .login-box input { width: 100%; }
  .login-box button { width: 100%; margin-top: 18px; }
  #login-error { color: var(--danger); font-size: 12px; min-height: 16px; margin-top: 10px; }
</style>
</head>
<body>
<div class="login-box">
  <h1>MILO</h1>
  <form id="login-form">
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password">
    <button class="btn" type="submit">Sign in</button>
    <div id="login-error"></div>
  </form>
</div>
<script type="module" src="/static/js/login.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `static/js/login.js`**

```js
const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  errorBox.textContent = "";
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  let data;
  try {
    const resp = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    data = await resp.json();
  } catch {
    errorBox.textContent = "network error — is the robot reachable?";
    return;
  }
  if (data.error) {
    errorBox.textContent = data.error;
    document.getElementById("password").value = "";
    return;
  }
  location.href = "/";
});
```

- [ ] **Step 5: Add the Logout button**

In `bridge/milo_bridge/webapp/static/index.html`, add one button to the header, right after the existing `#btn-stop` button:

```html
  <button id="btn-stop" class="btn danger">STOP</button>
  <button id="btn-logout" class="btn ghost">Logout</button>
```

- [ ] **Step 6: Wire the Logout button**

In `bridge/milo_bridge/webapp/static/js/main.js`, add this block right after the existing `document.getElementById("btn-stop").onclick = () => bus.send({ t: "stop" });` line:

```js
document.getElementById("btn-logout").onclick = async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
};
```

- [ ] **Step 7: Run the integrity test and the full suite**

Run: `python -m pytest bridge/tests/webapp/test_static_integrity.py -q`
Expected: all pass, including the new `test_login_page_references_exist`.

Run: `python -m pytest bridge/tests -q`
Expected: all green, 0 warnings.

- [ ] **Step 8: Commit**

```bash
git add bridge/milo_bridge/webapp/static bridge/tests/webapp/test_static_integrity.py
git commit -m "feat(web): login page, logout button"
```

---

### Task 6: Servo batch command

**Files:**
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Modify: `bridge/milo_bridge/webapp/static/js/cards/servos.js`
- Test: `bridge/tests/webapp/test_motion.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Consumes: `SERVO_CHANNELS` (existing import in `motion.py`), `ServoDriver.set_pose(angles: Mapping[str, float], stagger: bool = True)` (existing driver method).
- Produces: `MotionService.servo_batch(client_id: str, angles: dict[str, float]) -> dict`; WS message type `"servo_batch"` with body `{"angles": {...}}`.

- [ ] **Step 1: Write the failing test for `MotionService.servo_batch`**

Add to `bridge/tests/webapp/test_motion.py` (append; keep every existing test in the file unchanged):

```python
async def test_servo_batch_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.servo_batch("nobody", {"R1": 90})
    assert res == {"error": "not-controlling"}
    assert deps.servos.angles == {}


async def test_servo_batch_writes_all_channels_in_one_call():
    deps = _controlled_deps()
    svc = MotionService(deps)
    angles = {"R1": 90, "R2": 90, "L1": 45, "L4": 120}
    assert await svc.servo_batch("c1", angles) == {"ok": True}
    assert deps.servos.angles == angles


async def test_servo_batch_clamps_every_angle():
    deps = _controlled_deps()
    svc = MotionService(deps)
    await svc.servo_batch("c1", {"R1": 400, "R2": -20})
    assert deps.servos.angles == {"R1": 180, "R2": 0}


async def test_servo_batch_rejects_whole_batch_on_unknown_channel():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.servo_batch("c1", {"R1": 90, "R9": 90})
    assert "error" in res
    assert deps.servos.angles == {}  # no partial write
```

(These append to the file that already imports `ControlBroker`, `MotionService`, `make_deps`, and already defines `_controlled_deps()` — reuse them, do not redefine.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_motion.py -k servo_batch -v`
Expected: FAIL — `AttributeError: 'MotionService' object has no attribute 'servo_batch'`

- [ ] **Step 3: Implement `servo_batch` in `motion.py`**

Add this method to the `MotionService` class in `bridge/milo_bridge/webapp/motion.py`, directly after the existing `servo()` method:

```python
    async def servo_batch(self, client_id: str, angles: dict[str, float]) -> dict:
        if err := self._denied(client_id):
            return err
        bad = [name for name in angles if name not in SERVO_CHANNELS]
        if bad:
            return {"error": f"unknown servo(s) {bad!r}"}
        clamped = {name: _clamp(deg, DEG_MIN, DEG_MAX) for name, deg in angles.items()}
        try:
            await self._deps.servos.set_pose(clamped, stagger=True)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_motion.py -q`
Expected: all pass (previous tests + 4 new ones), 0 warnings.

- [ ] **Step 5: Add the WS dispatch test**

Add to `bridge/tests/webapp/test_ws.py` (append; keep every existing test unchanged):

```python
async def test_servo_batch_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "servo_batch", "angles": {"R1": 90, "L4": 90}})
        await _recv_json_until(ws, "ack")
        assert deps.servos.angles == {"R1": 90, "L4": 90}
    finally:
        await client.close()
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest bridge/tests/webapp/test_ws.py::test_servo_batch_dispatch -v`
Expected: FAIL — `{"t": "err", "for": "servo_batch", "error": "unknown-type"}` (no dispatch entry yet).

- [ ] **Step 7: Wire the dispatch in `ws.py`**

In `bridge/milo_bridge/webapp/ws.py`, in `_handle_text`, add one line to the `handlers` dict (which currently has `gait`, `pose`, `face`, `servo`):

```python
    handlers = {
        "gait": lambda: motion.gait(client_id, data.get("vx", 0), data.get("vy", 0), data.get("yaw", 0)),
        "pose": lambda: motion.pose(client_id, data.get("name", "")),
        "face": lambda: motion.face(client_id, data.get("name", "")),
        "servo": lambda: motion.servo(client_id, data.get("servo", ""), data.get("deg", 90)),
        "servo_batch": lambda: motion.servo_batch(client_id, data.get("angles", {})),
    }
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_ws.py -q`
Expected: all pass, 0 warnings.

- [ ] **Step 9: Update the Servo Test card's Center All button**

In `bridge/milo_bridge/webapp/static/js/cards/servos.js`, replace the `#center` button's `onclick` handler. Current:

```js
    el.querySelector("#center").onclick = () => SERVOS.forEach((s) => {
      const sl = el.querySelector(`[data-servo="${s}"]`);
      sl.value = 90; sl.oninput();
    });
```

Replace with:

```js
    el.querySelector("#center").onclick = () => {
      const angles = {};
      SERVOS.forEach((s) => {
        const sl = el.querySelector(`[data-servo="${s}"]`);
        sl.value = 90;
        el.querySelector(`[data-val="${s}"]`).textContent = "90°";
        angles[s] = 90;
      });
      bus.send({ t: "servo_batch", angles });
    };
```

- [ ] **Step 10: Run the full suite**

Run: `python -m pytest bridge/tests -q`
Expected: all green, 0 warnings.

- [ ] **Step 11: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/webapp/ws.py bridge/milo_bridge/webapp/static/js/cards/servos.js bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py
git commit -m "feat(web): servo_batch command — Center All moves all 8 channels in one staggered write"
```

---

### Task 7: Masonry auto-pack grid

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/grid.js`
- Modify: `bridge/milo_bridge/webapp/static/css/grid.css`
- Modify: `docs/WEB-DASHBOARD.md`

**Interfaces:**
- Consumes: same public shape as before — `initGrid(container, cards, bus)`; same `localStorage["milo.layout.v1"]` schema `{order, sizes, hidden}`.
- Produces: nothing consumed by later tasks — this is the last task in the plan.

No Python tests apply here (pure frontend layout logic; this repo has no JS test harness, per the existing convention established across Tasks 8-10 of the original dashboard plan — verification is the manual smoke checklist added to the docs in Step 3 below, plus the static-integrity test already covering that `grid.js` exists and is imported correctly, which is untouched by this task).

- [ ] **Step 1: Replace `grid.css`**

Replace the full contents of `bridge/milo_bridge/webapp/static/css/grid.css`:

```css
#grid {
  position: relative;
  padding: 12px;
}
.card {
  position: absolute;
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  display: flex; flex-direction: column; overflow: hidden;
  transition: left 0.15s ease, top 0.15s ease, width 0.15s ease, height 0.15s ease;
}
.card.dragging, .card.resizing { transition: none; opacity: 0.85; z-index: 10; }
.card.dragging { opacity: 0.55; outline: 2px dashed var(--muted); }
.card.drop-target { outline: 2px solid var(--ink); }
.card-head {
  display: flex; align-items: center; gap: 8px; padding: 6px 10px;
  border-bottom: 1px solid var(--line); cursor: grab; user-select: none;
  font-weight: 600; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
}
.card-head .close { margin-left: auto; cursor: pointer; color: var(--muted); background: none; border: none; font: inherit; }
.card-head .close:hover { color: var(--danger); }
.card-body { flex: 1; padding: 10px; overflow: auto; position: relative; }
.card .resize {
  position: absolute; right: 2px; bottom: 2px; width: 14px; height: 14px;
  cursor: nwse-resize; border-right: 2px solid var(--muted); border-bottom: 2px solid var(--muted);
}
.card.locked .card-body { opacity: 0.45; pointer-events: none; }
.card.locked::after {
  content: "take control to use"; position: absolute; bottom: 8px; left: 10px;
  font-size: 11px; color: var(--muted);
}
.unavail { color: var(--muted); font-style: italic; }
```

(Removed the old `display: grid`/`grid-template-columns`/media-query rules — column count and the narrow-viewport 2-column behavior are now computed in `grid.js` itself, not CSS, since card positions are JS-driven pixels.)

- [ ] **Step 2: Replace `grid.js`**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/grid.js`:

```js
// Masonry-style auto-packing card dashboard: drag to reorder, corner-resize,
// live compaction, persistence.
const KEY = "milo.layout.v1";
const ROW_PX = 80;
const NARROW_PX = 700;
const FULL_COLUMNS = 12;
const NARROW_COLUMNS = 2;
const MAX_W = 12;
const MAX_H = 10;
const MIN_W = 2;
const MIN_H = 2;

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function saveLayout(layout) { localStorage.setItem(KEY, JSON.stringify(layout)); }

// Bin-packs cards into the first legal top-left position, in `order`
// sequence, using `columns` logical columns. Returns Map<id, {x,y,w,h}>.
function compact(order, sizes, cardById, columns) {
  const placed = [];
  const positions = new Map();
  for (const id of order) {
    const card = cardById.get(id);
    if (!card) continue;
    const size = sizes[id] || { w: card.w, h: card.h };
    const w = Math.min(Math.max(size.w, MIN_W), Math.min(MAX_W, columns));
    const h = Math.min(Math.max(size.h, MIN_H), MAX_H);
    let bestX = 0, bestY = 0;
    outer:
    for (let y = 0; ; y++) {
      for (let x = 0; x <= columns - w; x++) {
        const overlaps = placed.some((p) =>
          x < p.x + p.w && x + w > p.x && y < p.y + p.h && y + h > p.y);
        if (!overlaps) { bestX = x; bestY = y; break outer; }
      }
    }
    placed.push({ x: bestX, y: bestY, w, h });
    positions.set(id, { x: bestX, y: bestY, w, h });
  }
  return positions;
}

export function initGrid(container, cards, bus) {
  const layout = loadLayout();
  layout.order = (layout.order || []).filter((id) => cards.some((c) => c.id === id));
  for (const c of cards) if (!layout.order.includes(c.id)) layout.order.push(c.id);
  layout.sizes = layout.sizes || {};
  layout.hidden = layout.hidden || [];

  const cardById = new Map(cards.map((c) => [c.id, c]));
  const shells = new Map();

  function columns() {
    return container.clientWidth < NARROW_PX ? NARROW_COLUMNS : FULL_COLUMNS;
  }

  function applyPositions() {
    const cols = columns();
    const cellPx = container.clientWidth / cols;
    const positions = compact(
      layout.order.filter((id) => !layout.hidden.includes(id)),
      layout.sizes, cardById, cols,
    );
    let maxBottom = 0;
    for (const [id, pos] of positions) {
      const el = shells.get(id);
      if (!el) continue;
      el.style.left = `${pos.x * cellPx}px`;
      el.style.top = `${pos.y * ROW_PX}px`;
      el.style.width = `${pos.w * cellPx}px`;
      el.style.height = `${pos.h * ROW_PX}px`;
      maxBottom = Math.max(maxBottom, (pos.y + pos.h) * ROW_PX);
    }
    container.style.height = `${maxBottom + 24}px`;
  }

  function render() {
    container.innerHTML = "";
    shells.clear();
    for (const id of layout.order) {
      if (layout.hidden.includes(id)) continue;
      const card = cardById.get(id);
      const el = buildShell(card);
      shells.set(id, el);
      container.appendChild(el);
    }
    applyPositions();
    updateLocks();
  }

  function buildShell(card) {
    const el = document.createElement("section");
    el.className = "card";
    el.dataset.id = card.id;
    el.innerHTML = `<div class="card-head"><span>${card.title}</span>
      <button class="close" title="Hide card">✕</button></div>
      <div class="card-body"></div><div class="resize"></div>`;
    el.querySelector(".close").onclick = () => {
      layout.hidden.push(card.id); saveLayout(layout); render();
    };
    wireDrag(el);
    wireResize(el, card);
    card.mount(el.querySelector(".card-body"), { bus });
    return el;
  }

  // -- drag to reorder ------------------------------------------------------
  let dragId = null;
  function wireDrag(el) {
    const head = el.querySelector(".card-head");
    head.addEventListener("pointerdown", (e) => {
      if (e.target.classList.contains("close")) return;
      dragId = el.dataset.id; el.classList.add("dragging");
      const move = (ev) => {
        const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".card");
        document.querySelectorAll(".card.drop-target").forEach((c) => c.classList.remove("drop-target"));
        if (over && over.dataset.id !== dragId) over.classList.add("drop-target");
      };
      const up = (ev) => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        el.classList.remove("dragging");
        const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".card");
        document.querySelectorAll(".card.drop-target").forEach((c) => c.classList.remove("drop-target"));
        if (over && over.dataset.id !== dragId) {
          const from = layout.order.indexOf(dragId);
          const to = layout.order.indexOf(over.dataset.id);
          layout.order.splice(from, 1);
          layout.order.splice(to, 0, dragId);
          saveLayout(layout); render();
        }
        dragId = null;
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  // -- corner resize: live-recompacts on every pointermove ------------------
  function wireResize(el, card) {
    const handle = el.querySelector(".resize");
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.classList.add("resizing");
      const start = { x: e.clientX, y: e.clientY };
      const cellPx = container.clientWidth / columns();
      const startSize = layout.sizes[card.id] || { w: card.w, h: card.h };
      const move = (ev) => {
        const w = Math.max(MIN_W, Math.min(MAX_W, startSize.w + Math.round((ev.clientX - start.x) / cellPx)));
        const h = Math.max(MIN_H, Math.min(MAX_H, startSize.h + Math.round((ev.clientY - start.y) / ROW_PX)));
        layout.sizes[card.id] = { w, h };
        applyPositions();
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        el.classList.remove("resizing");
        saveLayout(layout);
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  // -- control locking ------------------------------------------------------
  function updateLocks() {
    for (const card of cards) {
      const el = shells.get(card.id);
      if (el && card.needsControl) el.classList.toggle("locked", !bus.controlled);
    }
  }
  bus.on("control", updateLocks);
  bus.on("_close", updateLocks);

  // -- responsive reflow ------------------------------------------------------
  window.addEventListener("resize", applyPositions);

  // -- header helpers -------------------------------------------------------
  const menu = document.getElementById("add-menu");
  document.getElementById("btn-add").onclick = () => {
    menu.classList.toggle("hidden");
    menu.innerHTML = "";
    const hidden = layout.hidden;
    if (!hidden.length) menu.innerHTML = "<button disabled>all cards shown</button>";
    for (const id of [...hidden]) {
      const card = cardById.get(id);
      const b = document.createElement("button");
      b.textContent = card.title;
      b.onclick = () => {
        layout.hidden = layout.hidden.filter((x) => x !== id);
        saveLayout(layout); menu.classList.add("hidden"); render();
      };
      menu.appendChild(b);
    }
  };
  document.getElementById("btn-reset").onclick = () => {
    localStorage.removeItem(KEY); location.reload();
  };

  render();
}
```

- [ ] **Step 3: Extend the manual smoke checklist in `docs/WEB-DASHBOARD.md`**

Read the existing "Manual smoke checklist" section (§7, the last section of the file) first. Add these items to the end of the existing checklist (keep every existing bullet exactly as-is):

```markdown
- [ ] Open `http://localhost:8080` logged out (clear cookies or use a
      private window) → redirected to `/login`. Enter the wrong password
      → inline error, still on `/login`. Enter `dama` / `MILO@gate`
      (the seeded default) → lands on the dashboard. Close and reopen the
      browser (not just the tab) → logged out again, since the session
      cookie has no expiry and dies with the browser.
- [ ] Click **Logout** in the header → back on `/login`; reloading `/`
      directly stays on `/login` until you sign in again.
- [ ] On the Servo Test card, drag one servo's slider to make its card
      taller, or resize the card itself bigger from its corner handle:
      watch the cards after it in the layout order smoothly slide into
      their next free slot *while you're still dragging*, not just after
      you release. No two cards should ever overlap, and there should be
      no dead gap a card could have filled.
- [ ] Click **Center All (90°)** on the Servo Test card: all 8 sliders
      move to 90° together, not in a visible left-to-right sequence.
```

- [ ] **Step 4: Manual verification**

Run: `python bridge/tools/webdev.py`

Open `http://localhost:8080` and walk through the checklist items just added (steps 1-4 above), plus a general pass: resize several cards to different sizes, drag-reorder a few, hide and re-show one via **+ Card**, and confirm **Reset layout** (⟲) returns to the default packed arrangement. Also resize the actual browser window narrower than ~700px and confirm cards collapse to a clean 2-column stack without overlaps.

- [ ] **Step 5: Run the full Python suite one more time**

Run: `python -m pytest bridge/tests -q`
Expected: all green, 0 warnings (this task touches no Python files, so this is a final regression confirmation before the branch-level review).

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/grid.js bridge/milo_bridge/webapp/static/css/grid.css docs/WEB-DASHBOARD.md
git commit -m "feat(web): masonry auto-pack grid — live reflow on resize, no gaps or overlaps"
```

---

## Self-review notes

- **Spec coverage:** password hashing + config seeding (T1-T2); sessions + throttle (T3); login/logout API + middleware gating every route, including the necessary retrofit of every pre-existing test client (T4) — this last part isn't explicitly in the spec's task list but is a direct, unavoidable consequence of "everything requires a valid session" and is called out in the spec's own middleware section; login page + logout button (T5); servo_batch end to end, backend and frontend (T6); masonry grid algorithm, live-resize reflow, responsive narrow-viewport behavior, and the manual smoke checklist additions (T7).
- **Deviation from spec:** the spec's pseudocode for `_auth_middleware` used `raise web.HTTPSeeOther`/relied on `_json_error_middleware` to convert an auth-raised `HTTPException` to JSON for API paths; the plan instead has `_auth_middleware` construct and `return` the exact response itself (JSON 401 or a 303 redirect) rather than raising, because raising from the *outer* middleware in the `[_auth_middleware, _json_error_middleware]` chain would bypass `_json_error_middleware` entirely (it only wraps calls to the handler passed *into* it, not exceptions raised by middleware ahead of it in the chain). This is a corrected implementation detail, not a behavior change — the documented external behavior (303 for pages, 401 JSON for API/WS/stream) is identical to the spec.
- **Type/interface consistency:** `MotionService.servo_batch` signature (`client_id: str, angles: dict[str, float]) -> dict`) matches between T6's implementation and its ws.py dispatch lambda; `authed_client(deps) -> TestClient` (T4) is imported with the identical name and signature by every test file in T4, T6, and would be available to any future test file needing an authenticated client; `SESSION_COOKIE` is defined once in `webapp/api/auth.py` and imported (not redefined) by `webapp/__init__.py`.
