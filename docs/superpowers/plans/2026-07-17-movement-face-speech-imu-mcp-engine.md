# Movement, Face, Speech & IMU MCP Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the brain's LLM real, multi-step tool-calling control over Milo's entire movement repertoire, face display, and on-demand speech, plus live IMU telemetry, through a standard MCP server on the bridge — replacing the current 6-move JSON enum and fixed face field — and add an offline per-movement IMU characterization harness.

**Architecture:** A new MCP server (official `mcp` Python SDK, Streamable HTTP) runs on the bridge (Raspberry Pi) alongside the existing aiohttp dashboard, wrapping the same `GaitEngine`/`PoseRunner`/`Mpu6050`/`ControlBroker`/`FaceDisplay`/`AudioIO` objects `main.py` already builds. The brain's `CognitionAgent` gets an MCP client and a bounded tool-calling loop against Ollama; `move`/`face` are removed from the `T_CMD` wire protocol entirely. Auth reuses the existing pairing trust store (`PairedStore`) — the brain authenticates with the token it already shares with this robot; a new CLI command mints one-off tokens for human MCP clients (Claude Desktop/Code).

**Tech Stack:** Python 3.11+, `mcp` SDK (`FastMCP`, `ClientSession`, Streamable HTTP transport), `uvicorn` (serves the MCP ASGI app), `starlette` (bearer-auth middleware, a transitive dep of `mcp`), Ollama's native tool-calling `/api/chat` endpoint, `pytest`/`pytest-asyncio`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-17-movement-imu-mcp-design.md` — every task below traces back to a section there.
- No backward compatibility with brains/bridges that predate this change — both sides upgrade together (`PROTOCOL_VERSION` bumps from `1` to `2`).
- Every gated MCP tool must check `broker.allow_brain_motion()` — mirrors `RobotSession._handle_cmd`'s existing behavior; `stop`/`get_imu_state`/`get_status` are **never** gated.
- `run_pose`/`turn` share one `MovementGuard` (single in-flight task) so two overlapping MCP callers can't race the same `PoseRunner`; `walk`/`set_mode`/`reset`/`standby`/`relax`/`hold`/`set_face`/`speak` are synchronous one-shot calls with no such race today and don't need it (matches existing `MotionService` behavior — `reset()`/`standby()` already have no pose-running guard either).
- Bridge MCP port default: **8766** — deliberately *not* 8765, which is already `BrainConfig.port` (the brain's own WS listen port); reusing the same number for two unrelated services on two different machines is confusing even though it isn't a real collision.
- MCP auth: `Authorization: Bearer <token-hex>` + `X-Milo-Peer: <peer_id>`, checked against the bridge's own `PairedStore(cfg.paired_path)` — the *same* file robot↔brain pairing already writes to, not a new store.
- All new code follows this repo's existing pattern: real hardware/network objects are injected; tests use fakes; nothing bypasses `ruff`/existing lint config if present.
- Every step below assumes the working directory is the relevant sub-package root (`bridge/`, `brain/`, or `common/`) when running `pytest`, matching how this repo's `pyproject.toml` files are laid out per-package.

---

## Task 1: Bridge config — `mcp_port`

**Files:**
- Modify: `bridge/milo_bridge/config.py`
- Test: `bridge/tests/test_config.py` (create if it doesn't exist — check first with `Glob bridge/tests/test_config.py`)

**Interfaces:**
- Produces: `BridgeConfig.mcp_port: int` (default `8766`), persisted/loaded exactly like every other `BridgeConfig` field.

- [ ] **Step 1: Write the failing test**

```python
# bridge/tests/test_config.py
from pathlib import Path

from milo_bridge.config import BridgeConfig


def test_mcp_port_defaults_and_round_trips(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.mcp_port == 8766

    cfg.mcp_port = 9999
    cfg.save(path)
    reloaded = BridgeConfig.load(path)
    assert reloaded.mcp_port == 9999
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'BridgeConfig' object has no attribute 'mcp_port'`

- [ ] **Step 3: Add the field**

In `bridge/milo_bridge/config.py`, add `mcp_port: int = 8766` to the `BridgeConfig` dataclass, right after `web_port: int = 80`:

```python
    web_enabled: bool = True
    web_port: int = 80
    web_username: str = "dama"
    web_password_hash: str = ""   # scrypt "<salt_hex>$<hash_hex>"; seeded on first load()
    mcp_port: int = 8766
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/config.py bridge/tests/test_config.py
git commit -m "feat(bridge): add mcp_port to BridgeConfig"
```

---

## Task 2: Bridge — MCP auth (bearer middleware + one-off token minting)

**Files:**
- Create: `bridge/milo_bridge/mcp/__init__.py` (empty)
- Create: `bridge/milo_bridge/mcp/auth.py`
- Test: `bridge/tests/mcp/__init__.py` (empty)
- Test: `bridge/tests/mcp/test_auth.py`

**Interfaces:**
- Consumes: `milo_common.auth.PairedStore` (`.token_for(peer_id) -> bytes | None`, `.add(peer_id, token, name=...)`), `milo_common.auth.TOKEN_BYTES` (int, `32`).
- Produces: `mint_mcp_token(store: PairedStore, peer_id: str) -> str` (hex token, also persisted into `store`). `BearerAuthMiddleware(app, store: PairedStore)` — a `starlette.middleware.base.BaseHTTPMiddleware` subclass; 401s any request missing/mismatching `Authorization: Bearer <hex>` + `X-Milo-Peer: <peer_id>` against `store.token_for(peer_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# bridge/tests/mcp/test_auth.py
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from milo_common.auth import PairedStore
from milo_bridge.mcp.auth import BearerAuthMiddleware, mint_mcp_token


@pytest.fixture()
def store(tmp_path):
    return PairedStore(tmp_path / "paired.json")


def test_mint_mcp_token_persists_and_returns_hex(store):
    token_hex = mint_mcp_token(store, "laptop-1")
    assert len(token_hex) == 64  # 32 bytes hex-encoded
    assert store.token_for("laptop-1") == bytes.fromhex(token_hex)


def _app_with_auth(store):
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/ping", ok)])
    return BearerAuthMiddleware(app, store)


def test_missing_headers_are_rejected(store):
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping")
    assert resp.status_code == 401


def test_unknown_peer_is_rejected(store):
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": "Bearer " + "00" * 32, "X-Milo-Peer": "nobody"})
    assert resp.status_code == 401


def test_wrong_token_is_rejected(store):
    mint_mcp_token(store, "laptop-1")
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": "Bearer " + "11" * 32, "X-Milo-Peer": "laptop-1"})
    assert resp.status_code == 401


def test_correct_token_is_accepted(store):
    token_hex = mint_mcp_token(store, "laptop-1")
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": f"Bearer {token_hex}", "X-Milo-Peer": "laptop-1"})
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_malformed_bearer_token_is_rejected(store):
    mint_mcp_token(store, "laptop-1")
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": "Bearer not-hex", "X-Milo-Peer": "laptop-1"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/mcp/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.mcp'`

- [ ] **Step 3: Write the implementation**

```python
# bridge/milo_bridge/mcp/__init__.py
```

(empty file — marks the package)

```python
# bridge/tests/mcp/__init__.py
```

(empty file)

```python
# bridge/milo_bridge/mcp/auth.py
"""Bearer-token gate for the movement/face/speech/IMU MCP server, reusing
the existing pairing trust store (common/milo_common/auth.py PairedStore)
instead of standing up a second auth system.

A paired brain authenticates with the token it already shares with this
robot from the WS pairing handshake. A human MCP client (Claude Desktop/
Code) has no such existing relationship, so ``mint_mcp_token`` provisions
one -- a plain random secret added to the same store under a chosen name,
printed once for the operator to paste into their MCP client config.
"""
from __future__ import annotations

import hmac
import secrets

from milo_common.auth import TOKEN_BYTES, PairedStore
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def mint_mcp_token(store: PairedStore, peer_id: str) -> str:
    token = secrets.token_bytes(TOKEN_BYTES)
    store.add(peer_id, token, name=peer_id)
    return token.hex()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, store: PairedStore):
        super().__init__(app)
        self._store = store

    async def dispatch(self, request: Request, call_next):
        peer_id = request.headers.get("X-Milo-Peer", "")
        auth_header = request.headers.get("Authorization", "")
        expected = self._store.token_for(peer_id)
        if expected is None or not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            provided = bytes.fromhex(auth_header[len("Bearer "):])
        except ValueError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not hmac.compare_digest(provided, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/mcp/test_auth.py -v`
Expected: PASS (6 tests). `starlette.testclient.TestClient` requires `httpx` at runtime (a hard Starlette dependency for its test client, distinct from the SDK's own HTTP transport) in addition to `starlette` itself — if either isn't installed yet, `pip install starlette httpx` first. Task 9 adds `starlette` as a formal `bridge/pyproject.toml` dependency; add `httpx>=0.27` to that same package's `dev` extra at the same time (bridge's production code never calls Ollama/httpx directly, so it belongs in `dev`, not the main `dependencies` list) — installing both locally now just unblocks this test run ahead of that.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/mcp/__init__.py bridge/milo_bridge/mcp/auth.py bridge/tests/mcp/__init__.py bridge/tests/mcp/test_auth.py
git commit -m "feat(bridge): add MCP bearer-auth middleware reusing PairedStore"
```

---

## Task 3: Bridge — `mcp-pair` CLI command

**Files:**
- Modify: `bridge/milo_bridge/cli.py`
- Test: `bridge/tests/test_cli.py` (create if it doesn't exist — check first)

**Interfaces:**
- Consumes: `mint_mcp_token` from Task 2 (`bridge/milo_bridge/mcp/auth.py`).
- Produces: `python -m milo_bridge.cli mcp-pair --name <peer-name>` — prints the peer name and the minted hex token to stdout, persists it via `PairedStore(cfg.paired_path)`.

- [ ] **Step 1: Write the failing test**

```python
# bridge/tests/test_cli.py
from milo_bridge import cli
from milo_bridge.config import BridgeConfig
from milo_common.auth import PairedStore


def test_mcp_pair_mints_and_persists_a_token(tmp_path, monkeypatch, capsys):
    cfg = BridgeConfig(data_dir=str(tmp_path))
    monkeypatch.setattr(BridgeConfig, "load", classmethod(lambda cls: cfg))

    cli.main(["mcp-pair", "--name", "my-laptop"])

    out = capsys.readouterr().out
    assert "my-laptop" in out
    store = PairedStore(cfg.paired_path)
    assert store.is_paired("my-laptop")
    # The printed token is the same one that was persisted.
    printed_token = out.strip().splitlines()[-1].split()[-1]
    assert bytes.fromhex(printed_token) == store.token_for("my-laptop")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/test_cli.py -v`
Expected: FAIL — `mcp-pair` isn't a recognized subcommand (`argparse` exits with an error / `SystemExit`).

- [ ] **Step 3: Add the subcommand**

In `bridge/milo_bridge/cli.py`, add the import and subcommand:

```python
from .mcp.auth import mint_mcp_token
```

Add near the other `_cmd_*` functions:

```python
def _cmd_mcp_pair(cfg: BridgeConfig, name: str) -> None:
    store = PairedStore(cfg.paired_path)
    token_hex = mint_mcp_token(store, name)
    print(f"Paste this into the MCP client config for {name!r}:")
    print(f"  peer: {name}")
    print(f"  token: {token_hex}")
```

`PairedStore` isn't imported yet in `cli.py` (only used inside `_cmd_*`/`paired` handling via `from milo_common.auth import PairedStore` — check the existing import at the top; it's already there per the `paired` subcommand). Add the argparse wiring in `main()`:

```python
    mcp_pair = sub.add_parser("mcp-pair", help="mint an MCP bearer token for a human MCP client")
    mcp_pair.add_argument("--name", required=True, help="a name for this MCP client, e.g. your laptop")
```

and in the dispatch block:

```python
    elif args.command == "mcp-pair":
        _cmd_mcp_pair(cfg, args.name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/cli.py bridge/tests/test_cli.py
git commit -m "feat(bridge): add mcp-pair CLI command to provision human MCP clients"
```

---

## Task 4: Bridge — MCP dependency bundle + movement guard

**Files:**
- Create: `bridge/milo_bridge/mcp/deps.py`
- Test: `bridge/tests/mcp/test_deps.py`

**Interfaces:**
- Produces: `MovementGuard` (`.busy() -> bool`, `.start(coro) -> None`), `McpDeps` dataclass (`gait`, `runner`, `imu`, `broker`, `servos`, `display`, `audio`, `movement_guard: MovementGuard`). `PairedStore` is deliberately *not* a field here — auth happens in `BearerAuthMiddleware` (Task 2), wired around the ASGI app in Task 9, not inside individual tool functions, so no tool ever needs to read it.

- [ ] **Step 1: Write the failing test**

```python
# bridge/tests/mcp/test_deps.py
import asyncio

from milo_bridge.mcp.deps import MovementGuard


def test_guard_is_free_until_a_coroutine_is_running():
    guard = MovementGuard()
    assert guard.busy() is False


def test_guard_reports_busy_while_the_task_runs_and_frees_after():
    async def main():
        guard = MovementGuard()
        started = asyncio.Event()
        finish = asyncio.Event()

        async def slow():
            started.set()
            await finish.wait()

        guard.start(slow())
        await started.wait()
        assert guard.busy() is True

        finish.set()
        await asyncio.sleep(0)  # let the task actually finish
        await asyncio.sleep(0)
        assert guard.busy() is False

    asyncio.run(main())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/mcp/test_deps.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.mcp.deps'`

- [ ] **Step 3: Write the implementation**

```python
# bridge/milo_bridge/mcp/deps.py
"""Dependency bundle for the movement/face/speech/IMU MCP server -- mirrors
webapp/deps.py's WebDeps pattern for the same underlying objects, MCP-side.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class MovementGuard:
    """Tracks the one pose/gait animation run_pose/turn may have in flight,
    so two overlapping calls -- the brain's own tool-calling loop and a
    human's MCP client testing alongside it -- serialize instead of racing
    for the same PoseRunner (mirrors MotionService's existing
    "pose-running" guard in webapp/motion.py)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, coro) -> None:
        self._task = asyncio.ensure_future(coro)


@dataclass
class McpDeps:
    gait: Any             # GaitEngine
    runner: Any            # PoseRunner
    imu: Any | None        # Mpu6050
    broker: Any            # ControlBroker
    servos: Any            # SmoothServos (relax/hold)
    display: Any           # FaceDisplay
    audio: Any | None      # AudioIO
    movement_guard: MovementGuard = field(default_factory=MovementGuard)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/mcp/test_deps.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/mcp/deps.py bridge/tests/mcp/test_deps.py
git commit -m "feat(bridge): add McpDeps bundle and MovementGuard"
```

---

## Task 5: Bridge — MCP server, movement tools

**Files:**
- Create: `bridge/milo_bridge/mcp/server.py`
- Test: `bridge/tests/mcp/test_server.py`

**Interfaces:**
- Consumes: `McpDeps`/`MovementGuard` (Task 4), `poses.POSES` (existing).
- Produces: `build_mcp_server(deps: McpDeps) -> FastMCP` with tools `walk`, `run_pose`, `turn`, `set_mode`, `reset`, `standby`, `relax`, `hold`, `stop` registered. Every FastMCP tool function is `async def name(...) -> dict`; call it directly in tests via `await server.call_tool("name", {...})` (FastMCP's in-process call path — no HTTP needed for these tests).

- [ ] **Step 1: Write the failing tests**

```python
# bridge/tests/mcp/test_server.py
import asyncio

import pytest

from milo_bridge.mcp.deps import McpDeps
from milo_bridge.mcp.server import build_mcp_server


class FakeGait:
    def __init__(self):
        self.velocity = None
        self.mode = "balanced"
        self.backend = "cpg"
        self.reset_called = False
        self.standby_called = False

    def set_velocity_command(self, vx, vy, yaw):
        self.velocity = (vx, vy, yaw)

    def set_mode(self, name):
        if name not in ("raw", "balanced", "angled"):
            raise ValueError(f"unknown mode {name!r}")
        self.mode = name

    def reset(self):
        self.reset_called = True

    def standby(self):
        self.standby_called = True


class FakeRunner:
    def __init__(self):
        self.ran: list[tuple[str, int | None]] = []
        self.aborted = False
        self.gate = None  # optional asyncio.Event to hold run() open

    async def run(self, name, cycles=None):
        self.ran.append((name, cycles))
        if self.gate is not None:
            await self.gate.wait()
        return True

    def abort(self):
        self.aborted = True


class FakeBroker:
    def __init__(self, allow=True):
        self._allow = allow
        self.owner = "none"

    def allow_brain_motion(self):
        return self._allow


class FakeServos:
    def __init__(self):
        self.relaxed = False
        self.held = False

    def relax(self):
        self.relaxed = True

    def hold(self):
        self.held = True


def make_deps(allow=True):
    return McpDeps(
        gait=FakeGait(), runner=FakeRunner(), imu=None, broker=FakeBroker(allow),
        servos=FakeServos(), display=None, audio=None,
    )


async def _call(server, tool_name, **kwargs):
    # Parameter deliberately named tool_name, not name -- several tools
    # (run_pose, set_mode, set_face) take a kwarg literally called `name`,
    # which would collide with (and shadow) a same-named parameter here.
    result = await server.call_tool(tool_name, kwargs)
    return result


def test_walk_clamps_and_forwards_velocity():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "walk", vx=5.0, vy=-5.0, yaw_rate=100.0)
        assert result["ok"] is True
        assert deps.gait.velocity == (1.0, -1.0, 2.0)  # clamped to VX_LIM/VY_LIM/YAW_LIM

    asyncio.run(main())


def test_walk_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "walk", vx=0.1, vy=0.0, yaw_rate=0.0)
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.gait.velocity is None

    asyncio.run(main())


def test_run_pose_rejects_unknown_name():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "run_pose", name="not-a-pose")
        assert result["ok"] is False and "unknown pose" in result["error"]

    asyncio.run(main())


def test_run_pose_starts_the_runner_and_returns_immediately():
    async def main():
        deps = make_deps()
        deps.runner.gate = asyncio.Event()  # keep the pose "running" so we can assert fire-and-forget
        server = build_mcp_server(deps)
        result = await _call(server, "run_pose", name="wave")
        assert result == {"ok": True}
        assert deps.runner.ran == [("wave", None)]
        assert deps.movement_guard.busy() is True
        deps.runner.gate.set()

    asyncio.run(main())


def test_run_pose_rejects_a_second_call_while_one_is_in_flight():
    async def main():
        deps = make_deps()
        deps.runner.gate = asyncio.Event()
        server = build_mcp_server(deps)
        await _call(server, "run_pose", name="wave")
        second = await _call(server, "run_pose", name="dance")
        assert second == {"ok": False, "error": "movement-in-progress"}
        deps.runner.gate.set()

    asyncio.run(main())


def test_turn_starts_the_continuous_turn_pose():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "turn", direction="left")
        assert result == {"ok": True}
        name, cycles = deps.runner.ran[0]
        assert name == "turn_left" and cycles == 10_000

    asyncio.run(main())


def test_turn_rejects_bad_direction():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "turn", direction="sideways")
        assert result["ok"] is False

    asyncio.run(main())


def test_set_mode_validates_and_applies():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        ok = await _call(server, "set_mode", name="raw")
        assert ok == {"ok": True, "mode": "raw"}
        assert deps.gait.mode == "raw"
        bad = await _call(server, "set_mode", name="sideways")
        assert bad["ok"] is False

    asyncio.run(main())


def test_reset_and_standby_call_through_when_allowed():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        await _call(server, "reset")
        await _call(server, "standby")
        assert deps.gait.reset_called and deps.gait.standby_called

    asyncio.run(main())


def test_relax_and_hold_call_through():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        await _call(server, "relax")
        await _call(server, "hold")
        assert deps.servos.relaxed and deps.servos.held

    asyncio.run(main())


def test_stop_is_never_gated_and_aborts():
    async def main():
        deps = make_deps(allow=False)  # web controls -- stop must still work
        server = build_mcp_server(deps)
        result = await _call(server, "stop")
        assert result == {"ok": True}
        assert deps.gait.velocity == (0.0, 0.0, 0.0)
        assert deps.runner.aborted is True

    asyncio.run(main())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.mcp.server'`

- [ ] **Step 3: Write the implementation**

```python
# bridge/milo_bridge/mcp/server.py
"""Movement, face, speech & IMU MCP server -- the bridge's tool surface for
the brain's tool-calling LLM (and, for manual testing, a human's MCP
client). Every gated tool honors the same ControlBroker a web client
already does (see webapp/motion.py); run_pose/turn share one MovementGuard
so overlapping callers serialize instead of racing the same PoseRunner.
"""
from __future__ import annotations

from ..poses import POSES
from .deps import McpDeps

TURN_HOLD_CYCLES = 10_000  # matches webapp/motion.py's "continuous until aborted" idiom
VX_LIM, VY_LIM, YAW_LIM = 1.0, 1.0, 2.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def build_mcp_server(deps: McpDeps):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("milo-movement")

    @server.tool()
    async def walk(vx: float, vy: float, yaw_rate: float) -> dict:
        """Continuous velocity walk: vx/vy in m/s, yaw_rate in deg/s. (0,0,0) stops walking."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.gait.set_velocity_command(
            _clamp(vx, -VX_LIM, VX_LIM), _clamp(vy, -VY_LIM, VY_LIM), _clamp(yaw_rate, -YAW_LIM, YAW_LIM)
        )
        return {"ok": True}

    @server.tool()
    async def run_pose(name: str, cycles: int | None = None) -> dict:
        """Run a scripted pose/gait by name (wave, dance, bow, point, pushup,
        swim, cute, freaky, worm, shake, shrug, dead, wake_up, crab, look_up,
        look_down, rest, stand, walk, walk_backward, turn_left, turn_right)."""
        if name not in POSES:
            return {"ok": False, "error": f"unknown pose {name!r}"}
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        if deps.movement_guard.busy():
            return {"ok": False, "error": "movement-in-progress"}
        kwargs = {} if cycles is None else {"cycles": cycles}
        deps.movement_guard.start(deps.runner.run(name, **kwargs))
        return {"ok": True}

    @server.tool()
    async def turn(direction: str) -> dict:
        """Turn continuously left or right until stop() is called."""
        if direction not in ("left", "right"):
            return {"ok": False, "error": f"unknown direction {direction!r}"}
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        if deps.movement_guard.busy():
            return {"ok": False, "error": "movement-in-progress"}
        deps.movement_guard.start(deps.runner.run(f"turn_{direction}", cycles=TURN_HOLD_CYCLES))
        return {"ok": True}

    @server.tool()
    async def set_mode(name: str) -> dict:
        """Set the gait mode: raw, balanced, or angled."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        try:
            deps.gait.set_mode(name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "mode": name}

    @server.tool()
    async def reset() -> dict:
        """Smoothly return every servo to the 90-degree rest angles."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.gait.reset()
        return {"ok": True}

    @server.tool()
    async def standby() -> dict:
        """Smoothly return every servo to the stand pose."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.gait.standby()
        return {"ok": True}

    @server.tool()
    async def relax() -> dict:
        """Stop driving all servos (they go limp)."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.servos.relax()
        return {"ok": True}

    @server.tool()
    async def hold() -> dict:
        """Re-engage every servo at the angle it was commanded to right before the last relax()."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.servos.hold()
        return {"ok": True}

    @server.tool()
    async def stop() -> dict:
        """Emergency stop: always allowed, regardless of who holds control."""
        deps.gait.set_velocity_command(0.0, 0.0, 0.0)
        deps.runner.abort()
        return {"ok": True}

    return server
```

Note: `server.call_tool(name, arguments)` is FastMCP's in-process tool-invocation method, used directly by the tests above without going over HTTP. If the installed `mcp` SDK version names this method differently, adjust the tests' `_call` helper to match — check with `python -c "from mcp.server.fastmcp import FastMCP; help(FastMCP.call_tool)"` after `pip install mcp` (added formally in Task 9).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -v`
Expected: PASS (11 tests). Install `mcp` locally now if needed (`pip install mcp`) — Task 9 adds it as a formal dependency.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/mcp/server.py bridge/tests/mcp/test_server.py
git commit -m "feat(bridge): add MCP movement/mode/reset/standby/relax/hold/stop tools"
```

---

## Task 6: Bridge — MCP server, IMU + status tools

**Files:**
- Modify: `bridge/milo_bridge/mcp/server.py`
- Modify: `bridge/tests/mcp/test_server.py`

**Interfaces:**
- Produces: `get_imu_state()`, `get_status()` tools, both never gated.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/mcp/test_server.py`:

```python
from dataclasses import dataclass


@dataclass
class FakeImuState:
    roll: float
    pitch: float
    yaw: float
    gyro: tuple
    accel: tuple


class FakeImu:
    def __init__(self, state):
        self._state = state

    def update(self):
        return self._state


class FakeDisplay:
    current_face = "idle"


def test_get_imu_state_reports_the_live_snapshot_and_is_never_gated():
    async def main():
        deps = make_deps(allow=False)  # web controls -- read must still work
        deps.imu = FakeImu(FakeImuState(roll=1.5, pitch=-2.0, yaw=10.0, gyro=(0, 0, 0), accel=(0, 0, 1)))
        server = build_mcp_server(deps)
        result = await _call(server, "get_imu_state")
        assert result == {
            "ok": True, "roll": 1.5, "pitch": -2.0, "yaw": 10.0,
            "gyro": [0, 0, 0], "accel": [0, 0, 1],
        }

    asyncio.run(main())


def test_get_imu_state_reports_unavailable_when_no_imu():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "get_imu_state")
        assert result == {"ok": False, "error": "imu unavailable"}

    asyncio.run(main())


def test_get_status_reports_mode_backend_owner_and_current_face():
    async def main():
        deps = make_deps(allow=False)
        deps.broker.owner = "web"
        deps.display = FakeDisplay()
        server = build_mcp_server(deps)
        result = await _call(server, "get_status")
        assert result == {
            "ok": True, "mode": "balanced", "backend": "cpg", "owner": "web",
            "moving": False, "current_face": "idle",
        }

    asyncio.run(main())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -k "imu_state or get_status" -v`
Expected: FAIL — `get_imu_state`/`get_status` tools don't exist yet.

- [ ] **Step 3: Add the tools**

In `bridge/milo_bridge/mcp/server.py`, add before `return server`:

```python
    @server.tool()
    async def get_imu_state() -> dict:
        """Live IMU snapshot: roll/pitch/yaw in degrees, gyro in deg/s, accel in g. Never gated."""
        if deps.imu is None:
            return {"ok": False, "error": "imu unavailable"}
        state = deps.imu.update()
        return {
            "ok": True, "roll": state.roll, "pitch": state.pitch, "yaw": state.yaw,
            "gyro": list(state.gyro), "accel": list(state.accel),
        }

    @server.tool()
    async def get_status() -> dict:
        """Gait mode/backend, who holds control, whether a movement is in
        flight, and the current face. Never gated."""
        return {
            "ok": True,
            "mode": deps.gait.mode,
            "backend": deps.gait.backend,
            "owner": deps.broker.owner,
            "moving": deps.movement_guard.busy(),
            "current_face": deps.display.current_face if deps.display is not None else None,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/mcp/server.py bridge/tests/mcp/test_server.py
git commit -m "feat(bridge): add get_imu_state and get_status MCP tools"
```

---

## Task 7: Bridge — MCP server, `set_face`

**Files:**
- Modify: `bridge/milo_bridge/mcp/server.py`
- Modify: `bridge/tests/mcp/test_server.py`

**Interfaces:**
- Produces: `set_face(name: str) -> dict` tool, gated, calling `display.set_face(name)` then reporting `display.current_face` (which may differ from the requested name if `FaceDisplay` fell back to `idle` for missing art).

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/mcp/test_server.py`:

```python
class FakeDisplayWithSet:
    def __init__(self):
        self.current_face = None
        self.requested: list[str] = []

    async def set_face(self, name):
        self.requested.append(name)
        self.current_face = name


def test_set_face_calls_through_and_reports_the_actual_face():
    async def main():
        deps = make_deps()
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        result = await _call(server, "set_face", name="happy")
        assert result == {"ok": True, "face": "happy"}
        assert deps.display.requested == ["happy"]

    asyncio.run(main())


def test_set_face_accepts_talk_prefixed_names_for_the_reflex_caller():
    async def main():
        deps = make_deps()
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        result = await _call(server, "set_face", name="talk_happy")
        assert result == {"ok": True, "face": "talk_happy"}

    asyncio.run(main())


def test_set_face_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        result = await _call(server, "set_face", name="happy")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.display.requested == []

    asyncio.run(main())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -k set_face -v`
Expected: FAIL — `set_face` tool doesn't exist yet.

- [ ] **Step 3: Add the tool**

In `bridge/milo_bridge/mcp/server.py`, add before `return server`:

```python
    @server.tool()
    async def set_face(name: str) -> dict:
        """Show a preset face expression: happy, sad, angry, surprised,
        sleepy, love, excited, confused, thinking, or idle."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        await deps.display.set_face(name)
        return {"ok": True, "face": deps.display.current_face}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/mcp/server.py bridge/tests/mcp/test_server.py
git commit -m "feat(bridge): add set_face MCP tool"
```

---

## Task 8: Bridge — MCP server, `speak`

**Files:**
- Modify: `bridge/milo_bridge/mcp/server.py`
- Modify: `bridge/tests/mcp/test_server.py`

**Interfaces:**
- Consumes: `synth_pcm`, `tts_available` from `bridge/milo_bridge/webapp/api/speak.py` (existing, unmodified — reused directly, not duplicated).
- Produces: `speak(text: str) -> dict` tool, gated, capped at 500 chars.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/mcp/test_server.py`:

```python
class FakeAudio:
    def __init__(self):
        self.played: list[bytes] = []

    def play_pcm(self, pcm):
        self.played.append(pcm)


def test_speak_synthesizes_and_plays(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()

        async def fake_synth(text, timeout_s=10.0):
            return b"pcmbytes"

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", fake_synth)

        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hello there")
        assert result == {"ok": True}
        assert deps.audio.played == [b"pcmbytes"]

    asyncio.run(main())


def test_speak_truncates_to_500_chars(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()
        seen = {}

        async def fake_synth(text, timeout_s=10.0):
            seen["text"] = text
            return b"x"

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", fake_synth)

        server = build_mcp_server(deps)
        await _call(server, "speak", text="a" * 600)
        assert len(seen["text"]) == 500

    asyncio.run(main())


def test_speak_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        deps.audio = FakeAudio()
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "web-control-active"}

    asyncio.run(main())


def test_speak_reports_tts_unavailable(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()
        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: False)
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "tts-unavailable"}

    asyncio.run(main())


def test_speak_reports_synthesis_failure(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()

        async def fake_synth(text, timeout_s=10.0):
            return None

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", fake_synth)
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "tts-failed"}

    asyncio.run(main())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -k speak -v`
Expected: FAIL — `speak` tool doesn't exist yet.

- [ ] **Step 3: Add the tool**

In `bridge/milo_bridge/mcp/server.py`, add the import at the top:

```python
from ..webapp.api.speak import synth_pcm, tts_available
```

Add the tool before `return server`:

```python
    @server.tool()
    async def speak(text: str) -> dict:
        """Say something out loud right now, independent of the normal
        spoken conversational reply -- for something unprompted."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        if deps.audio is None:
            return {"ok": False, "error": "audio unavailable"}
        if not tts_available():
            return {"ok": False, "error": "tts-unavailable"}
        clean = text[:500].strip()
        if not clean:
            return {"ok": False, "error": "empty text"}
        pcm = await synth_pcm(clean)
        if pcm is None:
            return {"ok": False, "error": "tts-failed"}
        deps.audio.play_pcm(pcm)
        return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/mcp/test_server.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/mcp/server.py bridge/tests/mcp/test_server.py
git commit -m "feat(bridge): add speak MCP tool, reusing the dashboard's espeak-ng path"
```

---

## Task 9: Bridge — dependencies + wire the MCP server into `main.py`

**Files:**
- Modify: `bridge/pyproject.toml`
- Modify: `bridge/milo_bridge/main.py`
- Test: `bridge/tests/test_main_mcp_wiring.py`

**Interfaces:**
- Produces: `_start_mcp(cfg, gait, runner, imu, broker, servos, display, audio) -> None` (coroutine) in `main.py`, started as a task in `main()` and cancelled in the `finally:` block, matching `gait_task`/`backup_task`.

- [ ] **Step 1: Add dependencies**

In `bridge/pyproject.toml`, add to the main `dependencies` list (not the `pi`-only extra — the MCP server and its deps are pure Python and should be available in dev/test too):

```toml
dependencies = [
    "milo-common",
    "numpy>=1.26",
    "pillow>=10",
    "websockets>=12",
    "zeroconf>=0.130",
    "aiohttp>=3.9",
    "mcp>=1.9",
    "uvicorn>=0.30",
    "starlette>=0.37",
]
```

Also add `httpx` to the `dev` extra (needed by `starlette.testclient.TestClient`, used in Task 2's auth tests — bridge production code never calls Ollama/httpx directly, so it belongs in `dev`, not the main list):

```toml
dev = ["pytest>=8", "pytest-asyncio>=0.23", "onnx>=1.16", "onnxruntime>=1.17", "httpx>=0.27"]
```

Run: `cd bridge && pip install -e ".[dev]"`

- [ ] **Step 2: Write the failing test**

```python
# bridge/tests/test_main_mcp_wiring.py
import asyncio

import pytest

from milo_bridge.config import BridgeConfig
from milo_bridge.main import _start_mcp


class FakeGait:
    mode = "balanced"
    backend = "cpg"

    def set_velocity_command(self, *a):
        pass


class FakeBroker:
    owner = "none"

    def allow_brain_motion(self):
        return True


def test_start_mcp_serves_and_is_cancellable(tmp_path):
    async def main():
        cfg = BridgeConfig(data_dir=str(tmp_path), mcp_port=0)  # port 0 -- OS picks a free one
        task = asyncio.create_task(
            _start_mcp(cfg, FakeGait(), runner=None, imu=None, broker=FakeBroker(), servos=None, display=None, audio=None)
        )
        await asyncio.sleep(0.2)  # give uvicorn a moment to start
        assert not task.done()  # still serving, didn't crash
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/test_main_mcp_wiring.py -v`
Expected: FAIL with `ImportError: cannot import name '_start_mcp' from 'milo_bridge.main'`

- [ ] **Step 4: Wire it up**

In `bridge/milo_bridge/main.py`, add imports near the top (alongside the other lazily-imported webapp pieces, which are already imported inside `main()` rather than at module level — follow that same lazy-import convention):

```python
async def _start_mcp(cfg, gait, runner, imu, broker, servos, display, audio) -> None:
    """Start the movement/face/speech/IMU MCP server; logs and swallows any
    startup failure, matching every other optional subsystem in this file."""
    try:
        import uvicorn
        from milo_common.auth import PairedStore

        from .mcp.auth import BearerAuthMiddleware
        from .mcp.deps import McpDeps
        from .mcp.server import build_mcp_server

        store = PairedStore(cfg.paired_path)
        deps = McpDeps(
            gait=gait, runner=runner, imu=imu, broker=broker,
            servos=servos, display=display, audio=audio,
        )
        app = build_mcp_server(deps).streamable_http_app()
        wrapped = BearerAuthMiddleware(app, store)
        config = uvicorn.Config(wrapped, host="0.0.0.0", port=cfg.mcp_port, log_level="warning")
        log.info("MCP server on http://0.0.0.0:%d/mcp", cfg.mcp_port)
        await uvicorn.Server(config).serve()
    except Exception:
        log.exception("MCP server failed to start — continuing without it")
```

Add this function at module scope in `main.py` (near `_nightly_backup`, since both are "background task the main coroutine spawns and cancels on shutdown"). Then in `main()`, after `gait_task = asyncio.create_task(gait.run())`:

```python
    mcp_task = asyncio.create_task(
        _start_mcp(cfg, gait, runner, imu, broker, motion_servos, display, audio)
    )
```

And in the `finally:` block, alongside `gait_task.cancel()`:

```python
        gait_task.cancel()
        backup_task.cancel()
        mcp_task.cancel()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/test_main_mcp_wiring.py -v`
Expected: PASS

Note: `PairedStore(cfg.paired_path)` here is a *separate* instance from the one `SessionManager` builds internally for robot↔brain pairing — both read/write the same JSON file, which is safe since the MCP server only ever calls `.token_for()` (read-only) during a live session. A peer added via `mcp-pair` (or a brand-new brain pairing) after the bridge process started won't be visible to this already-loaded copy until the bridge restarts — acceptable for v1 (same "restart to pick up changes" tradeoff already used elsewhere in this file, e.g. `MotionService.restart()`).

- [ ] **Step 6: Commit**

```bash
git add bridge/pyproject.toml bridge/milo_bridge/main.py bridge/tests/test_main_mcp_wiring.py
git commit -m "feat(bridge): start the MCP server alongside the web dashboard"
```

---

## Task 10: Bridge — remove `move`/`face` handling from `RobotSession`

**Files:**
- Modify: `bridge/milo_bridge/net/session.py`
- Test: check for an existing `bridge/tests/net/test_session.py` or similar (`Glob bridge/tests/**/test_session*.py`) and modify it; if none exists, this task's tests live inline below.

**Interfaces:**
- Removes: `RobotSession._handle_cmd`, `RobotSession._turn`, `RobotSession._start_pose`, and the `T_CMD` branch in `RobotSession.dispatch`. `RobotSession.__init__` drops the now-unused `runner`/`gait` parameters *only if* nothing else in this class uses them — check first (see Step 1).

- [ ] **Step 1: Check what else uses `runner`/`gait`/`_pose_task` on `RobotSession`**

Run: `cd bridge && python -c "import ast, pathlib; src = pathlib.Path('milo_bridge/net/session.py').read_text(); print(src)"` (or just re-read the file). Confirm `self._runner`, `self._gait`, `self._pose_task` are used *only* inside `_handle_cmd`/`_turn`/`_start_pose` and nowhere else in `RobotSession` (e.g. `run()`, `dispatch()`, `_handle_graph()`). Based on the current file, they are — so `runner` and `gait` constructor parameters, and the `_pose_task` attribute, are removed entirely along with the three methods.

- [ ] **Step 2: Write the failing test**

Find or create `bridge/tests/net/test_session.py`. If a test file for `RobotSession` already exists, add this test to it; otherwise create it:

```python
# bridge/tests/net/test_session.py (create __init__.py in bridge/tests/net/ too if missing)
import asyncio

from milo_common import protocol
from milo_common.testing import socket_pair

from milo_bridge.net.session import RobotSession


class FakeDisplay:
    def __init__(self):
        self.faces: list[str] = []

    async def set_face(self, name, mode=None):
        self.faces.append(name)


def test_cmd_with_move_or_face_is_ignored_not_crashed():
    async def main():
        robot_sock, brain_sock = socket_pair()
        display = FakeDisplay()
        session = RobotSession(robot_sock, runner=None, display=display)
        task = asyncio.create_task(session.run())
        try:
            # A stale/legacy T_CMD carrying move+face must be silently
            # ignored -- the bridge no longer interprets either field.
            await brain_sock.send(protocol.T_CMD, face="happy", move={"pose": "wave"})
            await asyncio.sleep(0.05)
            assert display.faces == []
        finally:
            task.cancel()

    asyncio.run(main())
```

(This test asserts the *new* behavior — a `T_CMD` with `move`/`face` no longer does anything. `RobotSession(robot_sock, runner=None, display=display)` — note the constructor no longer takes `gait=`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/net/test_session.py -v`
Expected: FAIL — currently the test would actually pass functionally-speaking on the *old* code too (since `move`/`face` handling would run and change `display.faces`) — expected failure mode here is `display.faces == ["happy"]` (assertion fails because the OLD code DOES act on it). Confirm the test fails for that reason, not an import error.

- [ ] **Step 4: Remove the dead handling**

In `bridge/milo_bridge/net/session.py`:

- Remove the `gait` parameter from `RobotSession.__init__` and the `self._gait = gait` line.
- Remove the `self._pose_task: asyncio.Task | None = None` line.
- Change `dispatch()`:

```python
    async def dispatch(self, msg: protocol.Message) -> None:
        if msg.t == protocol.T_TTS:
            if self._audio is not None and msg.payload:
                self._audio.play_pcm(msg.payload)
        elif msg.t == protocol.T_GRAPH:
            await self._handle_graph(msg)
        else:
            log.debug("ignoring message type %r", msg.t)
```

- Delete `_handle_cmd`, `_turn`, and `_start_pose` entirely.
- `RobotSession.__init__`'s `broker` parameter and `self._broker` attribute are also now unused (they were only read inside the deleted `_handle_cmd`) — remove them too, along with the `runner` parameter *if* nothing else references `self._runner`. Re-check: `self._runner` was only used by the deleted methods — remove the `runner` parameter and attribute as well, and update `RobotSession.__init__`'s signature to just `(self, sock, *, display, media_hub=None, audio=None, graph_api=None)`.

- Update the one call site in `SessionManager._tick` (same file) that constructs `RobotSession(...)`, removing the now-gone `runner=`, `broker=`, `gait=` keyword arguments:

```python
                    session = RobotSession(
                        sock,
                        display=self._display,
                        media_hub=self._media_hub,
                        audio=self._audio,
                        graph_api=self._graph_api,
                    )
```

- `SessionManager.__init__` itself still needs to *keep* its own `runner`/`gait`/`broker` parameters — those are used elsewhere in `SessionManager` (`self._runner`/`self._gait`/`self._broker` — check: `self._broker.set_brain_connected(...)` in `_tick`, and `self._show_pin` uses `self._display`). Only the pass-through into `RobotSession(...)` is removed, not `SessionManager`'s own fields. Re-verify by reading the full updated file before moving on.

- Fix the test in Step 2: since `RobotSession.__init__` no longer accepts `runner=`, update the test to just `RobotSession(robot_sock, display=display)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/net/test_session.py -v && python -m pytest tests/ -v`
Expected: PASS across the whole bridge test suite (check for any other test referencing `RobotSession(..., runner=..., gait=..., broker=...)` or `_handle_cmd`/`_turn`/`_start_pose` and update those call sites the same way).

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/net/session.py bridge/tests/net/test_session.py
git commit -m "refactor(bridge): remove move/face handling from RobotSession, MCP replaces it"
```

---

## Task 11: Protocol — `mcp_port`/`mcp_url` on `Peer`, `PROTOCOL_VERSION` bump

**Files:**
- Modify: `common/milo_common/protocol.py`
- Modify: `common/milo_common/handshake.py`
- Modify: `common/tests/test_handshake.py`

**Interfaces:**
- Produces: `protocol.PROTOCOL_VERSION == 2`. `handshake.Peer` gains `mcp_port: int = 0` (raw value carried over the wire) and `mcp_url: str = ""` (computed later, brain-side only — see Task 13; `handshake.py` itself never sets this field). `robot_handshake(sock, robot_id, robot_name, store, show_pin=None, mcp_port=0)` sends `mcp_port` in its initial `T_HELLO`. `brain_handshake` reads `hello.get("mcp_port", 0)` into the returned `Peer`.

- [ ] **Step 1: Write the failing tests**

Append to `common/tests/test_handshake.py`:

```python
def test_mcp_port_travels_from_robot_to_brain(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=True)
    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store, mcp_port=8766),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store),
    )
    assert not isinstance(brain_result, Exception), brain_result
    assert brain_result.mcp_port == 8766
    assert brain_result.mcp_url == ""  # handshake never computes this -- see Task 13


def test_mcp_port_defaults_to_zero_when_omitted(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=True)
    rs, bs = socket_pair()
    _robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store),
    )
    assert brain_result.mcp_port == 0
```

`common/tests/test_handshake.py` already imports `socket_pair` (`from milo_common.testing import socket_pair`, used by the existing `stores`/`run_both`/`test_paired_mutual_auth_succeeds` etc.) — no new import needed for these two tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd common && python -m pytest tests/test_handshake.py -v`
Expected: FAIL — `robot_handshake()` doesn't accept `mcp_port=`, and/or `Peer` has no `mcp_port` attribute.

- [ ] **Step 3: Implement**

In `common/milo_common/protocol.py`, bump:

```python
PROTOCOL_VERSION = 2
```

In `common/milo_common/handshake.py`, update the `Peer` dataclass:

```python
@dataclass(frozen=True)
class Peer:
    """The authenticated party on the other end of the socket."""

    id: str
    name: str
    tier: str = ""
    mcp_port: int = 0   # the robot's MCP server port, carried in its T_HELLO
    mcp_url: str = ""   # computed brain-side from mcp_port + the connection's remote address (see server.py)
```

Update `robot_handshake`'s signature and its first `send`:

```python
async def robot_handshake(
    sock: MiloSocket,
    robot_id: str,
    robot_name: str,
    store: PairedStore,
    show_pin: Callable[[str], Awaitable[None]] | None = None,
    mcp_port: int = 0,
) -> Peer:
    """Run the robot side. ``show_pin`` renders the pairing PIN on the OLED;
    without it, unpaired brains are refused outright. ``mcp_port`` is this
    robot's movement/face/speech/IMU MCP server port, advertised to the
    brain so it can reach it without a second discovery mechanism."""
    await sock.send(
        protocol.T_HELLO, role="robot", robot_id=robot_id, name=robot_name,
        proto=PROTOCOL_VERSION, mcp_port=mcp_port,
    )
```

Update `brain_handshake`'s `Peer` construction (both places it constructs one — the `_robot_pairing`-completing branch inside `robot_handshake`... wait, `Peer` for the *robot's* view of the brain doesn't carry `mcp_port` — only `brain_handshake` (which returns a `Peer` representing the *robot*, as seen from the brain) needs to read `mcp_port` from the hello it received. Update just that one line in `brain_handshake`:

```python
    hello = await _expect(sock, protocol.T_HELLO)
    peer = Peer(id=hello["robot_id"], name=hello.get("name", ""), mcp_port=hello.get("mcp_port", 0))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd common && python -m pytest tests/test_handshake.py -v`
Expected: PASS (all tests, including the two new ones)

- [ ] **Step 5: Commit**

```bash
git add common/milo_common/protocol.py common/milo_common/handshake.py common/tests/test_handshake.py
git commit -m "feat(common): carry mcp_port in T_HELLO, bump PROTOCOL_VERSION to 2"
```

---

## Task 12: Bridge — `SessionManager` advertises its `mcp_port`

**Files:**
- Modify: `bridge/milo_bridge/net/session.py`
- Modify: `bridge/tests/net/test_session.py` (or wherever `SessionManager` is tested — `Grep "class SessionManager" -l` under `bridge/tests/` to find it if it's a different file)

**Interfaces:**
- `SessionManager._tick`'s `robot_handshake(...)` call passes `mcp_port=self._cfg.mcp_port`.

- [ ] **Step 1: Write the failing test**

```python
# add to whichever test file exercises SessionManager (e.g. bridge/tests/net/test_session_manager.py)
import asyncio

from milo_common.auth import PairedStore, derive_token
from milo_common.handshake import brain_handshake
from milo_common.testing import socket_pair

from milo_bridge.config import BridgeConfig
from milo_bridge.net.session import SessionManager


def test_session_manager_advertises_configured_mcp_port(tmp_path, monkeypatch):
    async def main():
        cfg = BridgeConfig(data_dir=str(tmp_path), robot_id="milo-1", robot_name="milo", mcp_port=9001)
        token = derive_token("123456", cfg.robot_id, "brain-1")
        PairedStore(cfg.paired_path).add("brain-1", token)

        rs, bs = socket_pair()

        class OneShotConnect:
            def __init__(self, sock):
                self._sock = sock
                self._used = False

            def __call__(self, url):
                return self

            async def __aenter__(self):
                return self._sock._ws  # the raw fake ws MiloSocket wraps

            async def __aexit__(self, *exc):
                pass

        class FakeDiscovery:
            def start(self):
                pass

            def stop(self):
                pass

            def snapshot(self):
                return []

        # Simpler: drive robot_handshake directly against the brain side and
        # assert what it received, instead of the full discovery/connect
        # plumbing (which needs a real BrainRecord/select_brain wiring not
        # worth faking here).
        brain_store = PairedStore(tmp_path / "brain_paired.json")
        brain_store.add(cfg.robot_id, token)

        from milo_common.handshake import robot_handshake

        robot_task = asyncio.create_task(
            robot_handshake(rs, cfg.robot_id, cfg.robot_name, PairedStore(cfg.paired_path), mcp_port=cfg.mcp_port)
        )
        peer = await brain_handshake(bs, "brain-1", "d", "large", brain_store)
        await robot_task
        assert peer.mcp_port == 9001

    asyncio.run(main())
```

This test intentionally bypasses `SessionManager._tick`'s discovery/connect machinery (faking `websockets.connect` realistically is more effort than it's worth) and instead verifies the *value* `SessionManager` would pass through — see Step 3's actual code change, which is a one-line addition. The real assurance that `_tick` passes `mcp_port` correctly is a direct read of the modified line in Step 3; this test exists to pin the config-to-handshake plumbing (`cfg.mcp_port` flows to `robot_handshake`) so a future refactor can't silently drop it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/net -k mcp_port -v`
Expected: This particular test actually exercises `robot_handshake` directly (already correct from Task 11) — it will PASS immediately once Task 11 is done. That's fine: this task's real change is in `SessionManager._tick`, which the test above doesn't reach. Skip asserting a "failing" state for this specific test; instead, confirm the change by reading the modified line in Step 3 and running the full bridge suite in Step 4.

- [ ] **Step 3: Update `SessionManager._tick`**

In `bridge/milo_bridge/net/session.py`, find:

```python
                peer = await robot_handshake(
                    sock,
                    self._cfg.robot_id,
                    self._cfg.robot_name,
                    self._store,
                    show_pin=self._show_pin,
                )
```

Change to:

```python
                peer = await robot_handshake(
                    sock,
                    self._cfg.robot_id,
                    self._cfg.robot_name,
                    self._store,
                    show_pin=self._show_pin,
                    mcp_port=self._cfg.mcp_port,
                )
```

- [ ] **Step 4: Run the full bridge test suite**

Run: `cd bridge && python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/net/session.py bridge/tests/net/test_session_manager.py
git commit -m "feat(bridge): advertise mcp_port during the robot->brain handshake"
```

---

## Task 13: Brain — resolve `mcp_url` from the incoming connection's remote address

**Files:**
- Modify: `brain/milo_brain/server.py`
- Modify: `brain/tests/test_server_integration.py`

**Interfaces:**
- `BrainServer._on_connection` computes `mcp_url = f"http://{host}:{peer.mcp_port}"` from `ws.remote_address[0]` and `peer.mcp_port`, and replaces `peer` (via `dataclasses.replace`) before calling `self._handler(sock, peer)`. Only done when `peer.mcp_port` is truthy.

- [ ] **Step 1: Write the failing test**

Append to `brain/tests/test_server_integration.py`:

```python
def test_peer_gets_mcp_url_from_the_connections_remote_address(tmp_path, paired_stores):
    robot_store, _ = paired_stores
    received: list = []
    done = asyncio.Event()

    async def handler(sock, peer):
        received.append(peer)
        done.set()

    async def main():
        server = make_server(tmp_path, handler)
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                await robot_handshake(sock, "milo-1", "milo", robot_store, mcp_port=8766)
                await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())
    assert received[0].mcp_url == "http://127.0.0.1:8766"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && python -m pytest tests/test_server_integration.py -k mcp_url -v`
Expected: FAIL — `received[0].mcp_url == ""` (handshake never sets it).

- [ ] **Step 3: Implement**

In `brain/milo_brain/server.py`, add `from dataclasses import replace` to the imports, and update `_on_connection`:

```python
    async def _on_connection(self, ws) -> None:
        sock = MiloSocket(ws)
        try:
            peer = await brain_handshake(
                sock,
                self._cfg.brain_id,
                self._cfg.name,
                self._cfg.tier,
                self._store,
                request_pin=self._request_pin if self.advertiser.pairing else None,
            )
        except HandshakeError as exc:
            log.warning("refused connection: %s", exc)
            await sock.close(4001, "auth failed")
            return
        if peer.mcp_port:
            host = ws.remote_address[0]
            peer = replace(peer, mcp_url=f"http://{host}:{peer.mcp_port}")
        log.info("robot connected: %s (%s)", peer.name, peer.id)
        self.connected_robot = peer
        try:
            await self._handler(sock, peer)
        except Exception as exc:
            log.info("robot session ended: %s: %s", type(exc).__name__, exc)
        finally:
            self.connected_robot = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_server_integration.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/server.py brain/tests/test_server_integration.py
git commit -m "feat(brain): resolve a connected robot's mcp_url from its remote address"
```

---

## Task 14: Brain — `MiloMcpClient` wrapper

**Files:**
- Create: `brain/milo_brain/mcp_client.py`
- Test: `brain/tests/test_mcp_client.py`

**Interfaces:**
- Produces: `MiloMcpClient(base_url: str, token: str, peer_id: str)` with `async connect()`, `async close()`, `async list_tools() -> list[dict]` (Ollama tool-schema format), `async call_tool(tool_name: str, **arguments) -> dict` — the parameter is named `tool_name`, not `name`, because tools like `run_pose`/`set_mode`/`set_face` take an argument literally called `name`, which would otherwise collide. Pure helpers `_to_ollama_tool(tool) -> dict`, `_tool_result_to_dict(result) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_mcp_client.py
from types import SimpleNamespace

from milo_brain.mcp_client import _to_ollama_tool, _tool_result_to_dict


def test_to_ollama_tool_maps_mcp_tool_shape():
    tool = SimpleNamespace(
        name="walk",
        description="Continuous velocity walk.",
        inputSchema={"type": "object", "properties": {"vx": {"type": "number"}}, "required": ["vx"]},
    )
    assert _to_ollama_tool(tool) == {
        "type": "function",
        "function": {
            "name": "walk",
            "description": "Continuous velocity walk.",
            "parameters": {"type": "object", "properties": {"vx": {"type": "number"}}, "required": ["vx"]},
        },
    }


def test_to_ollama_tool_defaults_missing_description_to_empty_string():
    tool = SimpleNamespace(name="stop", description=None, inputSchema={"type": "object", "properties": {}})
    result = _to_ollama_tool(tool)
    assert result["function"]["description"] == ""


def test_tool_result_prefers_structured_content():
    result = SimpleNamespace(structuredContent={"ok": True}, content=[])
    assert _tool_result_to_dict(result) == {"ok": True}


def test_tool_result_falls_back_to_json_text_block():
    block = SimpleNamespace(type="text", text='{"ok": true, "mode": "raw"}')
    result = SimpleNamespace(structuredContent=None, content=[block])
    assert _tool_result_to_dict(result) == {"ok": True, "mode": "raw"}


def test_tool_result_falls_back_to_plain_text_when_not_json():
    block = SimpleNamespace(type="text", text="not json")
    result = SimpleNamespace(structuredContent=None, content=[block])
    assert _tool_result_to_dict(result) == {"text": "not json"}


def test_tool_result_empty_when_nothing_usable():
    result = SimpleNamespace(structuredContent=None, content=[])
    assert _tool_result_to_dict(result) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_mcp_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_brain.mcp_client'`

- [ ] **Step 3: Write the implementation**

```python
# brain/milo_brain/mcp_client.py
"""Thin async wrapper over the official MCP SDK's Streamable HTTP client,
scoped to one robot's movement/face/speech/IMU MCP server for the lifetime
of one cognition session (see session.py's CognitionSessionFactory).
"""
from __future__ import annotations

import contextlib
import json
from typing import Any


def _to_ollama_tool(tool: Any) -> dict:
    """``tool``: an mcp.types.Tool (name, description, inputSchema)."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


def _tool_result_to_dict(result: Any) -> dict:
    """``result``: an mcp.types.CallToolResult. Structured content wins;
    falls back to parsing the first text block as JSON, else wraps it as
    {"text": ...} so a caller always gets a dict back."""
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    for block in getattr(result, "content", []):
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"text": block.text}
    return {}


class MiloMcpClient:
    """One connection to a single robot's bridge MCP server, held open for
    the lifetime of one RobotCognitionSession."""

    def __init__(self, base_url: str, token: str, peer_id: str):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._peer_id = peer_id
        self._stack: contextlib.AsyncExitStack | None = None
        self._session: Any = None

    async def connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._stack = contextlib.AsyncExitStack()
        headers = {"Authorization": f"Bearer {self._token}", "X-Milo-Peer": self._peer_id}
        read, write, _ = await self._stack.enter_async_context(
            streamablehttp_client(f"{self._base_url}/mcp", headers=headers)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def list_tools(self) -> list[dict]:
        result = await self._session.list_tools()
        return [_to_ollama_tool(tool) for tool in result.tools]

    async def call_tool(self, tool_name: str, **arguments: Any) -> dict:
        # Parameter deliberately named tool_name, not name -- several tools
        # (run_pose, set_mode, set_face) take a kwarg literally called
        # `name`, which would collide with a same-named parameter here
        # (e.g. call_tool("set_face", name="excited") would raise
        # "got multiple values for argument 'name'" otherwise).
        result = await self._session.call_tool(tool_name, arguments)
        return _tool_result_to_dict(result)
```

Note: `connect()`/`close()`/`list_tools()`/`call_tool()` are thin pass-throughs to the real SDK's network transport and can't be meaningfully unit-tested without a live MCP server — that's covered by the pure-function tests above (the translation logic) plus manual end-to-end verification once Task 9's bridge server is running on real/loopback hardware (flagged in the spec's "cannot verify off-hardware" section). Downstream consumers (Task 16's `CognitionAgent`) are tested against a hand-written fake implementing the same `list_tools`/`call_tool` shape, never this real class.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_mcp_client.py -v`
Expected: PASS (6 tests). Add `mcp>=1.9` to `brain/pyproject.toml`'s main `dependencies` list first (needed for the `connect()` method's imports to resolve, even though these particular tests don't call it) — `pip install -e ".[dev]"` afterward.

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/mcp_client.py brain/tests/test_mcp_client.py brain/pyproject.toml
git commit -m "feat(brain): add MiloMcpClient wrapper over the MCP SDK's Streamable HTTP client"
```

---

## Task 15: Brain — `OllamaClient.chat` becomes tools-aware

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py` (modify existing `FakeLlm`/tests that exercise `OllamaClient` directly, if any — check first with `Grep OllamaClient brain/tests/`)

**Interfaces:**
- Changes: `OllamaClient.chat(system: str, messages: list[dict], tools: list[dict] | None = None) -> dict` — previously returned `str` (the message content); now returns the full `message` dict (`{"role": ..., "content": ..., "tool_calls": [...]}`, `tool_calls` only present when the model requested them). When `tools` is falsy, `format: "json"` is still requested (today's behavior); when `tools` is given, `format` is omitted — Ollama's tool-calling and its strict JSON-format mode aren't used together.

- [ ] **Step 1: Write the failing tests**

Since `OllamaClient.chat` talks to a real Ollama server via `httpx`, its own behavior is exercised through a monkeypatched `httpx.AsyncClient.post`. Add to `brain/tests/test_agent.py` (or a new `brain/tests/test_ollama_client.py` if you prefer keeping `OllamaClient` tests separate from `CognitionAgent` tests — either is fine; this plan assumes the former for locality with the existing file):

```python
# add near the top of brain/tests/test_agent.py
import httpx

from milo_brain.llm.agent import OllamaClient


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_chat_without_tools_requests_json_format(monkeypatch):
    captured = {}

    async def fake_post(self, url, json):
        captured.update(json)
        return _FakeResponse({"message": {"role": "assistant", "content": "hi"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OllamaClient()
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "hey"}])
    assert captured["format"] == "json"
    assert "tools" not in captured
    assert message == {"role": "assistant", "content": "hi"}


def test_chat_with_tools_omits_json_format_and_forwards_tools(monkeypatch):
    captured = {}

    async def fake_post(self, url, json):
        captured.update(json)
        return _FakeResponse({"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "walk", "arguments": {"vx": 0.1}}}
        ]}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = OllamaClient()
    tools = [{"type": "function", "function": {"name": "walk", "description": "", "parameters": {}}}]
    message = asyncio_run_chat(client, "sys", [{"role": "user", "content": "walk forward"}], tools=tools)
    assert "format" not in captured
    assert captured["tools"] == tools
    assert message["tool_calls"][0]["function"]["name"] == "walk"


def asyncio_run_chat(client, system, messages, tools=None):
    import asyncio
    return asyncio.run(client.chat(system, messages, tools=tools))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_agent.py -k "chat_without_tools or chat_with_tools" -v`
Expected: FAIL — current `chat()` doesn't accept `tools=` and returns a string, not a dict.

- [ ] **Step 3: Update `OllamaClient.chat`**

In `brain/milo_brain/llm/agent.py`, replace:

```python
    async def chat(self, system: str, messages: list[dict]) -> str:
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "format": "json",
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]
```

with:

```python
    async def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Returns the raw assistant message dict (``content`` and, if the
        model requested one or more tool calls, ``tool_calls``). Ollama's
        strict JSON-format mode and its tool-calling mode aren't used
        together, so ``format: "json"`` is only requested when no tools are
        offered -- the final tool-calling turn's JSON-ness instead relies on
        SYSTEM_PROMPT's instructions plus parse_llm_json's existing
        tolerance for stray/non-strict text."""
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        else:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_agent.py -v`
Expected: The two new tests PASS. Every *other* test in `test_agent.py` will now FAIL, because `FakeLlm.chat` and every assertion downstream still assumes the old `str`-returning, `move`/`face`-carrying contract — that's expected and is exactly what Task 16 fixes. Confirm specifically that `test_chat_without_tools_requests_json_format` and `test_chat_with_tools_omits_json_format_and_forwards_tools` pass; leave the rest red until Task 16.

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat(brain): make OllamaClient.chat tools-aware, returning the full message"
```

---

## Task 16: Brain — `CognitionAgent` tool-calling loop, shrunk schema, naming-flow direct calls

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Modify: `brain/tests/test_agent.py` (full rewrite of the fixtures/tests below `# --- agent flows ---`)

**Interfaces:**
- Changes: `AgentResult` drops `face`/`move` (keeps `reply: str`, `facts: list[str] = []`, `new_person_name: str | None = None`). `sanitize(data: dict) -> AgentResult` drops all face/move validation. `VALID_FACES` becomes the tool-guidance list embedded in `SYSTEM_PROMPT`, not a JSON-field validator. `CognitionAgent.__init__(self, llm, graph, mcp=None)`. `CognitionAgent.on_utterance` runs a bounded (`MAX_TOOL_ROUNDS = 4`) tool-calling loop against `self._llm.chat(..., tools=...)`, dispatching each `tool_call` through `self._mcp.call_tool(name, **arguments)`. The unknown-person naming flow makes direct `self._mcp.call_tool("set_face", ...)` / `("run_pose", ...)` calls instead of returning `face`/`move` fields.

- [ ] **Step 1: Write the failing tests**

Replace the `# --- agent flows ---` section of `brain/tests/test_agent.py` (and update `FakeLlm`/`test_sanitize_rejects_invalid_face_and_move` above it) with:

```python
import json


class FakeLlm:
    """Each entry in ``turns`` is one raw message dict to return, in order,
    across the tool-calling loop's rounds."""

    def __init__(self, turns=None):
        self.turns = turns if turns is not None else [
            {"role": "assistant", "content": '{"reply": "Hello!", "facts": []}'}
        ]
        self.calls: list[dict] = []

    async def chat(self, system, messages, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.turns.pop(0)


class FakeMcp:
    def __init__(self, tools=None):
        self._tools = tools or [{"type": "function", "function": {"name": "run_pose", "description": "", "parameters": {}}}]
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, tool_name, **arguments):
        # Parameter named tool_name, not name -- set_face/run_pose/set_mode
        # all take a kwarg literally called `name`, which would collide.
        self.calls.append((tool_name, arguments))
        return {"ok": True}


def test_sanitize_drops_face_and_move_keeps_reply_and_facts():
    result = sanitize({"reply": "x", "facts": [1, " ok "], "face": "ignored", "move": "ignored"})
    assert result.reply == "x"
    assert result.facts == ["1", " ok "]
    assert not hasattr(result, "face")
    assert not hasattr(result, "move")


# --- agent flows -------------------------------------------------------------

def test_known_person_gets_contextual_reply_with_no_tool_calls():
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "Hi Daham!", "facts": ["Daham has an exam tomorrow"]}'}])
    graph = FakeGraph()
    mcp = FakeMcp()
    agent = CognitionAgent(llm, graph, mcp)

    result = asyncio.run(agent.on_utterance("I have an exam tomorrow", DAHAM, None))
    assert result.reply == "Hi Daham!"

    sent = str(llm.calls[0]["messages"])
    assert "likes robots" in sent and "met Daham yesterday" in sent
    ops = [op for op, _ in graph.calls]
    assert "upsert_node" in ops and "upsert_edge" in ops


def test_tool_calls_are_executed_and_looped_until_a_final_reply():
    llm = FakeLlm([
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "run_pose", "arguments": {"name": "wave"}}}
        ]},
        {"role": "assistant", "content": '{"reply": "Done waving!", "facts": []}'},
    ])
    mcp = FakeMcp()
    agent = CognitionAgent(llm, FakeGraph(), mcp)

    result = asyncio.run(agent.on_utterance("wave at me", DAHAM, None))
    assert result.reply == "Done waving!"
    assert mcp.calls == [("run_pose", {"name": "wave"})]
    assert len(llm.calls) == 2
    # Round 2's messages include the tool call and its result.
    round_two_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in round_two_messages)


def test_tool_loop_gives_up_gracefully_after_max_rounds():
    keep_calling = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "run_pose", "arguments": {"name": "wave"}}}
    ]}
    llm = FakeLlm([keep_calling, keep_calling, keep_calling, keep_calling])
    mcp = FakeMcp()
    agent = CognitionAgent(llm, FakeGraph(), mcp)

    result = asyncio.run(agent.on_utterance("wave forever", DAHAM, None))
    assert result.reply  # some graceful fallback reply, not a crash
    assert len(llm.calls) == 4  # MAX_TOOL_ROUNDS


def test_on_utterance_works_without_an_mcp_client():
    llm = FakeLlm([{"role": "assistant", "content": '{"reply": "hi", "facts": []}'}])
    agent = CognitionAgent(llm, FakeGraph(), mcp=None)
    result = asyncio.run(agent.on_utterance("hello", DAHAM, None))
    assert result.reply == "hi"
    assert llm.calls[0]["tools"] is None


def test_tool_schemas_are_fetched_once_and_cached_across_utterances():
    class CountingMcp(FakeMcp):
        def __init__(self):
            super().__init__()
            self.list_tools_calls = 0

        async def list_tools(self):
            self.list_tools_calls += 1
            return await super().list_tools()

    llm = FakeLlm([
        {"role": "assistant", "content": '{"reply": "hi", "facts": []}'},
        {"role": "assistant", "content": '{"reply": "hi again", "facts": []}'},
    ])
    mcp = CountingMcp()
    agent = CognitionAgent(llm, FakeGraph(), mcp)
    asyncio.run(agent.on_utterance("hello", DAHAM, None))
    asyncio.run(agent.on_utterance("hello again", DAHAM, None))
    assert mcp.list_tools_calls == 1  # fetched once at first use, not per utterance


def test_unknown_person_flow_sets_face_directly_via_mcp():
    mcp = FakeMcp()
    agent = CognitionAgent(FakeLlm(), FakeGraph(), mcp)

    first = asyncio.run(agent.on_utterance("hello there", None, "ZmFrZQ=="))
    assert "name" in first.reply.lower()
    assert ("set_face", {"name": "surprised"}) in mcp.calls

    second = asyncio.run(agent.on_utterance("My name is Sarah", None, "ZmFrZQ=="))
    assert second.new_person_name == "Sarah"
    assert ("set_face", {"name": "excited"}) in mcp.calls
    assert ("run_pose", {"name": "wave"}) in mcp.calls


def test_naming_flow_reprompts_and_sets_confused_face():
    mcp = FakeMcp()
    agent = CognitionAgent(FakeLlm(), FakeGraph(), mcp)
    asyncio.run(agent.on_utterance("hi", None, None))
    retry = asyncio.run(agent.on_utterance("ehh whatever who cares honestly like", None, None))
    assert "name" in retry.reply.lower()
    assert ("set_face", {"name": "confused"}) in mcp.calls
    done = asyncio.run(agent.on_utterance("I'm Bob", None, None))
    assert done.new_person_name == "Bob"


def test_empty_transcript_is_ignored():
    result = asyncio.run(CognitionAgent(FakeLlm(), FakeGraph(), FakeMcp()).on_utterance("  ", DAHAM, None))
    assert result.reply == ""
```

Remove `test_known_person_gets_contextual_reply_and_facts_written` (superseded by `test_known_person_gets_contextual_reply_with_no_tool_calls`), `test_unknown_person_triggers_naming_flow` (superseded by `test_unknown_person_flow_sets_face_directly_via_mcp`), and the old `test_sanitize_rejects_invalid_face_and_move` (superseded by `test_sanitize_drops_face_and_move_keeps_reply_and_facts`). `test_extract_name_variants` and the parsing-helper tests (`test_parse_llm_json_*`) are untouched.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_agent.py -v`
Expected: FAIL — `CognitionAgent.__init__` doesn't take a third `mcp` argument yet, `AgentResult` still has `face`/`move`, `on_utterance` doesn't loop over tool calls.

- [ ] **Step 3: Rewrite `CognitionAgent` and friends**

In `brain/milo_brain/llm/agent.py`:

Replace `VALID_FACES` and the JSON-schema portion of `SYSTEM_PROMPT`:

```python
VALID_FACES = {
    "happy", "sad", "angry", "surprised", "sleepy", "love", "excited",
    "confused", "thinking", "idle",
}

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """You are Milo, a small four-legged robot with a camera, microphones and an OLED face.
You are curious, warm and a little playful. Keep replies to 1-3 short spoken sentences.

You have tools to move (walk, run_pose, turn, set_mode, reset, standby, relax,
hold, stop), check your own state (get_imu_state, get_status), change your
face (set_face -- one of: happy sad angry surprised sleepy love excited
confused thinking idle), and speak something unprompted (speak). Use them
when it fits the moment; check get_imu_state before an ambitious movement if
you're unsure about balance.

You know things from your on-board memory graph; context about the speaker
follows. Once you're done (with or without using any tools), reply ONLY with
JSON matching this schema:
{
  "reply": "what you say out loud",
  "facts": ["short new facts about the speaker worth remembering, empty if none"]
}"""
```

Replace `AgentResult`:

```python
@dataclass(frozen=True)
class AgentResult:
    reply: str
    facts: list[str] = field(default_factory=list)
    new_person_name: str | None = None
```

Replace `sanitize`:

```python
def sanitize(data: dict) -> AgentResult:
    facts = [str(f)[:300] for f in data.get("facts", []) if str(f).strip()][:5]
    return AgentResult(reply=str(data.get("reply", ""))[:600], facts=facts)
```

Replace `CognitionAgent.__init__` and `on_utterance`:

```python
class CognitionAgent:
    def __init__(self, llm, graph, mcp=None):
        """``llm``: object with async chat(system, messages, tools=None) -> dict.
        ``graph``: object with async call(op, **kwargs) -> dict (the wire API).
        ``mcp``: object with async list_tools() -> list[dict] and async
        call_tool(tool_name, **kwargs) -> dict (MiloMcpClient, or None if
        this robot has no reachable MCP server)."""
        self._llm = llm
        self._graph = graph
        self._mcp = mcp
        self._tools: list[dict] | None = None
        self._tools_loaded = False
        self._awaiting_name = False
        self._pending_embedding: str | None = None
        self._history: list[dict] = []

    async def _get_tools(self) -> list[dict] | None:
        """Fetches the bridge's MCP tool schemas once (at first use, not
        per utterance) and caches them for the rest of this session."""
        if not self._tools_loaded:
            self._tools = await self._mcp.list_tools() if self._mcp is not None else None
            self._tools_loaded = True
        return self._tools

    async def on_utterance(
        self,
        transcript: str,
        person: dict | None,
        face_embedding_b64: str | None,
    ) -> AgentResult:
        if not transcript.strip():
            return AgentResult(reply="")

        if self._awaiting_name:
            name = extract_name(transcript)
            if name:
                pending = self._pending_embedding
                self._awaiting_name = False
                self._pending_embedding = None
                request = {"type": "person", "props": {"name": name}}
                if pending:
                    request["embedding"] = pending
                created = await self._graph.call("upsert_node", **request)
                node_id = created.get("node", {}).get("id")
                await self._graph.call(
                    "upsert_node", type="event",
                    props={"text": f"met {name} for the first time"},
                )
                log.info("new person: %s (node %s)", name, node_id)
                if self._mcp is not None:
                    await self._mcp.call_tool("set_face", name="excited")
                    await self._mcp.call_tool("run_pose", name="wave")
                return AgentResult(
                    reply=f"Nice to meet you, {name}! I'll remember you.",
                    new_person_name=name,
                )
            if self._mcp is not None:
                await self._mcp.call_tool("set_face", name="confused")
            return AgentResult(reply="Sorry, I didn't catch your name — what should I call you?")

        if person is None:
            self._awaiting_name = True
            self._pending_embedding = face_embedding_b64
            if self._mcp is not None:
                await self._mcp.call_tool("set_face", name="surprised")
            return AgentResult(reply="Hi! I don't think we've met — what's your name?")

        context = await self._build_context(person)
        self._history.append({"role": "user", "content": transcript})
        self._history = self._history[-12:]
        messages = [
            {"role": "user", "content": f"[memory context]\n{context}"},
            *self._history,
        ]
        tools = await self._get_tools()

        result = AgentResult(reply="Sorry, I got a bit stuck there.")
        for _ in range(MAX_TOOL_ROUNDS):
            message = await self._llm.chat(SYSTEM_PROMPT, messages, tools=tools)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                result = sanitize(parse_llm_json(message.get("content", "")))
                break
            messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                arguments = fn.get("arguments") or {}
                if self._mcp is not None:
                    tool_result = await self._mcp.call_tool(name, **arguments)
                else:
                    tool_result = {"ok": False, "error": "mcp unavailable"}
                messages.append({"role": "tool", "name": name, "content": json.dumps(tool_result)})

        self._history.append({"role": "assistant", "content": result.reply})
        await self._write_facts(person, result.facts)
        return result
```

`_build_context` and `_write_facts` are unchanged. Add `import json` at the top of the file if not already present (it already is, per the existing `parse_llm_json`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_agent.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat(brain): CognitionAgent tool-calling loop replaces the move/face JSON fields"
```

---

## Task 17: Brain — session reflex wiring (turn-toward-speaker, talk/revert face) + MCP client construction

**Files:**
- Modify: `brain/milo_brain/session.py`
- Modify: `brain/tests/test_cognition_session.py`

**Interfaces:**
- `RobotCognitionSession.__init__` gains `mcp=None`. `_handle_segment` calls `self._mcp.call_tool("turn", direction=direction_mod.classify(bearing))` instead of `sock.send(T_CMD, move=...)`. `_respond` drops the `T_CMD` face/move send entirely, wraps TTS playback with `set_face("talk_" + current_face)` / `set_face(current_face)` via MCP, reading `current_face` from `get_status()`. `CognitionSessionFactory` builds a `PairedStore` + `MiloMcpClient` per session using `peer.mcp_url` and the shared pairing token, and passes it to both `CognitionAgent` and `RobotCognitionSession`.

- [ ] **Step 1: Write the failing tests**

Replace `FakeLlm` and the face/move-related bits of `brain/tests/test_cognition_session.py`:

```python
class FakeLlm:
    async def chat(self, system, messages, tools=None):
        return {"role": "assistant", "content": '{"reply": "Hey Daham!", "facts": []}'}


class FakeMcp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status = {"ok": True, "current_face": "happy"}

    async def list_tools(self):
        return []

    async def call_tool(self, tool_name, **arguments):
        # Parameter named tool_name, not name -- set_face takes a kwarg
        # literally called `name`, which would collide.
        self.calls.append((tool_name, arguments))
        if tool_name == "get_status":
            return self.status
        return {"ok": True}
```

Update `build_session` to accept and pass through a `FakeMcp`:

```python
def build_session(robot_side_answers, mcp=None):
    brain_sock, robot_sock = socket_pair()
    graph = GraphClient(brain_sock)
    mcp = mcp if mcp is not None else FakeMcp()
    session = RobotCognitionSession(
        brain_sock,
        Peer(id="milo-1", name="milo"),
        vad=VadSegmenter(is_speech=energy_detector, min_silence_ms=60, pre_roll_frames=2),
        asr=FakeAsr(),
        vision=FakeVision(),
        tts=FakeTts(),
        agent=CognitionAgent(FakeLlm(), graph, mcp),
        graph=graph,
        mcp=mcp,
    )

    async def robot(collected):
        while True:
            msg = await robot_sock.recv()
            if msg.t == protocol.T_GRAPH:
                op = msg.get("op")
                reply = robot_side_answers(op, dict(msg.header))
                await robot_sock.send(protocol.T_GRAPH_RESULT, id=msg.get("id"), **reply)
            else:
                collected.append(msg)

    return session, robot_sock, robot, mcp
```

Rewrite `test_full_hearing_to_speaking_loop` to assert against `mcp.calls` instead of `T_CMD` face frames:

```python
def test_full_hearing_to_speaking_loop():
    def answers(op, header):
        if op == "match_face":
            return {"match": {"id": 1, "type": "person", "props": {"name": "Daham"}},
                    "similarity": 0.98}
        if op == "neighbors":
            return {"neighbors": []}
        if op == "recent_events":
            return {"nodes": []}
        return {}

    async def main():
        session, robot_sock, robot, mcp = build_session(answers)
        collected: list = []
        session_task = asyncio.create_task(session.run())
        robot_task = asyncio.create_task(robot(collected))
        try:
            await robot_sock.send(protocol.T_VIDEO, payload=b"jpegbytes", ts=0.0)
            await asyncio.sleep(0.05)
            t = 0.0
            for loud in [True] * 10 + [False] * 5:
                await robot_sock.send(
                    protocol.T_AUDIO, payload=loud_frame() if loud else quiet_frame(), ts=t
                )
                t += 0.02
            for _ in range(300):
                if any(name == "set_face" and kwargs.get("name") == "happy" for name, kwargs in mcp.calls):
                    break
                await asyncio.sleep(0.02)
        finally:
            session_task.cancel()
            robot_task.cancel()
        return mcp.calls

    calls = asyncio.run(main())
    assert protocol.T_TTS  # sanity: module import still valid
    set_face_calls = [kwargs["name"] for name, kwargs in calls if name == "set_face"]
    assert "talk_happy" in set_face_calls
    assert set_face_calls[-1] == "happy"  # settles back to the non-talk variant after speaking
```

Add a new test for the turn-toward-speaker reflex:

```python
def test_off_center_speech_calls_turn_via_mcp():
    def answers(op, header):
        if op == "match_face":
            return {"match": {"id": 1, "type": "person", "props": {"name": "Daham"}}, "similarity": 0.98}
        if op == "neighbors":
            return {"neighbors": []}
        if op == "recent_events":
            return {"nodes": []}
        return {}

    async def main():
        session, robot_sock, robot, mcp = build_session(answers)
        collected: list = []
        session_task = asyncio.create_task(session.run())
        robot_task = asyncio.create_task(robot(collected))
        try:
            await robot_sock.send(protocol.T_VIDEO, payload=b"jpegbytes", ts=0.0)
            await asyncio.sleep(0.05)
            # A hard-panned stereo burst -- clearly off-center -- drives the
            # direction-of-arrival reflex regardless of the exact bearing math.
            t = 0.0
            rng_frames = []
            import numpy as np
            for i in range(10):
                left = np.random.default_rng(i).normal(0, 8000, FRAME).astype(np.int16)
                right = np.zeros(FRAME, dtype=np.int16)  # all energy on the left channel
                rng_frames.append(np.stack([left, right], axis=1).astype(np.int16).tobytes())
            for i, frame in enumerate(rng_frames + [quiet_frame()] * 5):
                await robot_sock.send(protocol.T_AUDIO, payload=frame, ts=t)
                t += 0.02
            for _ in range(300):
                if any(name == "turn" for name, _ in mcp.calls):
                    break
                await asyncio.sleep(0.02)
        finally:
            session_task.cancel()
            robot_task.cancel()
        return mcp.calls

    calls = asyncio.run(main())
    turn_calls = [kwargs for name, kwargs in calls if name == "turn"]
    assert turn_calls, f"no turn call made: {calls}"
    assert turn_calls[0]["direction"] in ("left", "right")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_cognition_session.py -v`
Expected: FAIL — `RobotCognitionSession.__init__` doesn't accept `mcp=`, `_handle_segment`/`_respond` still use `T_CMD`.

- [ ] **Step 3: Update `session.py`**

In `brain/milo_brain/session.py`:

```python
class RobotCognitionSession:
    def __init__(
        self,
        sock: MiloSocket,
        peer: Peer,
        *,
        vad: VadSegmenter,
        asr,
        vision,
        tts,
        agent: CognitionAgent,
        graph: GraphClient,
        mcp=None,
        face_match_threshold: float = 0.45,
    ):
        self._sock = sock
        self._peer = peer
        self._vad = vad
        self._asr = asr
        self._vision = vision
        self._tts = tts
        self._agent = agent
        self._graph = graph
        self._mcp = mcp
        self._threshold = face_match_threshold
        self._current_person: dict | None = None
        self._current_embedding_b64: str | None = None
        self._video_task: asyncio.Task | None = None
        self._segment_task: asyncio.Task | None = None
```

Update `_handle_segment`:

```python
    async def _handle_segment(self, segment) -> None:
        bearing = direction_mod.estimate_bearing(segment.stereo)
        direction = direction_mod.classify(bearing)
        if direction != "center" and self._mcp is not None:
            await self._mcp.call_tool("turn", direction=direction)

        transcript = await asyncio.to_thread(self._asr.transcribe, segment.mono)
        log.info("heard (%.2f): %s", transcript.confidence, transcript.text)
        if not transcript.text or transcript.confidence < 0.3:
            return

        result = await self._agent.on_utterance(
            transcript.text, self._current_person, self._current_embedding_b64
        )
        await self._respond(result)
```

Update `_respond`:

```python
    async def _respond(self, result: AgentResult) -> None:
        if not result.reply:
            return
        current_face = "idle"
        if self._mcp is not None:
            status = await self._mcp.call_tool("get_status")
            current_face = status.get("current_face") or "idle"
            await self._mcp.call_tool("set_face", name=f"talk_{current_face}")

        pcm = await asyncio.to_thread(self._tts.synthesize, result.reply)
        for chunk in chunk_pcm(pcm):
            await self._sock.send(protocol.T_TTS, payload=chunk)

        if self._mcp is not None:
            await self._mcp.call_tool("set_face", name=current_face)
```

Update `CognitionSessionFactory`:

```python
class CognitionSessionFactory:
    """Builds the production pipeline stack once and a session per robot."""

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

    async def handle(self, sock: MiloSocket, peer: Peer) -> None:
        from .mcp_client import MiloMcpClient

        graph = GraphClient(sock)
        mcp = None
        if peer.mcp_url:
            token = self._store.token_for(peer.id)
            if token is not None:
                mcp = MiloMcpClient(peer.mcp_url, token=token.hex(), peer_id=self._cfg.brain_id)
                await mcp.connect()
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
        try:
            await session.run()
        finally:
            if mcp is not None:
                await mcp.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_cognition_session.py -v`
Expected: PASS (all tests, including the two rewritten/new ones)

- [ ] **Step 5: Run the full brain test suite**

Run: `cd brain && python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add brain/milo_brain/session.py brain/tests/test_cognition_session.py
git commit -m "feat(brain): wire turn-toward-speaker and talk/revert-face reflexes through MCP"
```

---

## Task 18: Bridge — IMU characterization analysis math

**Files:**
- Create: `bridge/milo_bridge/characterize.py`
- Test: `bridge/tests/test_characterize.py`

**Interfaces:**
- Produces: `@dataclass MovementReport(name: str, peak_roll: float, peak_pitch: float, residual_roll: float, residual_pitch: float, peak_gyro: float, settle_time_s: float | None, safe: bool)`. `analyze_samples(name: str, samples: list[ImuSample], movement_end_s: float, safety_ceiling_deg: float = 45.0, settle_threshold_deg: float = 3.0, settle_hold_s: float = 0.5) -> MovementReport`, where `ImuSample = namedtuple/dataclass(t: float, roll: float, pitch: float, gyro: tuple[float, float, float])`.

- [ ] **Step 1: Write the failing tests**

```python
# bridge/tests/test_characterize.py
from milo_bridge.characterize import ImuSample, MovementReport, analyze_samples


def _samples(rows):
    return [ImuSample(t=t, roll=r, pitch=p, gyro=(gx, gy, gz)) for t, r, p, gx, gy, gz in rows]


def test_peak_and_residual_are_reported():
    samples = _samples([
        (0.0, 0.0, 0.0, 0, 0, 0),
        (0.5, 10.0, -5.0, 20, 0, 0),
        (1.0, 2.0, -1.0, 5, 0, 0),
        (1.5, 0.5, -0.2, 1, 0, 0),
    ])
    report = analyze_samples("wave", samples, movement_end_s=1.0)
    assert report.name == "wave"
    assert report.peak_roll == 10.0
    assert report.peak_pitch == 5.0  # magnitude, not signed
    assert report.residual_roll == 0.5
    assert report.residual_pitch == 0.2
    assert report.peak_gyro == 20.0


def test_settle_time_is_first_point_after_movement_end_holding_below_threshold():
    samples = _samples([
        (0.0, 0.0, 0.0, 0, 0, 0),
        (1.0, 15.0, 0.0, 0, 0, 0),   # during the movement, ignored for settle
        (1.1, 8.0, 0.0, 0, 0, 0),    # still above threshold, after movement_end_s=1.0
        (1.4, 2.0, 0.0, 0, 0, 0),    # under threshold...
        (2.0, 1.0, 0.0, 0, 0, 0),    # ...and stays under for >= settle_hold_s=0.5 from t=1.4
    ])
    report = analyze_samples("bow", samples, movement_end_s=1.0, settle_threshold_deg=3.0, settle_hold_s=0.5)
    assert report.settle_time_s == 1.4 - 1.0  # 0.4s after the movement ended


def test_settle_time_is_none_when_it_never_settles():
    samples = _samples([(t, 20.0, 0.0, 0, 0, 0) for t in [0.0, 0.5, 1.0, 1.5, 2.0]])
    report = analyze_samples("dance", samples, movement_end_s=0.5)
    assert report.settle_time_s is None


def test_unsafe_when_peak_tilt_exceeds_the_ceiling():
    samples = _samples([(0.0, 50.0, 0.0, 0, 0, 0), (0.5, 0.0, 0.0, 0, 0, 0)])
    report = analyze_samples("crab", samples, movement_end_s=0.5, safety_ceiling_deg=45.0)
    assert report.safe is False


def test_safe_when_within_the_ceiling():
    samples = _samples([(0.0, 10.0, 5.0, 0, 0, 0), (0.5, 1.0, 0.5, 0, 0, 0)])
    report = analyze_samples("look_up", samples, movement_end_s=0.5, safety_ceiling_deg=45.0)
    assert report.safe is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/test_characterize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.characterize'`

- [ ] **Step 3: Write the implementation**

```python
# bridge/milo_bridge/characterize.py
"""Offline IMU characterization: runs every pose/gait on real hardware and
reports how it actually behaves (peak tilt, settle time, safety), so
gait/servo tuning changes can be diffed against a known-good baseline
instead of trusting a human watching the robot.

This module is split into pure analysis (testable off-hardware) and
hardware orchestration (Task 19, real-robot only).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImuSample:
    t: float
    roll: float
    pitch: float
    gyro: tuple[float, float, float]


@dataclass(frozen=True)
class MovementReport:
    name: str
    peak_roll: float
    peak_pitch: float
    residual_roll: float
    residual_pitch: float
    peak_gyro: float
    settle_time_s: float | None
    safe: bool


def analyze_samples(
    name: str,
    samples: list[ImuSample],
    movement_end_s: float,
    safety_ceiling_deg: float = 45.0,
    settle_threshold_deg: float = 3.0,
    settle_hold_s: float = 0.5,
) -> MovementReport:
    peak_roll = max(abs(s.roll) for s in samples)
    peak_pitch = max(abs(s.pitch) for s in samples)
    peak_gyro = max(max(abs(g) for g in s.gyro) for s in samples)
    last = samples[-1]
    residual_roll = abs(last.roll)
    residual_pitch = abs(last.pitch)

    # First post-movement sample from which every remaining sample (to the
    # end of the recording) stays under the settle threshold on both axes,
    # provided the recording actually covers at least settle_hold_s of real
    # time from that point -- a candidate near the very end of a short
    # recording hasn't actually demonstrated it *stays* settled.
    settle_time_s = None
    after = [s for s in samples if s.t >= movement_end_s]
    for i, candidate in enumerate(after):
        tail = after[i:]
        stayed_under = all(
            abs(s.roll) < settle_threshold_deg and abs(s.pitch) < settle_threshold_deg for s in tail
        )
        if stayed_under and tail[-1].t - candidate.t >= settle_hold_s:
            settle_time_s = candidate.t - movement_end_s
            break

    safe = peak_roll <= safety_ceiling_deg and peak_pitch <= safety_ceiling_deg
    return MovementReport(
        name=name, peak_roll=peak_roll, peak_pitch=peak_pitch,
        residual_roll=residual_roll, residual_pitch=residual_pitch,
        peak_gyro=peak_gyro, settle_time_s=settle_time_s, safe=safe,
    )
```

Trace through `test_settle_time_is_first_point_after_movement_end_holding_below_threshold`'s fixture against this logic: post-movement samples are t=1.1 (8°), t=1.4 (2°), t=2.0 (1°). At `i=0` (t=1.1), the tail `[1.1, 1.4, 2.0]` includes 8° which fails `stayed_under`, so skip. At `i=1` (t=1.4), the tail `[1.4, 2.0]` is entirely under 3°, and `tail[-1].t - candidate.t == 2.0 - 1.4 == 0.6 >= settle_hold_s (0.5)` — settles at `1.4 - movement_end_s (1.0) == 0.4`, matching the test's expectation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/test_characterize.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/characterize.py bridge/tests/test_characterize.py
git commit -m "feat(bridge): add IMU characterization analysis (peak tilt, settle time, safety)"
```

---

## Task 19: Bridge — characterization orchestration + `cli.py characterize` subcommand

**Files:**
- Modify: `bridge/milo_bridge/characterize.py`
- Modify: `bridge/milo_bridge/cli.py`
- Test: `bridge/tests/test_characterize.py`

**Interfaces:**
- Produces: `async def run_characterization(servos, imu, runner, gait, names: list[str], out_dir: Path, safety_ceiling_deg: float = 45.0) -> list[MovementReport]` — runs each named pose (via `runner.run(name)`) or velocity sample (a few representative `walk`/`turn` commands via `gait.set_velocity_command` for a fixed duration), sampling IMU at 50 Hz, returning to `standby()` between each, skipping the rest of a pose's cycles (not applicable — poses run once each) but continuing past any single unsafe result. Writes `report.md` + `data.json` to `out_dir`. `cli.py` gains a `characterize` subcommand.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_characterize.py`:

```python
import asyncio
import json
from pathlib import Path

from milo_bridge.characterize import run_characterization


class FakeServos:
    pass


class FakeGait:
    def __init__(self):
        self.standby_calls = 0

    def standby(self):
        self.standby_calls += 1

    def set_velocity_command(self, vx, vy, yaw):
        pass


class FakeImu:
    def __init__(self, rolls):
        self._rolls = iter(rolls)
        self.last = 0.0

    def update(self):
        from milo_bridge.drivers.imu import ImuState

        try:
            self.last = next(self._rolls)
        except StopIteration:
            pass
        return ImuState(roll=self.last, pitch=0.0, yaw=0.0, gyro=(0.0, 0.0, 0.0), accel=(0.0, 0.0, 1.0))


class FakeRunner:
    def __init__(self):
        self.ran: list[str] = []

    async def run(self, name, cycles=None):
        self.ran.append(name)
        return True


def test_run_characterization_writes_report_and_calls_standby_between_moves(tmp_path):
    async def main():
        servos = FakeServos()
        gait = FakeGait()
        runner = FakeRunner()
        imu = FakeImu([5.0, 1.0, 0.5, 20.0, 3.0, 0.5])  # small, safe wiggles for two poses
        reports = await run_characterization(
            servos, imu, runner, gait, names=["wave", "bow"], out_dir=tmp_path,
            safety_ceiling_deg=45.0, settle_window_s=0.05, between_pause_s=0.0,
        )
        return reports

    reports = asyncio.run(main())
    assert [r.name for r in reports] == ["wave", "bow"]
    assert all(r.safe for r in reports)
    assert gait.standby_calls == 2

    report_md = (tmp_path / "report.md").read_text()
    assert "wave" in report_md and "bow" in report_md
    data = json.loads((tmp_path / "data.json").read_text())
    assert set(data.keys()) == {"wave", "bow"}


def test_run_characterization_flags_an_unsafe_pose_but_continues(tmp_path):
    async def main():
        servos, gait, runner = FakeServos(), FakeGait(), FakeRunner()
        imu = FakeImu([60.0, 0.5, 1.0, 0.5])  # first pose spikes past the 45deg ceiling
        return await run_characterization(
            servos, imu, runner, gait, names=["crab", "look_up"], out_dir=tmp_path,
            safety_ceiling_deg=45.0, settle_window_s=0.05, between_pause_s=0.0,
        )

    reports = asyncio.run(main())
    assert reports[0].name == "crab" and reports[0].safe is False
    assert reports[1].name == "look_up" and reports[1].safe is True
    assert runner.ran == ["crab", "look_up"]  # continued past the unsafe one
```

Both tests pass `settle_window_s=0.05` (instead of production's default `1.0`) and `between_pause_s=0.0` so the test suite doesn't spend real wall-clock seconds waiting on a fixed settle/pause window — mirrors this codebase's existing convention of injecting `clock`/`sleep` for testability (e.g. `PoseRunner`'s `sleep=asyncio.sleep` parameter, `GaitEngine`'s `clock=time.monotonic` parameter).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/test_characterize.py -k run_characterization -v`
Expected: FAIL with `ImportError: cannot import name 'run_characterization'`

- [ ] **Step 3: Write the implementation**

Append to `bridge/milo_bridge/characterize.py`:

```python
import asyncio
import json as json_module
from pathlib import Path

SAMPLE_HZ = 50


async def _sample_during(imu, coro, settle_window_s: float = 1.0, clock=None) -> tuple[list[ImuSample], float]:
    """Runs ``coro`` to completion while sampling ``imu`` at SAMPLE_HZ; keeps
    sampling for ``settle_window_s`` afterward so settle time has something
    to measure against. ``settle_window_s`` is injectable (default 1.0s on
    real hardware) so tests don't have to spend a real wall-clock second
    per movement."""
    import time

    clock = clock or time.monotonic
    t0 = clock()
    samples: list[ImuSample] = []
    task = asyncio.ensure_future(coro)

    async def sample_loop():
        while True:
            state = imu.update()
            samples.append(ImuSample(t=clock() - t0, roll=state.roll, pitch=state.pitch, gyro=state.gyro))
            await asyncio.sleep(1.0 / SAMPLE_HZ)

    sampler = asyncio.ensure_future(sample_loop())
    await task
    movement_end_s = clock() - t0
    await asyncio.sleep(settle_window_s)
    sampler.cancel()
    try:
        await sampler
    except asyncio.CancelledError:
        pass
    return samples, movement_end_s


def _write_report(reports: list[MovementReport], out_dir: Path, raw: dict[str, list[ImuSample]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["| pose | peak roll | peak pitch | settle (s) | safe |", "|---|---|---|---|---|"]
    for r in reports:
        settle = f"{r.settle_time_s:.2f}" if r.settle_time_s is not None else "never"
        lines.append(f"| {r.name} | {r.peak_roll:.1f} | {r.peak_pitch:.1f} | {settle} | {'yes' if r.safe else 'NO'} |")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    data = {
        name: [{"t": s.t, "roll": s.roll, "pitch": s.pitch, "gyro": list(s.gyro)} for s in samples]
        for name, samples in raw.items()
    }
    (out_dir / "data.json").write_text(json_module.dumps(data, indent=2), encoding="utf-8")


async def run_characterization(
    servos, imu, runner, gait, names: list[str], out_dir: Path,
    safety_ceiling_deg: float = 45.0, settle_window_s: float = 1.0, between_pause_s: float = 0.1,
) -> list[MovementReport]:
    reports: list[MovementReport] = []
    raw: dict[str, list[ImuSample]] = {}
    for name in names:
        samples, movement_end_s = await _sample_during(imu, runner.run(name), settle_window_s=settle_window_s)
        report = analyze_samples(name, samples, movement_end_s, safety_ceiling_deg=safety_ceiling_deg)
        reports.append(report)
        raw[name] = samples
        gait.standby()
        await asyncio.sleep(between_pause_s)  # let standby's own slew settle before the next pose
    _write_report(reports, Path(out_dir), raw)
    return reports
```

Add the CLI subcommand in `bridge/milo_bridge/cli.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

from .characterize import run_characterization
from .drivers.imu import Mpu6050
from .poses import POSES


async def _cmd_characterize(cfg: BridgeConfig, pose: str | None, out: Path | None) -> None:
    servos, display = _hardware(cfg)
    runner = PoseRunner(servos, display)
    imu = Mpu6050.from_hardware()
    print("Calibrating IMU gyro bias — keep the robot still...")
    await asyncio.to_thread(imu.calibrate_gyro)
    from .gait.engine import GaitEngine

    gait = GaitEngine(servos, imu=imu, runner=runner)
    names = [pose] if pose else sorted(POSES)
    out_dir = out or (Path(cfg.data_dir) / "characterization" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    reports = await run_characterization(servos, imu, runner, gait, names, out_dir)
    for r in reports:
        flag = "OK" if r.safe else "UNSAFE"
        print(f"{r.name}: peak roll={r.peak_roll:.1f} peak pitch={r.peak_pitch:.1f} [{flag}]")
    print(f"Full report: {out_dir / 'report.md'}")
```

Wire it into `main()`:

```python
    characterize = sub.add_parser("characterize", help="run every pose and record IMU response")
    characterize.add_argument("--pose", choices=sorted(POSES), help="characterize just one pose (default: all)")
    characterize.add_argument("--out", type=Path, help="output directory (default: ~/.milo/characterization/<timestamp>)")
```

and:

```python
    elif args.command == "characterize":
        asyncio.run(_cmd_characterize(cfg, args.pose, args.out))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/test_characterize.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full bridge test suite**

Run: `cd bridge && python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/characterize.py bridge/milo_bridge/cli.py bridge/tests/test_characterize.py
git commit -m "feat(bridge): add characterize CLI command to run and record every pose's IMU response"
```

---

## Post-plan: what still needs real hardware/network (cannot be verified by this plan's tests)

- Whether the installed `mcp` SDK's exact method names (`FastMCP.call_tool`, `.streamable_http_app()`, `ClientSession`, `streamablehttp_client`) match what's written above — these are written against the documented/common `mcp` Python SDK surface, but the SDK evolves; if a name has changed, `pip show mcp` and the installed package's own docstrings are the source of truth, and only the affected call site needs adjusting, not the surrounding design.
- Whether MCP tool calls actually move, re-face, or speak through the real robot correctly.
- Real end-to-end pairing of a Claude Desktop/Code MCP client against the bridge (`mcp-pair` → paste the token into that client's config → it can see and call Milo's tools).
- The actual characterization report numbers for each pose on real hardware, and whether any pose trips the 45° safety ceiling (if so, that's useful information, not a bug).
- Whether Ollama's `llama3.2:3b` reliably produces usable `tool_calls` in practice, and whether `MAX_TOOL_ROUNDS = 4` is the right cap for real conversational latency — likely needs prompt and/or timeout tuning after a live pass.
