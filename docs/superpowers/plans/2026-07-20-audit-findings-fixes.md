# Audit Findings Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close five findings from the graphify security/quality audit of MILO-Robot: the shipped default dashboard password, a wrong HTTP status on failed login, an unbounded/uncaught `?limit` query param, an MCP-token mint that can clobber an existing paired brain's token plus a world-readable secrets file, and two pieces of documentation that no longer match the code (the `seq` field's docstring claim, and dead `face`/`move` keys in the LLM JSON-parse fallback).

**Architecture:** Each task is an isolated, independently testable fix in its own module — no shared new abstractions, no new files except the plan itself. Tasks 1–4 add or change a test first (TDD), then the minimal code to pass it. Task 5 is docs-only (module docstrings), verified by grep instead of pytest.

**Tech Stack:** Python 3.13, pytest (`pytest-asyncio` auto mode — test functions are plain `async def` with no marker, matching this repo's existing tests), aiohttp (`bridge` webapp), Starlette (`bridge` MCP auth), stdlib `secrets`/`os`/`hashlib`.

## Global Constraints

- Match existing test file conventions exactly: bare `async def test_...()` (no `@pytest.mark.asyncio`), `tmp_path` fixture for filesystem isolation, existing fixture/helper modules (`bridge/tests/webapp/fakes.py`, `bridge/tests/webapp/client_helpers.py`).
- No new dependencies.
- No new files except this plan — every task modifies existing modules and existing (or sibling) test files.
- Run each task's test file (not the whole suite) after each change; run the full affected package's suite before the final commit of that task.
- Commit after each task, not after each step.

---

### Task 1: Kill the hardcoded default dashboard password

**Files:**
- Modify: `bridge/milo_bridge/config.py:1-20` (imports), `bridge/milo_bridge/config.py:74-78` (`BridgeConfig.load`)
- Test: `bridge/tests/test_config.py:8-17` (`test_load_seeds_web_credentials_on_first_run`)

**Interfaces:**
- Consumes: `bridge/milo_bridge/webapp/auth.py`'s existing `hash_password(password: str) -> str` and `verify_password(password: str, stored: str) -> bool` (unchanged).
- Produces: no new public names. `BridgeConfig.load()` still sets `cfg.web_password_hash` on first run; the seeded plaintext password is no longer a fixed string — it's logged once via the module's existing `log` logger (`logging.getLogger(__name__)`, already defined at `config.py:17`) at `WARNING` level instead of being silently baked in.

Every robot currently ships with username `dama` / password `MILO@gate` hardcoded in source (`config.py:77`), and there is no way to change it short of hand-editing the config file. This replaces the fixed string with a random per-robot password, generated once and printed to the log the operator already watches (`journalctl -u milo-bridge` per `milo-dashboard/milo_dashboard/collectors/services.py`), mirroring the existing `mint_mcp_token` convention of "printed once for the operator to paste."

- [ ] **Step 1: Write the failing test**

Replace the body of `test_load_seeds_web_credentials_on_first_run` in `bridge/tests/test_config.py`:

```python
def test_load_seeds_web_credentials_on_first_run(tmp_path, caplog):
    path = tmp_path / "config.json"
    with caplog.at_level("WARNING"):
        cfg = BridgeConfig.load(path)
    assert cfg.web_username == "dama"
    assert cfg.web_password_hash != ""

    # The generated password is logged once so the operator can log in.
    warning_text = "\n".join(r.message for r in caplog.records)
    assert "generated" in warning_text.lower()
    import re
    match = re.search(r"password[^:]*:\s*(\S+)", warning_text)
    assert match, f"no password found in log output: {warning_text!r}"
    generated_password = match.group(1)
    assert verify_password(generated_password, cfg.web_password_hash)

    # It must not be the old hardcoded default.
    assert not verify_password("MILO@gate", cfg.web_password_hash)

    # Second load reads the saved file back — must NOT re-seed/re-hash/re-log.
    caplog.clear()
    with caplog.at_level("WARNING"):
        cfg2 = BridgeConfig.load(path)
    assert cfg2.web_password_hash == cfg.web_password_hash
    assert not any("generated" in r.message.lower() for r in caplog.records)


def test_load_seeds_a_different_password_per_config(tmp_path):
    cfg_a = BridgeConfig.load(tmp_path / "a" / "config.json")
    cfg_b = BridgeConfig.load(tmp_path / "b" / "config.json")
    assert cfg_a.web_password_hash != cfg_b.web_password_hash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/test_config.py -v`
Expected: `test_load_seeds_web_credentials_on_first_run` FAILS — `verify_password("MILO@gate", cfg.web_password_hash)` is currently `True`, so the `assert not verify_password(...)` line fails (or the earlier `"generated" in warning_text` assertion fails since nothing is logged yet).

- [ ] **Step 3: Write minimal implementation**

In `bridge/milo_bridge/config.py`, add `secrets` to the stdlib imports (currently `json`, `logging`, `uuid`):

```python
import json
import logging
import secrets
import uuid
```

Replace the password-seeding block at `config.py:75-78`:

```python
        if not cfg.web_password_hash:
            from .webapp.auth import hash_password
            cfg.web_password_hash = hash_password("MILO@gate")
            cfg.save(path)
```

with:

```python
        if not cfg.web_password_hash:
            from .webapp.auth import hash_password
            password = secrets.token_urlsafe(12)
            cfg.web_password_hash = hash_password(password)
            log.warning(
                "no dashboard password was set -- generated one for user %r: %s "
                "(shown once here; log in and note it down)",
                cfg.web_username, password,
            )
            cfg.save(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/test_config.py -v`
Expected: PASS (all tests in the file, including the two new/changed ones).

- [ ] **Step 5: Run the full bridge config-adjacent suite to check for fallout**

Run: `cd bridge && python -m pytest tests/test_config.py tests/webapp/test_auth_api.py tests/webapp/test_auth.py -v`
Expected: PASS. (`webapp/tests` build `BridgeConfig` directly with an explicit `web_password_hash=hash_password(TEST_PASSWORD)` via `bridge/tests/webapp/fakes.py:206-211`, so they never go through `BridgeConfig.load()`'s seeding path and are unaffected.)

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/config.py bridge/tests/test_config.py
git commit -m "fix(bridge): replace hardcoded default dashboard password with a random per-robot one"
```

---

### Task 2: Return 401 on failed login; validate `?limit` on the graph search endpoint

**Files:**
- Modify: `bridge/milo_bridge/webapp/api/auth.py:30-33` (`post_login`)
- Modify: `bridge/milo_bridge/webapp/api/graph.py` (whole file — add a constant and guard `get_search`)
- Test: `bridge/tests/webapp/test_auth_api.py:61-70` (`test_login_wrong_password_fails`)
- Test: `bridge/tests/webapp/test_graph_api.py` (add new tests)

**Interfaces:**
- Consumes: `bridge/tests/webapp/client_helpers.py`'s `authed_client(deps)` helper and `bridge/tests/webapp/fakes.py`'s `make_deps(**overrides)` (both already used by `test_graph_api.py`, unchanged).
- Produces: no new public names outside the modified files. `get_search` still returns the same JSON shape on success; it now returns `{"error": "..."}` with `status=400` for a non-integer or out-of-range `limit`.

The dashboard login currently returns HTTP 200 with an `{"error": ...}` body for wrong credentials — every other failure path in the same handler (`too many attempts` at line 23, `invalid request` at line 27) already returns a real status code. The graph search endpoint's `int(request.query.get("limit", "25"))` throws an uncaught `ValueError` on a non-numeric `limit` (falls through to the app's generic 500 handler), and SQLite treats a negative `LIMIT` as "no limit," so `?limit=-1` silently bypasses the cap entirely — both need a real check.

- [ ] **Step 1: Write the failing tests**

In `bridge/tests/webapp/test_auth_api.py`, change `test_login_wrong_password_fails`:

```python
async def test_login_wrong_password_fails():
    client = await _raw_client(make_deps())
    try:
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": "wrong"})
        assert resp.status == 401
        data = await resp.json()
        assert data["error"] == "invalid credentials"
        assert "milo_session" not in client.session.cookie_jar.filter_cookies("http://127.0.0.1")
    finally:
        await client.close()
```

In `bridge/tests/webapp/test_graph_api.py`, add at the end of the file:

```python
async def test_search_rejects_non_integer_limit():
    client = await _client(make_deps())
    try:
        resp = await client.get("/api/graph/search", params={"limit": "not-a-number"})
        assert resp.status == 400
        data = await resp.json()
        assert "limit" in data["error"]
    finally:
        await client.close()


async def test_search_rejects_negative_limit():
    deps = make_deps()
    _seed(deps.graph_store)
    client = await _client(deps)
    try:
        resp = await client.get("/api/graph/search", params={"limit": "-1"})
        assert resp.status == 400
        data = await resp.json()
        assert "limit" in data["error"]
    finally:
        await client.close()


async def test_search_rejects_limit_above_cap():
    client = await _client(make_deps())
    try:
        resp = await client.get("/api/graph/search", params={"limit": "100000"})
        assert resp.status == 400
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/webapp/test_auth_api.py::test_login_wrong_password_fails tests/webapp/test_graph_api.py -v`
Expected: `test_login_wrong_password_fails` FAILS (`assert resp.status == 401` gets `200`). `test_search_rejects_non_integer_limit` FAILS with a 500 (uncaught `ValueError`) instead of the asserted 400. `test_search_rejects_negative_limit` FAILS (currently succeeds with `status=200` and no cap). `test_search_rejects_limit_above_cap` FAILS the same way.

- [ ] **Step 3: Write minimal implementation**

In `bridge/milo_bridge/webapp/api/auth.py`, change line 33:

```python
    if not ok:
        throttle.record_failure(ip)
        return web.json_response({"error": "invalid credentials"}, status=401)
```

Replace `bridge/milo_bridge/webapp/api/graph.py` in full:

```python
from aiohttp import web

MAX_SEARCH_LIMIT = 500


async def post_graph(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"})
    return web.json_response(deps.graph_api.handle(body))


async def get_search(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    q = request.query.get("q", "").strip()
    try:
        limit = int(request.query.get("limit", "25"))
    except ValueError:
        return web.json_response({"error": "limit must be an integer"}, status=400)
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        return web.json_response(
            {"error": f"limit must be between 1 and {MAX_SEARCH_LIMIT}"}, status=400
        )
    if not q:
        return web.json_response(deps.graph_store.all(limit))
    return web.json_response(deps.graph_store.search_text(q, limit))


def register(app: web.Application) -> None:
    app.router.add_post("/api/graph", post_graph)
    app.router.add_get("/api/graph/search", get_search)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/webapp/test_auth_api.py tests/webapp/test_graph_api.py -v`
Expected: PASS. In particular re-check `test_search_with_empty_query_returns_full_graph_via_http` (passes `limit=200`, inside the new `[1, 500]` range) and `test_all_respects_limit`/`test_all_returns_full_graph_capped` (call `deps.graph_store.all(limit=...)` directly, bypassing the HTTP layer entirely) still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/api/auth.py bridge/milo_bridge/webapp/api/graph.py \
        bridge/tests/webapp/test_auth_api.py bridge/tests/webapp/test_graph_api.py
git commit -m "fix(bridge): 401 on bad login, validate and cap the graph search ?limit"
```

---

### Task 3: Guard `mint_mcp_token` against overwriting a paired brain; lock down `paired.json`

**Files:**
- Modify: `common/milo_common/auth.py:109-111` (`PairedStore._save`, add `import os`)
- Modify: `bridge/milo_bridge/mcp/auth.py:22-25` (`mint_mcp_token`)
- Modify: `bridge/milo_bridge/cli.py:54-59` (`_cmd_mcp_pair`, add `import sys`)
- Test: `common/tests/test_auth.py` (add a permissions test)
- Test: `bridge/tests/mcp/test_auth.py` (add a collision test)
- Test: `bridge/tests/test_cli.py` (add a collision test)

**Interfaces:**
- Consumes: `PairedStore.is_paired(peer_id: str) -> bool` (already exists, `common/milo_common/auth.py:103-104`).
- Produces: `mint_mcp_token(store, peer_id)` now raises `ValueError` instead of silently overwriting when `peer_id` is already paired — `_cmd_mcp_pair` (the only caller) catches it and exits with a message instead of a raw traceback.

`mint_mcp_token` does a blind `store.add(peer_id, token, ...)`, which is a plain dict assignment (`PairedStore.add`, `common/milo_common/auth.py:79-84`) — minting an MCP token for a `peer_id` that collides with an already-paired robot/brain silently replaces that peer's real pairing token, breaking its connection. Separately, `paired.json` holds every long-term shared secret in cleartext hex and is written with the process's default umask; it should be `0600`.

- [ ] **Step 1: Write the failing tests**

In `common/tests/test_auth.py`, add (needs `import sys` at top only if not already present — check first; it isn't, so add it):

```python
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions only")
def test_paired_store_file_is_not_world_or_group_readable(tmp_path: Path):
    store = auth.PairedStore(tmp_path / "paired.json")
    store.add("brain-1", auth.derive_token("123123", "r", "b"), name="laptop")
    mode = (tmp_path / "paired.json").stat().st_mode & 0o777
    assert mode == 0o600
```

In `bridge/tests/mcp/test_auth.py`, add:

```python
def test_mint_mcp_token_refuses_to_overwrite_an_existing_peer(store):
    original_hex = mint_mcp_token(store, "laptop-1")
    with pytest.raises(ValueError):
        mint_mcp_token(store, "laptop-1")
    # The original token must survive the rejected mint attempt.
    assert store.token_for("laptop-1") == bytes.fromhex(original_hex)
```

In `bridge/tests/test_cli.py`, add (matching the existing `test_mcp_pair_mints_and_persists_a_token` fixture pattern at the top of the file):

```python
def test_mcp_pair_refuses_to_clobber_an_existing_peer(tmp_path, monkeypatch, capsys):
    cfg = BridgeConfig(data_dir=str(tmp_path))
    monkeypatch.setattr(BridgeConfig, "load", classmethod(lambda cls: cfg))

    cli.main(["mcp-pair", "--name", "my-laptop"])
    first_token = PairedStore(cfg.paired_path).token_for("my-laptop")

    with pytest.raises(SystemExit):
        cli.main(["mcp-pair", "--name", "my-laptop"])

    assert PairedStore(cfg.paired_path).token_for("my-laptop") == first_token
```

Check `bridge/tests/test_cli.py`'s existing imports (`BridgeConfig`, `PairedStore`, `cli` module, `pytest`) already cover what this test needs — if `pytest` isn't imported, add `import pytest` at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd common && python -m pytest tests/test_auth.py -v` (skips on Windows; if running in this Windows dev environment, note the skip and move on)
Run: `cd bridge && python -m pytest tests/mcp/test_auth.py tests/test_cli.py -v`
Expected: `test_mint_mcp_token_refuses_to_overwrite_an_existing_peer` FAILS (no exception raised — the second mint currently succeeds silently). `test_mcp_pair_refuses_to_clobber_an_existing_peer` FAILS (no `SystemExit` raised).

- [ ] **Step 3: Write minimal implementation**

In `common/milo_common/auth.py`, add `import os` alongside the existing stdlib imports (`hashlib`, `hmac`, `json`, `secrets`, `pathlib.Path`), then change `_save`:

```python
    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._peers, indent=2), encoding="utf-8")
        os.chmod(self.path, 0o600)
```

In `bridge/milo_bridge/mcp/auth.py`, change `mint_mcp_token`:

```python
def mint_mcp_token(store: PairedStore, peer_id: str) -> str:
    if store.is_paired(peer_id):
        raise ValueError(f"{peer_id!r} is already paired; choose a different name")
    token = secrets.token_bytes(TOKEN_BYTES)
    store.add(peer_id, token, name=peer_id)
    return token.hex()
```

In `bridge/milo_bridge/cli.py`, add `import sys` to the top-level imports (alongside `argparse`, `asyncio`), then change `_cmd_mcp_pair`:

```python
def _cmd_mcp_pair(cfg: BridgeConfig, name: str) -> None:
    store = PairedStore(cfg.paired_path)
    try:
        token_hex = mint_mcp_token(store, name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Paste this into the MCP client config for {name!r}:")
    print(f"  peer: {name}")
    print(f"  token: {token_hex}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd common && python -m pytest tests/test_auth.py -v`
Run: `cd bridge && python -m pytest tests/mcp/test_auth.py tests/test_cli.py -v`
Expected: PASS (the permissions test skips on Windows; it will run and pass on the Pi / any POSIX CI runner).

- [ ] **Step 5: Commit**

```bash
git add common/milo_common/auth.py bridge/milo_bridge/mcp/auth.py bridge/milo_bridge/cli.py \
        common/tests/test_auth.py bridge/tests/mcp/test_auth.py bridge/tests/test_cli.py
git commit -m "fix(common,bridge): lock down paired.json and refuse to clobber an existing peer's MCP token"
```

---

### Task 4: Reconcile the `seq` docstring; drop the dead `face`/`move` fallback keys

**Files:**
- Modify: `common/milo_common/protocol.py:1-12` (module docstring)
- Modify: `brain/milo_brain/llm/agent.py:152-166` (`parse_llm_json`)
- Test: `brain/tests/test_agent.py:247-250` (`test_parse_llm_json_garbage_degrades_gracefully`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `parse_llm_json`'s total-parse-failure fallback dict now has only `{"reply": ..., "facts": []}` — no `face`/`move` keys. `sanitize()` (`agent.py:169-171`) already only ever reads `reply` and `facts`, so no caller is affected.

`protocol.py`'s module docstring claims `seq` lets "either side ... detect a lost pairing between header and payload and re-sync" — but `MiloSocket.recv()` (`protocol.py:122-136`) never reads `header.get("seq")` at all. The actual desync protection is `MiloSocket._send_lock` (making header+payload atomic on the wire) plus the `bin` flag matching, both already covered by `test_concurrent_sends_on_the_same_socket_never_interleave` and `test_desync_detected` in `common/tests/test_protocol.py`. Since every connection is a single ordered TCP stream, a receive-side seq-gap check would be dead code detecting a condition TCP already rules out — the honest fix is correcting the docstring, not adding an unused check. Separately, `parse_llm_json`'s garbage-fallback manufactures `"face": "confused"` and `"move": "none"`, but `sanitize()` never reads either key (confirmed by `test_sanitize_drops_face_and_move_keeps_reply_and_facts` at `brain/tests/test_agent.py:253`) — they're vestigial from an older reply schema.

- [ ] **Step 1: Write the failing test**

In `brain/tests/test_agent.py`, change `test_parse_llm_json_garbage_degrades_gracefully`:

```python
def test_parse_llm_json_garbage_degrades_gracefully():
    result = parse_llm_json("I am not JSON at all")
    assert result["reply"]
    assert "face" not in result
    assert "move" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && python -m pytest tests/test_agent.py::test_parse_llm_json_garbage_degrades_gracefully -v`
Expected: FAIL on `assert "face" not in result` (currently `result["face"] == "confused"`).

- [ ] **Step 3: Write minimal implementation**

In `brain/milo_brain/llm/agent.py`, change the last line of `parse_llm_json` (line 166):

```python
    return {"reply": text[:200] or "Hmm.", "facts": []}
```

In `common/milo_common/protocol.py`, replace the docstring paragraph at lines 3-6:

```python
Every logical message is a JSON text frame. Messages that carry bulk data
(video/audio/tts) set ``"bin": true`` in the header; the binary payload is sent
as the immediately following bytes frame. Headers carry ``seq`` so either side
can detect a lost pairing between header and payload and re-sync.
```

with:

```python
Every logical message is a JSON text frame. Messages that carry bulk data
(video/audio/tts) set ``"bin": true`` in the header; the binary payload is sent
as the immediately following bytes frame. ``MiloSocket``'s per-connection send
lock makes each header+payload pair atomic on the wire, so a receiver never
sees one message's header followed by another's payload -- see
``test_concurrent_sends_on_the_same_socket_never_interleave`` in
common/tests/test_protocol.py. Headers also carry a monotonically increasing
``seq`` (not currently read on receive) for log correlation across the two
sides -- the transport is a single ordered stream, so there's nothing for a
receive-side seq check to catch that TCP hasn't already ruled out.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && python -m pytest tests/test_agent.py -v`
Expected: PASS (the full file, since `test_parse_llm_json_plain_and_fenced` at line 239 doesn't touch the garbage-fallback path and is unaffected).

Run: `cd common && python -m pytest tests/test_protocol.py -v`
Expected: PASS (docstring-only change, no behavior touched).

- [ ] **Step 5: Commit**

```bash
git add common/milo_common/protocol.py brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "docs(common,brain): correct the seq docstring, drop dead face/move fallback keys"
```

---

### Task 5: Document the plaintext-transport / LAN-only trust boundary

**Files:**
- Modify: `common/milo_common/protocol.py` (module docstring, appended in Task 4 — add one more paragraph)
- Modify: `bridge/milo_bridge/webapp/__init__.py:1` (module docstring)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing (docs-only; no code path changes, no tests).

Neither the robot↔brain WebSocket link nor the web dashboard use TLS: `RobotServer.serve_forever` (`bridge/milo_bridge/net/server.py:216`) calls `websockets.serve(..., "0.0.0.0", ...)` with a bare `ws://` socket, and `create_app` (`bridge/milo_bridge/webapp/__init__.py:79`) is plain `aiohttp` with no HTTPS termination. The pairing handshake itself is genuinely strong (HMAC challenge/response, constant-time compare, PIN never on the wire — see `common/milo_common/handshake.py`'s docstring), but everything *after* the handshake — video, audio, movement commands, the dashboard session cookie — rides unencrypted. That's an acceptable design for "trusted home LAN only," but it isn't written down anywhere, so a future contributor could reasonably assume more protection exists than actually does. This task makes the assumption explicit at the two places a reader is most likely to look.

- [ ] **Step 1: Add the trust-boundary note to `protocol.py`**

Append this paragraph to the module docstring in `common/milo_common/protocol.py` (after the paragraph added in Task 4, before the `Message types` section):

```python
Trust boundary: every frame after the handshake -- video, audio, tts, movement
commands -- travels over a plain ``ws://`` socket with no transport
encryption. The handshake's mutual HMAC auth (see handshake.py) proves who's
on the other end; it does not make the session traffic itself confidential
against something else already on the same LAN. This is a deliberate
"trusted home network" design, not an oversight -- add TLS if that
assumption stops holding (e.g. the robot leaves a trusted network).
```

- [ ] **Step 2: Add the trust-boundary note to `webapp/__init__.py`**

Replace the module docstring in `bridge/milo_bridge/webapp/__init__.py:1`:

```python
"""Milo web dashboard: aiohttp app factory."""
```

with:

```python
"""Milo web dashboard: aiohttp app factory.

Trust boundary: this serves plain HTTP, no TLS termination. The session
cookie is ``httponly`` + ``samesite=Strict`` but not ``Secure`` (there's no
HTTPS to require) -- login credentials and the session cookie itself are
readable by anything else on the same LAN segment. Same "trusted home
network" assumption as the robot<->brain link (see
common/milo_common/protocol.py); add TLS (e.g. a reverse proxy) if that
assumption stops holding.
"""
```

- [ ] **Step 3: Verify the notes landed and nothing else broke**

Run: `grep -n "Trust boundary" common/milo_common/protocol.py bridge/milo_bridge/webapp/__init__.py`
Expected: one match in each file.

Run: `cd bridge && python -m pytest tests/webapp/ -v`
Expected: PASS (docstring-only change to a file with no executable statements touched).

- [ ] **Step 4: Commit**

```bash
git add common/milo_common/protocol.py bridge/milo_bridge/webapp/__init__.py
git commit -m "docs: write down the LAN-only plaintext-transport trust boundary"
```

---

## Final verification

- [ ] Run the full suite for every package touched:

```bash
cd common && python -m pytest -q
cd ../bridge && python -m pytest -q
cd ../brain && python -m pytest -q
```

Expected: all PASS, zero new failures or skips beyond the one platform-gated skip in Task 3 (Windows).
