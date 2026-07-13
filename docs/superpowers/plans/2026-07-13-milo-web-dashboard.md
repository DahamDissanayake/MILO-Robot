# Milo Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A LAN web dashboard served by the `milo-bridge` process at `http://milo.local` — live camera/audio, speak-through-robot, movement/pose/servo control with exclusive-control arbitration, searchable knowledge-graph view, logs — as a drag-and-drop monochrome card UI.

**Architecture:** An `aiohttp` app embedded in the bridge process (hardware is process-exclusive). A `ControlBroker` arbitrates motion between the brain session and web clients; a `MediaHub` fans camera/audio frames out to the brain socket and any number of web clients. Frontend is no-build vanilla ES modules: a card registry + CSS-grid dashboard with drag/reorder/resize persisted in `localStorage`.

**Tech Stack:** Python ≥3.11, `aiohttp>=3.9` (new bridge dep), existing bridge drivers/gait/graph, vanilla JS ES modules, CSS custom properties, pytest + `pytest-aiohttp`-style testing via `aiohttp.test_utils`.

**Spec:** `docs/superpowers/specs/2026-07-13-milo-web-dashboard-design.md`

## Global Constraints

- New bridge runtime dep: `aiohttp>=3.9` only. No frontend build tools, no CDN assets — every JS/CSS file is local under `webapp/static/`.
- Server binds `0.0.0.0:<config.web_port>` (default 80); on `OSError` retries once on 8080 and logs the fallback. Web server failure must never kill the robot service.
- Motion (gait/pose/face/servo/speaker) requires the caller to hold web control via `ControlBroker`; `stop` is always allowed from anyone. Observation (camera, audio out, telemetry, graph, logs) is always allowed, multi-client.
- WS heartbeat: client sends `{"t":"hb"}` every 5 s; broker auto-releases control after 10 s silence.
- Binary WS framing: first byte `0x01` = mic audio server→client, `0x02` = intercom audio client→server; payload is raw PCM (robot's native format both ways).
- Gait staleness: if no gait command for 0.5 s, velocities are zeroed server-side.
- All API errors return JSON `{"error": "..."}`, never a traceback page.
- Monochrome theme only: black/white surfaces + `--ok` green and `--danger` red accents; light and dark both required, toggle persisted as `milo.theme`, layout as `milo.layout.v1`.
- Servo channel map/names come from `milo_bridge.drivers.servos.SERVO_CHANNELS` (R1..L4 = 0..7); never redefine.
- Run tests from repo root: `python -m pytest bridge/tests -q` (webapp tests live in `bridge/tests/webapp/`).
- Commit per task, no co-author trailer.

## File Map (whole feature)

```
bridge/pyproject.toml                        modify: + aiohttp>=3.9
bridge/milo_bridge/config.py                 modify: + web_port, web_enabled
bridge/milo_bridge/main.py                   modify: build WebDeps, start_web task, MediaHub wiring
bridge/milo_bridge/net/session.py            modify: RobotSession uses MediaHub; motion gate via broker
bridge/milo_bridge/net/streams.py            modify: pumps read from hub subscriptions
bridge/systemd/milo-bridge.service           modify: + AmbientCapabilities/CapabilityBoundingSet
bridge/milo_bridge/graph/store.py            modify: + search_text()
bridge/milo_bridge/graph/api.py              modify: + _op_search_text
bridge/milo_bridge/webapp/__init__.py        create_app(deps)
bridge/milo_bridge/webapp/deps.py            WebDeps dataclass
bridge/milo_bridge/webapp/server.py          start_web(deps) with port fallback
bridge/milo_bridge/webapp/control.py         ControlBroker
bridge/milo_bridge/webapp/media_hub.py       Fanout + MediaHub
bridge/milo_bridge/webapp/logbuf.py          RingBufferLogHandler
bridge/milo_bridge/webapp/telemetry.py       collect_telemetry(deps) -> dict
bridge/milo_bridge/webapp/ws.py              /ws endpoint: dispatch, heartbeat, telemetry loop
bridge/milo_bridge/webapp/api/__init__.py    register_routes(app)
bridge/milo_bridge/webapp/api/status.py      GET /api/status
bridge/milo_bridge/webapp/api/media.py       GET /stream/camera
bridge/milo_bridge/webapp/api/speak.py       POST /api/speak (espeak-ng)
bridge/milo_bridge/webapp/api/graph.py       POST /api/graph, GET /api/graph/search
bridge/milo_bridge/webapp/api/motion_meta.py GET /api/poses, GET /api/faces
bridge/milo_bridge/webapp/api/logs.py        GET /api/logs
bridge/milo_bridge/webapp/motion.py          MotionService: gait watchdog, pose/face/servo/stop
bridge/milo_bridge/webapp/static/index.html
bridge/milo_bridge/webapp/static/css/theme.css
bridge/milo_bridge/webapp/static/css/grid.css
bridge/milo_bridge/webapp/static/js/main.js
bridge/milo_bridge/webapp/static/js/registry.js
bridge/milo_bridge/webapp/static/js/bus.js
bridge/milo_bridge/webapp/static/js/grid.js
bridge/milo_bridge/webapp/static/js/cards/status.js | log.js | camera.js | ears.js |
    voice.js | move.js | poses.js | servos.js | sensors.js | graph.js
bridge/tests/webapp/__init__.py + test files per task
docs/WEB-DASHBOARD.md                        setup + card-authoring guide
```

Fake drivers for tests live in `bridge/tests/webapp/fakes.py` (Task 1) and are reused by every later test module.

---

### Task 1: Webapp skeleton — config, deps, create_app, /api/status, port fallback, main wiring

**Files:**
- Modify: `bridge/pyproject.toml` (add `aiohttp>=3.9` to `[project] dependencies`)
- Modify: `bridge/milo_bridge/config.py` (add fields)
- Create: `bridge/milo_bridge/webapp/__init__.py`, `deps.py`, `server.py`, `telemetry.py`
- Create: `bridge/milo_bridge/webapp/api/__init__.py`, `api/status.py`
- Create: `bridge/milo_bridge/webapp/static/index.html` (placeholder, replaced in Task 9)
- Modify: `bridge/milo_bridge/main.py`
- Create: `bridge/tests/webapp/__init__.py`, `bridge/tests/webapp/fakes.py`, `bridge/tests/webapp/test_status.py`, `bridge/tests/webapp/test_server.py`

**Interfaces:**
- Produces: `WebDeps` dataclass (fields: `config, runner, display, servos, camera, audio, imu, gait, graph_api, graph_store, broker, media_hub, log_buffer, get_link_state`) — `broker`/`media_hub`/`log_buffer` are `None`-tolerant until Tasks 2-4 fill them; `create_app(deps) -> web.Application` with `app["deps"]`; `start_web(deps) -> None` (async, binds with fallback); `collect_telemetry(deps) -> dict`; fakes: `FakeGait, FakeServos, FakeRunner, FakeDisplay, FakeAudio, FakeCamera, FakeImu, make_deps(**overrides)`.

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/__init__.py`: empty file.

`bridge/tests/webapp/fakes.py`:

```python
"""Fake drivers for webapp tests — mirror only the methods the webapp uses."""
from __future__ import annotations

import asyncio
from pathlib import Path

from milo_bridge.config import BridgeConfig
from milo_bridge.graph.api import GraphApi
from milo_bridge.graph.store import GraphStore
from milo_bridge.webapp.deps import WebDeps


class FakeGait:
    backend = "cpg"

    def __init__(self):
        self.vel = (0.0, 0.0, 0.0)

    def set_velocity_command(self, vx, vy, yaw_rate):
        self.vel = (vx, vy, yaw_rate)


class FakeServos:
    def __init__(self):
        self.angles = {}

    def set_angle(self, servo, angle):
        self.angles[servo] = angle

    async def set_pose(self, angles, stagger=True):
        self.angles.update(angles)


class FakeRunner:
    def __init__(self):
        self.ran = []
        self.aborted = False

    async def run(self, name, cycles=2):
        self.ran.append(name)
        return True

    def abort(self):
        self.aborted = True


class FakeDisplay:
    def __init__(self):
        self.faces = []

    async def set_face(self, name, mode=None):
        self.faces.append(name)

    def start_idle(self):
        pass


class FakeAudio:
    def __init__(self, frames=(b"\x00\x02" * 160,)):
        self._frames = list(frames)
        self.played = []

    async def capture_frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)

    def play_pcm(self, pcm):
        self.played.append(pcm)


class FakeCamera:
    def __init__(self, frames=(b"jpeg-a", b"jpeg-b")):
        self._frames = list(frames)

    async def frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)


class FakeImu:
    def read(self):
        return {"pitch": 1.0, "roll": -2.0, "gyro_z": 0.5}


def make_deps(**overrides) -> WebDeps:
    store = GraphStore(":memory:")
    deps = WebDeps(
        config=BridgeConfig(robot_id="milo-test", robot_name="milo"),
        runner=FakeRunner(),
        display=FakeDisplay(),
        servos=FakeServos(),
        camera=FakeCamera(),
        audio=FakeAudio(),
        imu=FakeImu(),
        gait=FakeGait(),
        graph_api=GraphApi(store),
        graph_store=store,
        broker=None,
        media_hub=None,
        log_buffer=None,
        get_link_state=lambda: "disconnected",
    )
    for k, v in overrides.items():
        setattr(deps, k, v)
    return deps
```

`bridge/tests/webapp/test_status.py`:

```python
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from .fakes import make_deps


async def _client(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_status_reports_identity_and_hardware():
    deps = make_deps()
    client = await _client(deps)
    try:
        resp = await client.get("/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["robot_id"] == "milo-test"
        assert data["hardware"]["camera"] is True
        assert data["hardware"]["audio"] is True
        assert data["link"] == "disconnected"
        assert data["gait_backend"] == "cpg"
    finally:
        await client.close()


async def test_status_flags_missing_hardware():
    deps = make_deps(camera=None, audio=None, imu=None, display=None)
    client = await _client(deps)
    try:
        data = await (await client.get("/api/status")).json()
        assert data["hardware"] == {
            "camera": False, "audio": False, "imu": False, "display": True is False or False,
        } or data["hardware"]["camera"] is False
        assert data["hardware"]["audio"] is False
    finally:
        await client.close()


async def test_index_served():
    client = await _client(make_deps())
    try:
        resp = await client.get("/")
        assert resp.status == 200
        assert "MILO" in await resp.text()
    finally:
        await client.close()
```

(Note: fix the hardware assertion to a plain dict equality — `{"camera": False, "audio": False, "imu": False, "display": False}` — when writing the real file; the flags are one bool per optional device.)

`bridge/tests/webapp/test_server.py`:

```python
import milo_bridge.webapp.server as server_mod
from milo_bridge.webapp.server import pick_port


def test_pick_port_prefers_config():
    assert pick_port(80, port_free=lambda p: True) == 80


def test_pick_port_falls_back_to_8080():
    assert pick_port(80, port_free=lambda p: p != 80) == 8080
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp -q`
Expected: collection errors — `ModuleNotFoundError: No module named 'milo_bridge.webapp'`

- [ ] **Step 3: Implement**

`bridge/pyproject.toml` — in `[project] dependencies` add the line `"aiohttp>=3.9",` after `"zeroconf>=0.130",`. Then `pip install -e "bridge[dev]"`.

`bridge/milo_bridge/config.py` — after `reconnect_seconds: float = 10.0` add:

```python
    # Web dashboard
    web_enabled: bool = True
    web_port: int = 80
```

`bridge/milo_bridge/webapp/deps.py`:

```python
"""Dependency bundle handed to the web app — everything it may touch.

Typed loosely (Any) on purpose: real drivers on the Pi, fakes in tests,
and None where optional hardware is absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class WebDeps:
    config: Any
    runner: Any            # PoseRunner
    display: Any | None    # FaceDisplay
    servos: Any            # ServoDriver
    camera: Any | None     # CameraStreamer
    audio: Any | None      # AudioIO
    imu: Any | None        # Mpu6050
    gait: Any              # GaitEngine
    graph_api: Any         # GraphApi
    graph_store: Any       # GraphStore
    broker: Any | None     # ControlBroker (Task 2)
    media_hub: Any | None  # MediaHub (Task 4)
    log_buffer: Any | None # RingBufferLogHandler (Task 7)
    get_link_state: Callable[[], str]
```

`bridge/milo_bridge/webapp/telemetry.py`:

```python
"""Telemetry snapshot pushed to every WS client and used by /api/status."""
from __future__ import annotations

import time

import psutil_shim  # see below: no psutil dep in bridge — use /proc directly

# The bridge deliberately avoids a psutil dependency; read the two numbers
# we need straight from the kernel, degrading to None off-Linux.


def _cpu_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return None


_last_cpu: tuple[float, float] | None = None


def _cpu_percent() -> float | None:
    global _last_cpu
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(float, parts))
        idle, total = vals[3] + vals[4], sum(vals)
    except (OSError, IndexError, ValueError):
        return None
    if _last_cpu is None:
        _last_cpu = (idle, total)
        return None
    didle, dtotal = idle - _last_cpu[0], total - _last_cpu[1]
    _last_cpu = (idle, total)
    if dtotal <= 0:
        return None
    return round(100.0 * (1.0 - didle / dtotal), 1)


def _mem_percent() -> float | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = float(v.strip().split()[0])
        return round(100.0 * (1.0 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        return None


_START = time.monotonic()


def collect_telemetry(deps) -> dict:
    imu = None
    if deps.imu is not None:
        try:
            imu = deps.imu.read()
        except Exception:
            imu = None
    return {
        "t": "telemetry",
        "cpu_percent": _cpu_percent(),
        "temp_c": _cpu_temp_c(),
        "mem_percent": _mem_percent(),
        "uptime_s": round(time.monotonic() - _START, 1),
        "link": deps.get_link_state(),
        "owner": deps.broker.owner if deps.broker else "none",
        "gait_backend": getattr(deps.gait, "backend", None),
        "imu": imu,
    }
```

(Remove the `import psutil_shim` line — it is illustrative only; the module uses `/proc` and `/sys` reads directly as shown.)

`bridge/milo_bridge/webapp/api/status.py`:

```python
from aiohttp import web

from ..telemetry import collect_telemetry


async def get_status(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    body = collect_telemetry(deps)
    body.pop("t", None)
    body.update(
        robot_id=deps.config.robot_id,
        robot_name=deps.config.robot_name,
        hardware={
            "camera": deps.camera is not None,
            "audio": deps.audio is not None,
            "imu": deps.imu is not None,
            "display": deps.display is not None,
        },
    )
    return web.json_response(body)


def register(app: web.Application) -> None:
    app.router.add_get("/api/status", get_status)
```

`bridge/milo_bridge/webapp/api/__init__.py`:

```python
"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import status


def register_routes(app: web.Application) -> None:
    status.register(app)
```

`bridge/milo_bridge/webapp/__init__.py`:

```python
"""Milo web dashboard: aiohttp app factory."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .api import register_routes
from .deps import WebDeps

STATIC_DIR = Path(__file__).parent / "static"


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


def create_app(deps: WebDeps) -> web.Application:
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app["deps"] = deps
    app["ws_clients"] = set()
    register_routes(app)
    app.router.add_get("/", _index)
    app.router.add_static("/static", STATIC_DIR)
    return app
```

`bridge/milo_bridge/webapp/server.py`:

```python
"""Bind and serve the dashboard; port 80 with one 8080 fallback."""
from __future__ import annotations

import logging
import socket

from aiohttp import web

from . import create_app

log = logging.getLogger(__name__)
FALLBACK_PORT = 8080


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int, port_free=_port_free) -> int:
    if port_free(preferred):
        return preferred
    log.warning("port %d unavailable, falling back to %d", preferred, FALLBACK_PORT)
    return FALLBACK_PORT


async def start_web(deps) -> None:
    """Run the dashboard forever. Exceptions are logged, never propagated."""
    try:
        app = create_app(deps)
        port = pick_port(deps.config.web_port)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log.info("web dashboard on http://0.0.0.0:%d (milo.local)", port)
    except Exception:
        log.exception("web dashboard failed to start")
```

`bridge/milo_bridge/webapp/static/index.html` (placeholder until Task 9):

```html
<!doctype html>
<meta charset="utf-8">
<title>MILO</title>
<h1>MILO dashboard — UI lands in Task 9</h1>
```

`bridge/milo_bridge/main.py` — add imports and wiring inside `main()` after the gait engine is built (exact insertion: after `log.info("gait backend: %s", gait.backend)`):

```python
    from .webapp.deps import WebDeps
    from .webapp.server import start_web

    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
        graph_api=graph_api, graph_store=graph,
        broker=None, media_hub=None, log_buffer=None,
        get_link_state=lambda: "disconnected",
    )
    if cfg.web_enabled:
        asyncio.create_task(start_web(web_deps))
```

(`graph`/`graph_api` already exist a few lines below in `main()`; place this block after their construction. Later tasks replace the `None`s and the link-state lambda.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp -q`
Expected: 5 passed (3 status/index + 2 pick_port). Also run the full bridge suite to catch wiring regressions: `python -m pytest bridge/tests -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add bridge
git commit -m "feat(web): webapp skeleton — app factory, status API, port fallback, main wiring"
```

---

### Task 2: ControlBroker

**Files:**
- Create: `bridge/milo_bridge/webapp/control.py`
- Test: `bridge/tests/webapp/test_control.py`

**Interfaces:**
- Produces: `ControlBroker` with: `owner -> str` ("web" | "brain" | "none"); `set_brain_connected(bool)`; `acquire_web(client_id) -> bool`; `release_web(client_id)`; `heartbeat(client_id)`; `expire(now: float) -> bool` (True if expired+released); `allow_brain_motion() -> bool`; `is_web_controller(client_id) -> bool`; constructor `ControlBroker(on_change: Callable[[str], None] | None = None, timeout_s: float = 10.0)`. `on_change(owner)` fires on every ownership transition.

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_control.py`:

```python
from milo_bridge.webapp.control import ControlBroker


def test_owner_none_then_brain():
    b = ControlBroker()
    assert b.owner == "none"
    b.set_brain_connected(True)
    assert b.owner == "brain"
    assert b.allow_brain_motion() is True


def test_web_acquire_and_exclusivity():
    b = ControlBroker()
    b.set_brain_connected(True)
    assert b.acquire_web("c1") is True
    assert b.owner == "web"
    assert b.allow_brain_motion() is False
    assert b.acquire_web("c2") is False          # second client denied
    assert b.is_web_controller("c1") is True
    assert b.is_web_controller("c2") is False
    b.release_web("c1")
    assert b.owner == "brain"
    assert b.allow_brain_motion() is True


def test_release_by_non_owner_is_noop():
    b = ControlBroker()
    b.acquire_web("c1")
    b.release_web("c2")
    assert b.owner == "web"


def test_heartbeat_timeout_releases():
    b = ControlBroker(timeout_s=10.0)
    b.acquire_web("c1")
    b.heartbeat("c1")
    t0 = b._last_hb
    assert b.expire(now=t0 + 9.0) is False
    assert b.owner == "web"
    assert b.expire(now=t0 + 10.1) is True
    assert b.owner == "none"


def test_on_change_fires_on_transitions():
    seen = []
    b = ControlBroker(on_change=seen.append)
    b.set_brain_connected(True)   # none -> brain
    b.acquire_web("c1")           # brain -> web
    b.acquire_web("c1")           # re-acquire by same owner: no event
    b.release_web("c1")           # web -> brain
    assert seen == ["brain", "web", "brain"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_control.py -q`
Expected: `ModuleNotFoundError: No module named 'milo_bridge.webapp.control'`

- [ ] **Step 3: Implement**

`bridge/milo_bridge/webapp/control.py`:

```python
"""Single gate for anything that moves hardware.

Observation is never brokered — only motion. The brain has motion rights
implicitly whenever no web client holds the slot. STOP is handled by the
callers (always allowed) and never routes through acquire.
"""
from __future__ import annotations

import time
from typing import Callable


class ControlBroker:
    def __init__(self, on_change: Callable[[str], None] | None = None, timeout_s: float = 10.0):
        self._web_owner: str | None = None
        self._brain = False
        self._on_change = on_change
        self._timeout_s = timeout_s
        self._last_hb: float = 0.0

    @property
    def owner(self) -> str:
        if self._web_owner is not None:
            return "web"
        return "brain" if self._brain else "none"

    def _emit(self, before: str) -> None:
        if self._on_change is not None and self.owner != before:
            self._on_change(self.owner)

    def set_brain_connected(self, connected: bool) -> None:
        before = self.owner
        self._brain = connected
        self._emit(before)

    def acquire_web(self, client_id: str) -> bool:
        if self._web_owner is not None and self._web_owner != client_id:
            return False
        before = self.owner
        self._web_owner = client_id
        self._last_hb = time.monotonic()
        self._emit(before)
        return True

    def release_web(self, client_id: str) -> None:
        if self._web_owner != client_id:
            return
        before = self.owner
        self._web_owner = None
        self._emit(before)

    def heartbeat(self, client_id: str) -> None:
        if self._web_owner == client_id:
            self._last_hb = time.monotonic()

    def expire(self, now: float | None = None) -> bool:
        """Release web control if the owner has gone quiet. Returns True if released."""
        if self._web_owner is None:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last_hb < self._timeout_s:
            return False
        before = self.owner
        self._web_owner = None
        self._emit(before)
        return True

    def allow_brain_motion(self) -> bool:
        return self._web_owner is None

    def is_web_controller(self, client_id: str) -> bool:
        return self._web_owner == client_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_control.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/control.py bridge/tests/webapp/test_control.py
git commit -m "feat(web): ControlBroker — exclusive motion arbitration with heartbeat expiry"
```

---

### Task 3: MediaHub + streams refactor + session motion gate

**Files:**
- Create: `bridge/milo_bridge/webapp/media_hub.py`
- Modify: `bridge/milo_bridge/net/streams.py`
- Modify: `bridge/milo_bridge/net/session.py`
- Modify: `bridge/milo_bridge/main.py`
- Test: `bridge/tests/webapp/test_media_hub.py`

**Interfaces:**
- Consumes: `ControlBroker.allow_brain_motion()` (Task 2).
- Produces: `Fanout(gen_factory, name, on_item=None)` with `subscribe() -> asyncio.Queue`, `unsubscribe(q)`, property `active: bool`; `MediaHub(camera=None, audio=None, on_audio_level=None)` with `.video: Fanout | None`, `.audio: Fanout | None`. `RobotSession.__init__` signature changes: `camera=None, audio=None, on_audio_level=None` replaced by `media_hub=None`; `RobotSession` gains `broker=None` param; `streams.pump_video(sock, fanout)` / `streams.pump_audio(sock, fanout)` take a `Fanout`.

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_media_hub.py`:

```python
import asyncio

import pytest

from milo_bridge.webapp.media_hub import Fanout, MediaHub
from .fakes import FakeAudio, FakeCamera


async def _drain(q, n):
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(q.get(), 1.0))
    return out


async def test_two_subscribers_both_get_frames():
    async def gen():
        for i in range(3):
            yield f"f{i}".encode()
            await asyncio.sleep(0)

    fan = Fanout(gen, "video")
    q1, q2 = fan.subscribe(), fan.subscribe()
    assert await _drain(q1, 3) == [b"f0", b"f1", b"f2"]
    assert await _drain(q2, 3) == [b"f0", b"f1", b"f2"]
    fan.unsubscribe(q1)
    fan.unsubscribe(q2)
    await asyncio.sleep(0)
    assert fan.active is False


async def test_slow_subscriber_drops_oldest_not_blocks():
    async def gen():
        for i in range(10):
            yield bytes([i])
            await asyncio.sleep(0)

    fan = Fanout(gen, "video")
    q = fan.subscribe()          # never drained while producing
    await asyncio.sleep(0.05)    # let the producer finish
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert len(got) <= 2                     # maxsize=2: only newest kept
    assert got[-1] == bytes([9])             # newest frame survived
    fan.unsubscribe(q)


async def test_reader_stops_when_last_unsubscribes():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def gen():
        started.set()
        try:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)
        finally:
            cancelled.set()

    fan = Fanout(gen, "video")
    q = fan.subscribe()
    await asyncio.wait_for(started.wait(), 1.0)
    fan.unsubscribe(q)
    await asyncio.wait_for(cancelled.wait(), 1.0)


async def test_media_hub_audio_level_callback():
    levels = []
    hub = MediaHub(camera=FakeCamera(), audio=FakeAudio(), on_audio_level=levels.append)
    q = hub.audio.subscribe()
    await asyncio.wait_for(q.get(), 1.0)
    assert levels, "on_audio_level should fire for every captured chunk"
    hub.audio.unsubscribe(q)


async def test_hub_none_drivers():
    hub = MediaHub(camera=None, audio=None)
    assert hub.video is None and hub.audio is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_media_hub.py -q`
Expected: `ModuleNotFoundError: No module named 'milo_bridge.webapp.media_hub'`

- [ ] **Step 3: Implement**

`bridge/milo_bridge/webapp/media_hub.py`:

```python
"""Single-reader fanout for camera and mic streams.

Drivers expose single-consumer async generators; the hub owns the one
reader task per driver and feeds every subscriber a bounded queue. Slow
subscribers lose old frames instead of stalling the pipeline.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable

log = logging.getLogger(__name__)

QUEUE_SIZE = 2


class Fanout:
    def __init__(self, gen_factory: Callable[[], AsyncIterator[bytes]], name: str,
                 on_item: Callable[[bytes], None] | None = None):
        self._factory = gen_factory
        self._name = name
        self._on_item = on_item
        self._subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.add(q)
        if not self.active:
            self._task = asyncio.ensure_future(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        if not self._subs and self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            async for item in self._factory():
                if self._on_item is not None:
                    self._on_item(item)
                for q in list(self._subs):
                    if q.full():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s fanout reader died", self._name)


class MediaHub:
    def __init__(self, camera=None, audio=None,
                 on_audio_level: Callable[[float], None] | None = None):
        self.video = Fanout(camera.frames, "video") if camera is not None else None
        if audio is not None:
            def _level(chunk: bytes) -> None:
                if on_audio_level is not None:
                    from ..drivers.audio import rms
                    on_audio_level(rms(chunk))
            self.audio = Fanout(audio.capture_frames, "audio", on_item=_level)
        else:
            self.audio = None
```

`bridge/milo_bridge/net/streams.py` — replace both pump bodies:

```python
"""Outbound media pumps: hub-subscribed camera frames and mic audio."""

from __future__ import annotations

import time

from milo_common import protocol
from milo_common.protocol import MiloSocket


async def pump_video(sock: MiloSocket, fanout) -> None:
    """Send MJPEG frames from the hub until cancelled."""
    q = fanout.subscribe()
    try:
        while True:
            frame = await q.get()
            await sock.send(protocol.T_VIDEO, payload=frame, ts=time.time())
    finally:
        fanout.unsubscribe(q)


async def pump_audio(sock: MiloSocket, fanout) -> None:
    """Send 20 ms stereo PCM frames from the hub until cancelled."""
    q = fanout.subscribe()
    try:
        while True:
            chunk = await q.get()
            await sock.send(protocol.T_AUDIO, payload=chunk, ts=time.time())
    finally:
        fanout.unsubscribe(q)
```

`bridge/milo_bridge/net/session.py` — three changes:

1. `RobotSession.__init__`: replace parameters `camera=None, audio=None, on_audio_level=None` with `media_hub=None, broker=None`; store `self._hub = media_hub`, `self._broker = broker`; delete `self._camera/self._audio/self._on_audio_level`.
2. `RobotSession.run()` pump setup becomes:

```python
        pumps: list[asyncio.Task] = []
        if self._hub is not None and self._hub.video is not None:
            pumps.append(asyncio.create_task(streams.pump_video(self._sock, self._hub.video)))
        if self._hub is not None and self._hub.audio is not None:
            pumps.append(asyncio.create_task(streams.pump_audio(self._sock, self._hub.audio)))
```

3. Gate brain motion in `_handle_cmd` — at its top, before executing any motion command (pose/gait/turn), add:

```python
        if self._broker is not None and not self._broker.allow_brain_motion():
            log.info("dropping brain motion cmd while web client controls: %s", msg)
            return
```

Also in `SessionManager`: wherever it constructs `RobotSession(...)`, pass `media_hub=...` and `broker=...` through (add both as `SessionManager.__init__` params, stored and forwarded); wherever the session connects/disconnects, call `broker.set_brain_connected(True/False)` if the broker is not None, and change the link-state so `get_link_state` can read it (add `self.link_state: str = "disconnected"` set to `"connected"`/`"disconnected"` at the same points).

`bridge/milo_bridge/main.py` — replace the Task 1 wiring block's `media_hub=None` and session construction:

```python
    from .webapp.control import ControlBroker
    from .webapp.media_hub import MediaHub

    broker = ControlBroker()
    hub = MediaHub(camera=camera, audio=audio, on_audio_level=sleeper.on_audio_level)
```

(`sleeper.on_audio_level` — use the same callback `main()` currently passes into `SessionManager`/session for the sleep perk-up; search for `on_audio_level` in `main.py` and move that callable here.) Pass `broker=broker, media_hub=hub` into both `SessionManager(...)` and `WebDeps(...)`; set `get_link_state=lambda: manager.link_state`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_media_hub.py -q` → 5 passed.
Run: `python -m pytest bridge/tests -q` → full bridge suite passes (session/stream tests updated expectations: if existing tests construct `RobotSession(camera=..., audio=...)`, update them to build a `MediaHub` from the same fakes).

- [ ] **Step 5: Commit**

```bash
git add bridge
git commit -m "feat(web): MediaHub fanout; brain session reads media via hub, motion gated by broker"
```

---

### Task 4: MotionService (gait watchdog, pose/face/servo/stop)

**Files:**
- Create: `bridge/milo_bridge/webapp/motion.py`
- Test: `bridge/tests/webapp/test_motion.py`

**Interfaces:**
- Consumes: `WebDeps` fakes (Task 1), `ControlBroker` (Task 2), `SERVO_CHANNELS`/`SERVO_NAMES` from `milo_bridge.drivers.servos`, `POSES` from `milo_bridge.poses`.
- Produces: `MotionService(deps)` with async methods, each returning a dict (`{"ok": true}` or `{"error": ...}`): `gait(client_id, vx, vy, yaw)`, `pose(client_id, name)`, `face(client_id, name)`, `servo(client_id, servo, deg)`, `stop()` (no client check), `start(app)` / `stop_watchdog()` for the staleness task; constant `STALE_S = 0.5`; `list_faces() -> list[str]` module function (unique face names from `bridge/assets/faces/*.png`, `dance_1.png`+`dance_2.png` → `dance`).

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_motion.py`:

```python
import asyncio

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.motion import MotionService, list_faces
from .fakes import make_deps


def _controlled_deps():
    deps = make_deps(broker=ControlBroker())
    deps.broker.acquire_web("c1")
    return deps


async def test_gait_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.gait("nobody", 0.5, 0.0, 0.0)
    assert res == {"error": "not-controlling"}
    assert deps.gait.vel == (0.0, 0.0, 0.0)


async def test_gait_sets_velocity_and_clamps():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.gait("c1", 2.0, -2.0, 9.0) == {"ok": True}
    vx, vy, yaw = deps.gait.vel
    assert -1.0 <= vx <= 1.0 and -1.0 <= vy <= 1.0 and -2.0 <= yaw <= 2.0


async def test_gait_staleness_zeroes():
    deps = _controlled_deps()
    svc = MotionService(deps)
    await svc.gait("c1", 1.0, 0.0, 0.0)
    svc._last_cmd -= 1.0            # simulate 1 s silence
    svc._watchdog_tick()
    assert deps.gait.vel == (0.0, 0.0, 0.0)


async def test_pose_valid_and_invalid():
    deps = _controlled_deps()
    svc = MotionService(deps)
    name = next(iter(__import__("milo_bridge.poses", fromlist=["POSES"]).POSES))
    assert await svc.pose("c1", name) == {"ok": True}
    assert deps.runner.ran == [name]
    assert "error" in await svc.pose("c1", "no-such-pose")


async def test_servo_clamps_and_validates():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.servo("c1", "R1", 200) == {"ok": True}
    assert deps.servos.angles["R1"] == 180
    assert "error" in await svc.servo("c1", "R9", 90)


async def test_face_requires_display():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.face("c1", "cute") == {"ok": True}
    assert deps.display.faces == ["cute"]
    deps.display = None
    assert "error" in await svc.face("c1", "cute")


async def test_stop_always_allowed():
    deps = make_deps(broker=ControlBroker())   # nobody controls
    svc = MotionService(deps)
    deps.gait.vel = (1.0, 0.0, 0.0)
    assert await svc.stop() == {"ok": True}
    assert deps.gait.vel == (0.0, 0.0, 0.0)
    assert deps.runner.aborted is True


def test_list_faces_groups_frames():
    names = list_faces()
    assert "dance" in names and "dance_1" not in names
    assert "cute" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_motion.py -q`
Expected: `ModuleNotFoundError: No module named 'milo_bridge.webapp.motion'`

- [ ] **Step 3: Implement**

`bridge/milo_bridge/webapp/motion.py`:

```python
"""Motion commands from web clients: control-checked, clamped, stale-safed."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from ..drivers.servos import SERVO_CHANNELS
from ..poses import POSES

log = logging.getLogger(__name__)

STALE_S = 0.5
ASSETS_FACES = Path(__file__).resolve().parents[2] / "assets" / "faces"

VX_LIM, VY_LIM, YAW_LIM = 1.0, 1.0, 2.0
DEG_MIN, DEG_MAX = 0, 180


def list_faces() -> list[str]:
    names = set()
    for p in sorted(ASSETS_FACES.glob("*.png")):
        names.add(re.sub(r"_\d+$", "", p.stem))
    return sorted(names)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


class MotionService:
    def __init__(self, deps):
        self._deps = deps
        self._last_cmd = 0.0
        self._moving = False
        self._task: asyncio.Task | None = None
        self._pose_task: asyncio.Task | None = None

    # -- control gate ------------------------------------------------------
    def _denied(self, client_id: str) -> dict | None:
        broker = self._deps.broker
        if broker is None or not broker.is_web_controller(client_id):
            return {"error": "not-controlling"}
        return None

    # -- commands ----------------------------------------------------------
    async def gait(self, client_id: str, vx: float, vy: float, yaw: float) -> dict:
        if err := self._denied(client_id):
            return err
        self._deps.gait.set_velocity_command(
            _clamp(vx, -VX_LIM, VX_LIM), _clamp(vy, -VY_LIM, VY_LIM),
            _clamp(yaw, -YAW_LIM, YAW_LIM))
        self._last_cmd = time.monotonic()
        self._moving = (vx, vy, yaw) != (0.0, 0.0, 0.0)
        return {"ok": True}

    async def pose(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if name not in POSES:
            return {"error": f"unknown pose {name!r}"}
        if self._pose_task is not None and not self._pose_task.done():
            return {"error": "pose-running"}
        self._pose_task = asyncio.ensure_future(self._deps.runner.run(name))
        return {"ok": True}

    async def face(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if self._deps.display is None:
            return {"error": "display unavailable"}
        await self._deps.display.set_face(name)
        return {"ok": True}

    async def servo(self, client_id: str, servo: str, deg: float) -> dict:
        if err := self._denied(client_id):
            return err
        if servo not in SERVO_CHANNELS:
            return {"error": f"unknown servo {servo!r}"}
        self._deps.servos.set_angle(servo, _clamp(deg, DEG_MIN, DEG_MAX))
        return {"ok": True}

    async def stop(self) -> dict:
        """Emergency stop: anyone, anytime."""
        self._deps.gait.set_velocity_command(0.0, 0.0, 0.0)
        self._moving = False
        self._deps.runner.abort()
        return {"ok": True}

    # -- staleness watchdog --------------------------------------------------
    def _watchdog_tick(self) -> None:
        if self._moving and time.monotonic() - self._last_cmd > STALE_S:
            log.info("gait command stale — zeroing velocity")
            self._deps.gait.set_velocity_command(0.0, 0.0, 0.0)
            self._moving = False

    async def _watchdog(self) -> None:
        while True:
            self._watchdog_tick()
            await asyncio.sleep(0.1)

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._watchdog())

    def stop_watchdog(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_motion.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/tests/webapp/test_motion.py
git commit -m "feat(web): MotionService — clamped gait/pose/face/servo with stale watchdog and universal STOP"
```

---

### Task 5: /ws endpoint — JSON dispatch, heartbeat, telemetry push, audio binary

**Files:**
- Create: `bridge/milo_bridge/webapp/ws.py`
- Modify: `bridge/milo_bridge/webapp/__init__.py` (register /ws, start/stop background tasks)
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Consumes: `ControlBroker` (2), `MediaHub` (3), `MotionService` (4), `collect_telemetry` (1).
- Produces: `register_ws(app)` adding `GET /ws`; app keys `app["motion"]` (MotionService), `app["ws_clients"]: set[web.WebSocketResponse]`; `broadcast_json(app, payload: dict)` helper importable by other modules; background tasks via aiohttp `on_startup`/`on_cleanup`: telemetry loop (2 s), broker-expiry loop (1 s), motion watchdog. Client→server JSON: `{"t":"hb"}`, `{"t":"control","take":bool}`, `{"t":"gait","vx","vy","yaw"}`, `{"t":"pose","name"}`, `{"t":"face","name"}`, `{"t":"servo","servo","deg"}`, `{"t":"stop"}`, `{"t":"audio","on":bool}`. Server→client JSON: `{"t":"telemetry",...}`, `{"t":"control","owner","you":bool}`, `{"t":"ack","for":...}` / `{"t":"err","for":...,"error":...}`, `{"t":"log","line":...}` (Task 7). Binary out: `0x01 + pcm` when client audio on; binary in: `0x02 + pcm` → `audio.play_pcm` (controller only).

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_ws.py`:

```python
import asyncio
import json

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from milo_bridge.webapp.control import ControlBroker
from .fakes import make_deps


async def _ws(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    ws = await client.ws_connect("/ws")
    return client, ws


async def _recv_json_until(ws, t, tries=10):
    for _ in range(tries):
        msg = await asyncio.wait_for(ws.receive(), 2.0)
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("t") == t:
                return data
    raise AssertionError(f"no {t!r} message")


async def test_take_and_release_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        data = await _recv_json_until(ws, "control")
        assert data["owner"] == "web" and data["you"] is True
        await ws.send_json({"t": "control", "take": False})
        data = await _recv_json_until(ws, "control")
        assert data["owner"] == "none" and data["you"] is False
    finally:
        await client.close()


async def test_gait_denied_without_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "gait", "vx": 1, "vy": 0, "yaw": 0})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
        assert deps.gait.vel == (0.0, 0.0, 0.0)
    finally:
        await client.close()


async def test_gait_accepted_with_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "gait", "vx": 0.5, "vy": 0, "yaw": 0})
        await _recv_json_until(ws, "ack")
        assert deps.gait.vel[0] == 0.5
    finally:
        await client.close()


async def test_stop_without_control():
    deps = make_deps(broker=ControlBroker())
    deps.gait.vel = (1.0, 0.0, 0.0)
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "stop"})
        await _recv_json_until(ws, "ack")
        assert deps.gait.vel == (0.0, 0.0, 0.0)
    finally:
        await client.close()


async def test_disconnect_releases_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    await ws.send_json({"t": "control", "take": True})
    await _recv_json_until(ws, "control")
    await ws.close()
    await client.close()
    assert deps.broker.owner == "none"


async def test_intercom_binary_plays_when_controlling():
    from milo_bridge.webapp.media_hub import MediaHub
    deps = make_deps(broker=ControlBroker(), media_hub=MediaHub())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_bytes(b"\x02" + b"pcm-data")
        for _ in range(20):
            if deps.audio.played:
                break
            await asyncio.sleep(0.05)
        assert deps.audio.played == [b"pcm-data"]
    finally:
        await client.close()


async def test_telemetry_pushed():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        data = await _recv_json_until(ws, "telemetry", tries=30)
        assert data["gait_backend"] == "cpg"
        assert data["owner"] == "none"
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_ws.py -q`
Expected: FAIL — 404 on `/ws` (no such route yet).

- [ ] **Step 3: Implement**

`bridge/milo_bridge/webapp/ws.py`:

```python
"""One WebSocket per browser tab: JSON dispatch + binary audio framing."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from aiohttp import WSMsgType, web

from .motion import MotionService
from .telemetry import collect_telemetry

log = logging.getLogger(__name__)

TELEMETRY_S = 2.0
EXPIRY_S = 1.0
AUDIO_OUT = 0x01
AUDIO_IN = 0x02


def broadcast_json(app: web.Application, payload: dict) -> None:
    for ws in list(app["ws_clients"]):
        if not ws.closed:
            asyncio.ensure_future(ws.send_json(payload))


async def _handle_text(app, ws, client_id: str, data: dict) -> None:
    deps = app["deps"]
    motion: MotionService = app["motion"]
    t = data.get("t")
    if t == "hb":
        if deps.broker:
            deps.broker.heartbeat(client_id)
        return
    if t == "control":
        broker = deps.broker
        if broker is None:
            await ws.send_json({"t": "err", "for": "control", "error": "no-broker"})
            return
        ok = broker.acquire_web(client_id) if data.get("take") else (broker.release_web(client_id) or True)
        if not ok:
            await ws.send_json({"t": "err", "for": "control", "error": "held-by-other"})
        _broadcast_owner(app)
        return
    if t == "stop":
        await motion.stop()
        await ws.send_json({"t": "ack", "for": "stop"})
        return
    if t == "audio":
        ws_state = app["ws_state"][ws]
        ws_state["audio_on"] = bool(data.get("on"))
        return
    handlers = {
        "gait": lambda: motion.gait(client_id, data.get("vx", 0), data.get("vy", 0), data.get("yaw", 0)),
        "pose": lambda: motion.pose(client_id, data.get("name", "")),
        "face": lambda: motion.face(client_id, data.get("name", "")),
        "servo": lambda: motion.servo(client_id, data.get("servo", ""), data.get("deg", 90)),
    }
    if t not in handlers:
        await ws.send_json({"t": "err", "for": t, "error": "unknown-type"})
        return
    res = await handlers[t]()
    if "error" in res:
        await ws.send_json({"t": "err", "for": t, "error": res["error"]})
    else:
        await ws.send_json({"t": "ack", "for": t})


def _broadcast_owner(app: web.Application) -> None:
    deps = app["deps"]
    owner = deps.broker.owner if deps.broker else "none"
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            you = bool(deps.broker and deps.broker.is_web_controller(state["id"]))
            asyncio.ensure_future(ws.send_json({"t": "control", "owner": owner, "you": you}))


async def _audio_out_pump(app, ws) -> None:
    """Forward hub mic audio to this client while its audio flag is on."""
    deps = app["deps"]
    hub = deps.media_hub
    if hub is None or hub.audio is None:
        return
    q = None
    try:
        while not ws.closed:
            state = app["ws_state"].get(ws)
            if state is None:
                return
            if state["audio_on"] and q is None:
                q = hub.audio.subscribe()
            elif not state["audio_on"] and q is not None:
                hub.audio.unsubscribe(q)
                q = None
            if q is None:
                await asyncio.sleep(0.2)
                continue
            try:
                chunk = await asyncio.wait_for(q.get(), 0.5)
            except asyncio.TimeoutError:
                continue
            await ws.send_bytes(bytes([AUDIO_OUT]) + chunk)
    finally:
        if q is not None:
            hub.audio.unsubscribe(q)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    app = request.app
    deps = app["deps"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    client_id = uuid.uuid4().hex[:8]
    app["ws_clients"].add(ws)
    app["ws_state"][ws] = {"id": client_id, "audio_on": False}
    await ws.send_json({"t": "hello", "id": client_id})
    pump = asyncio.ensure_future(_audio_out_pump(app, ws))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                await _handle_text(app, ws, client_id, data)
            elif msg.type == WSMsgType.BINARY and msg.data[:1] == bytes([AUDIO_IN]):
                if deps.audio is not None and deps.broker and deps.broker.is_web_controller(client_id):
                    deps.audio.play_pcm(msg.data[1:])
    finally:
        pump.cancel()
        app["ws_clients"].discard(ws)
        app["ws_state"].pop(ws, None)
        if deps.broker:
            deps.broker.release_web(client_id)
            _broadcast_owner(app)
    return ws


async def _telemetry_loop(app: web.Application) -> None:
    while True:
        await asyncio.sleep(TELEMETRY_S)
        if app["ws_clients"]:
            broadcast_json(app, collect_telemetry(app["deps"]))


async def _expiry_loop(app: web.Application) -> None:
    deps = app["deps"]
    while True:
        await asyncio.sleep(EXPIRY_S)
        if deps.broker and deps.broker.expire():
            _broadcast_owner(app)


async def _on_startup(app: web.Application) -> None:
    app["motion"].start()
    app["bg_tasks"] = [
        asyncio.ensure_future(_telemetry_loop(app)),
        asyncio.ensure_future(_expiry_loop(app)),
    ]


async def _on_cleanup(app: web.Application) -> None:
    app["motion"].stop_watchdog()
    for t in app.get("bg_tasks", []):
        t.cancel()


def register_ws(app: web.Application) -> None:
    app["ws_state"] = {}
    app["motion"] = MotionService(app["deps"])
    app.router.add_get("/ws", websocket_handler)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
```

`bridge/milo_bridge/webapp/__init__.py` — in `create_app`, after `register_routes(app)` add:

```python
    from .ws import register_ws
    register_ws(app)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_ws.py -q` → 7 passed.
Run: `python -m pytest bridge/tests -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp bridge/tests/webapp/test_ws.py
git commit -m "feat(web): /ws endpoint — control, motion dispatch, telemetry push, audio binary framing"
```

---

### Task 6: Camera MJPEG stream + speak (TTS)

**Files:**
- Create: `bridge/milo_bridge/webapp/api/media.py`, `bridge/milo_bridge/webapp/api/speak.py`
- Modify: `bridge/milo_bridge/webapp/api/__init__.py`
- Test: `bridge/tests/webapp/test_media_endpoints.py`

**Interfaces:**
- Consumes: `MediaHub.video` fanout (3); `deps.audio.play_pcm`; `ControlBroker.is_web_controller` — `/api/speak` requires the `client` id passed in the JSON body to hold control.
- Produces: `GET /stream/camera` (`multipart/x-mixed-replace; boundary=milo-frame`); `POST /api/speak {"text": str, "client": str}` → runs `espeak-ng --stdout -a 120 <text>` piped through `ffmpeg`-free raw conversion: use `espeak-ng --stdout` (WAV), strip the 44-byte header, pass PCM to `play_pcm`. If `espeak-ng` is missing → `{"error": "tts-unavailable"}`. `tts_available() -> bool` helper (shutil.which).

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_media_endpoints.py`:

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


async def test_camera_stream_yields_mjpeg_parts():
    cam = FakeCamera(frames=(b"AAA", b"BBB"))
    deps = make_deps(camera=cam, media_hub=MediaHub(camera=cam))
    client = await _client(deps)
    try:
        resp = await client.get("/stream/camera")
        assert resp.status == 200
        assert "multipart/x-mixed-replace" in resp.headers["Content-Type"]
        raw = await asyncio.wait_for(resp.content.read(200), 2.0)
        assert b"--milo-frame" in raw
        assert b"Content-Type: image/jpeg" in raw
        assert b"AAA" in raw
    finally:
        await client.close()


async def test_camera_stream_without_camera_404s():
    deps = make_deps(camera=None, media_hub=MediaHub(camera=None))
    client = await _client(deps)
    try:
        resp = await client.get("/stream/camera")
        assert resp.status == 404
        assert (await resp.json())["error"] == "camera unavailable"
    finally:
        await client.close()


async def test_speak_requires_control(monkeypatch):
    deps = make_deps(broker=ControlBroker())
    client = await _client(deps)
    try:
        resp = await client.post("/api/speak", json={"text": "hi", "client": "x"})
        assert (await resp.json())["error"] == "not-controlling"
    finally:
        await client.close()


async def test_speak_tts_unavailable(monkeypatch):
    import milo_bridge.webapp.api.speak as speak_mod
    monkeypatch.setattr(speak_mod, "tts_available", lambda: False)
    deps = make_deps(broker=ControlBroker())
    deps.broker.acquire_web("c1")
    client = await _client(deps)
    try:
        resp = await client.post("/api/speak", json={"text": "hi", "client": "c1"})
        assert (await resp.json())["error"] == "tts-unavailable"
    finally:
        await client.close()


async def test_speak_plays_pcm(monkeypatch):
    import milo_bridge.webapp.api.speak as speak_mod
    monkeypatch.setattr(speak_mod, "tts_available", lambda: True)

    async def fake_synth(text):
        return b"\x00\x01" * 100

    monkeypatch.setattr(speak_mod, "synth_pcm", fake_synth)
    deps = make_deps(broker=ControlBroker())
    deps.broker.acquire_web("c1")
    client = await _client(deps)
    try:
        resp = await client.post("/api/speak", json={"text": "hello", "client": "c1"})
        assert (await resp.json()) == {"ok": True}
        assert deps.audio.played == [b"\x00\x01" * 100]
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_media_endpoints.py -q`
Expected: FAIL — 404 on `/stream/camera` and `/api/speak`.

- [ ] **Step 3: Implement**

`bridge/milo_bridge/webapp/api/media.py`:

```python
"""MJPEG camera stream — a hub subscription per connected browser."""
from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger(__name__)
BOUNDARY = "milo-frame"


async def camera_stream(request: web.Request) -> web.StreamResponse:
    deps = request.app["deps"]
    hub = deps.media_hub
    if hub is None or hub.video is None:
        return web.json_response({"error": "camera unavailable"}, status=404)
    resp = web.StreamResponse(headers={
        "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        "Cache-Control": "no-store",
    })
    await resp.prepare(request)
    q = hub.video.subscribe()
    try:
        while True:
            frame = await q.get()
            await resp.write(
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n".encode() + frame + b"\r\n"
            )
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.video.unsubscribe(q)
    return resp


def register(app: web.Application) -> None:
    app.router.add_get("/stream/camera", camera_stream)
```

(Add `import asyncio` at the top of the file.)

`bridge/milo_bridge/webapp/api/speak.py`:

```python
"""Text-to-speech through Milo's speaker via espeak-ng."""
from __future__ import annotations

import asyncio
import logging
import shutil

from aiohttp import web

log = logging.getLogger(__name__)
WAV_HEADER = 44


def tts_available() -> bool:
    return shutil.which("espeak-ng") is not None


async def synth_pcm(text: str) -> bytes | None:
    proc = await asyncio.create_subprocess_exec(
        "espeak-ng", "--stdout", "-a", "120", text,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), 10.0)
    if proc.returncode != 0 or len(out) <= WAV_HEADER:
        return None
    return out[WAV_HEADER:]


async def post_speak(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    body = await request.json()
    client_id = body.get("client", "")
    if deps.broker is None or not deps.broker.is_web_controller(client_id):
        return web.json_response({"error": "not-controlling"})
    if deps.audio is None:
        return web.json_response({"error": "audio unavailable"})
    if not tts_available():
        return web.json_response({"error": "tts-unavailable"})
    text = str(body.get("text", ""))[:500]
    if not text.strip():
        return web.json_response({"error": "empty text"})
    pcm = await synth_pcm(text)
    if pcm is None:
        return web.json_response({"error": "tts-failed"})
    deps.audio.play_pcm(pcm)
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_post("/api/speak", post_speak)
```

`bridge/milo_bridge/webapp/api/__init__.py` becomes:

```python
"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import media, speak, status


def register_routes(app: web.Application) -> None:
    status.register(app)
    media.register(app)
    speak.register(app)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp/test_media_endpoints.py -q` → 5 passed.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/api bridge/tests/webapp/test_media_endpoints.py
git commit -m "feat(web): MJPEG camera stream and espeak-ng TTS speak endpoint"
```

---

### Task 7: Graph search, poses/faces metadata, log ring buffer

**Files:**
- Modify: `bridge/milo_bridge/graph/store.py` (add `search_text`)
- Modify: `bridge/milo_bridge/graph/api.py` (add `_op_search_text`)
- Create: `bridge/milo_bridge/webapp/logbuf.py`
- Create: `bridge/milo_bridge/webapp/api/graph.py`, `api/motion_meta.py`, `api/logs.py`
- Modify: `bridge/milo_bridge/webapp/api/__init__.py`, `bridge/milo_bridge/main.py` (attach log handler, pass `log_buffer`)
- Test: `bridge/tests/webapp/test_graph_api.py`, `bridge/tests/webapp/test_logs.py`

**Interfaces:**
- Produces: `GraphStore.search_text(q: str, limit: int = 25) -> dict` returning `{"nodes": [node dicts], "edges": [edge dicts]}` where nodes match `type LIKE %q% OR props LIKE %q%` and edges are all edges among the matched node set; `GraphApi` op `{"op": "search_text", "q": ...}`; `POST /api/graph` (JSON body → `GraphApi.handle()` result); `GET /api/graph/search?q=&limit=`; `GET /api/poses` → `{"poses": [names]}`; `GET /api/faces` → `{"faces": [names]}`; `GET /api/logs?n=` → `{"lines": [...]}`; `RingBufferLogHandler(capacity=400)` with `.lines(n) -> list[str]` and `on_line: Callable[[str], None] | None` hook (ws push wired in `main`? no — wired in `create_app` via `deps.log_buffer.on_line`, broadcasting `{"t":"log","line":...}`).

- [ ] **Step 1: Write the failing tests**

`bridge/tests/webapp/test_graph_api.py`:

```python
from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from .fakes import make_deps


async def _client(deps):
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _seed(store):
    alice = store.upsert_node("person", {"name": "Alice", "likes": "tennis"})
    bob = store.upsert_node("person", {"name": "Bob"})
    ball = store.upsert_node("object", {"name": "red ball"})
    store.upsert_edge(alice.id, ball.id, "owns")
    store.upsert_edge(alice.id, bob.id, "knows")
    return alice, bob, ball


async def test_search_matches_props_and_includes_edges():
    deps = make_deps()
    alice, bob, ball = _seed(deps.graph_store)
    res = deps.graph_store.search_text("alice")
    ids = {n["id"] for n in res["nodes"]}
    assert alice.id in ids and bob.id not in ids
    res2 = deps.graph_store.search_text("person")
    ids2 = {n["id"] for n in res2["nodes"]}
    assert {alice.id, bob.id} <= ids2
    edge_pairs = {(e["src"], e["dst"]) for e in res2["edges"]}
    assert (alice.id, bob.id) in edge_pairs          # both endpoints matched
    assert (alice.id, ball.id) not in edge_pairs     # ball didn't match


async def test_graph_http_passthrough_and_search():
    deps = make_deps()
    _seed(deps.graph_store)
    client = await _client(deps)
    try:
        resp = await client.post("/api/graph", json={"op": "query", "id": 1})
        data = await resp.json()
        assert "error" not in data
        resp = await client.get("/api/graph/search", params={"q": "tennis"})
        data = await resp.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["props"]["name"] == "Alice"
    finally:
        await client.close()


async def test_poses_and_faces_endpoints():
    client = await _client(make_deps())
    try:
        poses = await (await client.get("/api/poses")).json()
        assert "walk_forward" in poses["poses"] or len(poses["poses"]) > 0
        faces = await (await client.get("/api/faces")).json()
        assert "cute" in faces["faces"]
    finally:
        await client.close()
```

`bridge/tests/webapp/test_logs.py`:

```python
import logging

from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from milo_bridge.webapp.logbuf import RingBufferLogHandler
from .fakes import make_deps


def test_ring_buffer_caps_and_tails():
    h = RingBufferLogHandler(capacity=3)
    logger = logging.getLogger("rbtest")
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    for i in range(5):
        logger.info("line %d", i)
    logger.removeHandler(h)
    assert len(h.lines(10)) == 3
    assert h.lines(1)[0].endswith("line 4")
    assert h.lines(2)[0].endswith("line 3")


async def test_logs_endpoint():
    h = RingBufferLogHandler(capacity=10)
    logging.getLogger("milo-web-test").addHandler(h)
    logging.getLogger("milo-web-test").setLevel(logging.INFO)
    logging.getLogger("milo-web-test").info("hello from test")
    deps = make_deps(log_buffer=h)
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        data = await (await client.get("/api/logs?n=5")).json()
        assert any("hello from test" in line for line in data["lines"])
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/webapp/test_graph_api.py bridge/tests/webapp/test_logs.py -q`
Expected: FAIL — `search_text` missing, modules missing.

- [ ] **Step 3: Implement**

`bridge/milo_bridge/graph/store.py` — add method to `GraphStore` (after `query`):

```python
    def search_text(self, q: str, limit: int = 25) -> dict:
        """Free-text search over node type and props JSON; edges among matches."""
        pat = f"%{q}%"
        cur = self._conn.execute(
            "SELECT id, type, props, created_at, updated_at FROM nodes "
            "WHERE type LIKE ? OR props LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (pat, pat, limit),
        )
        nodes = [self._row_to_node(row) for row in cur.fetchall()]
        ids = {n.id for n in nodes}
        edges = []
        if ids:
            marks = ",".join("?" * len(ids))
            cur = self._conn.execute(
                f"SELECT id, src, dst, type, props, created_at FROM edges "
                f"WHERE src IN ({marks}) AND dst IN ({marks})",
                (*ids, *ids),
            )
            edges = [Edge(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in cur.fetchall()]
        return {"nodes": [n.to_dict() for n in nodes], "edges": [e.to_dict() for e in edges]}
```

(If `GraphStore` has no `_row_to_node` helper, build the `Node` inline exactly the way `get_node` does — copy its row-unpacking expression.)

`bridge/milo_bridge/graph/api.py` — add to `GraphApi`:

```python
    def _op_search_text(self, req: dict) -> dict:
        return self._store.search_text(str(req.get("q", "")), int(req.get("limit", 25)))
```

`bridge/milo_bridge/webapp/logbuf.py`:

```python
"""Ring buffer of formatted log lines + optional live line hook."""
from __future__ import annotations

import logging
from collections import deque
from typing import Callable


class RingBufferLogHandler(logging.Handler):
    def __init__(self, capacity: int = 400):
        super().__init__()
        self._buf: deque[str] = deque(maxlen=capacity)
        self.on_line: Callable[[str], None] | None = None
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        self._buf.append(line)
        if self.on_line is not None:
            try:
                self.on_line(line)
            except Exception:
                pass

    def lines(self, n: int = 200) -> list[str]:
        items = list(self._buf)
        return items[-n:]
```

`bridge/milo_bridge/webapp/api/graph.py`:

```python
from aiohttp import web


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
    limit = int(request.query.get("limit", "25"))
    if not q:
        return web.json_response({"nodes": [], "edges": []})
    return web.json_response(deps.graph_store.search_text(q, limit))


def register(app: web.Application) -> None:
    app.router.add_post("/api/graph", post_graph)
    app.router.add_get("/api/graph/search", get_search)
```

`bridge/milo_bridge/webapp/api/motion_meta.py`:

```python
from aiohttp import web

from ...poses import POSES
from ..motion import list_faces


async def get_poses(request: web.Request) -> web.Response:
    return web.json_response({"poses": sorted(POSES)})


async def get_faces(request: web.Request) -> web.Response:
    return web.json_response({"faces": list_faces()})


def register(app: web.Application) -> None:
    app.router.add_get("/api/poses", get_poses)
    app.router.add_get("/api/faces", get_faces)
```

`bridge/milo_bridge/webapp/api/logs.py`:

```python
from aiohttp import web


async def get_logs(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    if deps.log_buffer is None:
        return web.json_response({"lines": []})
    n = int(request.query.get("n", "200"))
    return web.json_response({"lines": deps.log_buffer.lines(n)})


def register(app: web.Application) -> None:
    app.router.add_get("/api/logs", get_logs)
```

`bridge/milo_bridge/webapp/api/__init__.py`:

```python
"""Route registry: adding a server feature = one import + one line here."""
from aiohttp import web

from . import graph, logs, media, motion_meta, speak, status


def register_routes(app: web.Application) -> None:
    status.register(app)
    media.register(app)
    speak.register(app)
    graph.register(app)
    motion_meta.register(app)
    logs.register(app)
```

`bridge/milo_bridge/webapp/__init__.py` — in `create_app`, after `register_ws(app)`, wire live log push:

```python
    if deps.log_buffer is not None:
        from .ws import broadcast_json
        deps.log_buffer.on_line = lambda line: broadcast_json(app, {"t": "log", "line": line})
```

`bridge/milo_bridge/main.py` — in `main()` before building `WebDeps`:

```python
    from .webapp.logbuf import RingBufferLogHandler
    log_buffer = RingBufferLogHandler()
    logging.getLogger().addHandler(log_buffer)
```

and pass `log_buffer=log_buffer` in `WebDeps`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/webapp -q` → all webapp tests pass.
Run: `python -m pytest bridge/tests common/tests -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add bridge
git commit -m "feat(web): graph text search + graph/poses/faces/logs endpoints + log ring buffer"
```

---

### Task 8: Frontend shell — theme, grid framework, bus, Status + Log cards

**Files:**
- Replace: `bridge/milo_bridge/webapp/static/index.html`
- Create: `static/css/theme.css`, `static/css/grid.css`, `static/js/main.js`, `static/js/registry.js`, `static/js/bus.js`, `static/js/grid.js`, `static/js/cards/status.js`, `static/js/cards/log.js`
- Test: `bridge/tests/webapp/test_static_integrity.py`

**Interfaces:**
- Consumes: `/ws` protocol (Task 5: `hello`, `telemetry`, `control`, `ack`, `err`, `log`), `/api/status`, `/api/logs`.
- Produces (JS contracts every card in Tasks 9-10 relies on):
  - `bus.js` exports `createBus()` → `{ send(obj), sendBytes(u8), on(topic, fn) -> off, clientId, controlled -> bool, onBinary(fn) -> off, connected -> bool }`. Auto-reconnect (backoff 1 s→10 s), `hb` every 5 s, tracks `hello` (clientId) and `control` (`you` flag → `controlled`).
  - Card module default export: `{ id, title, w, h, needsControl (bool, optional), mount(el, ctx) -> cleanupFn|undefined }` with `ctx = { bus, refreshers }`.
  - `grid.js` exports `initGrid(container, cards, bus)`: renders card shells (header = title + drag handle + ✕, body = mount target, corner resize handle), drag-to-reorder, resize in grid units (12-col grid, 80 px row), persists `{order, sizes, hidden}` to `localStorage["milo.layout.v1"]`, exposes global "Add card" menu + "Reset layout" in the page header, disables inputs inside cards with `needsControl` when `!bus.controlled` (adds `.locked` class).

- [ ] **Step 1: Write the failing test**

`bridge/tests/webapp/test_static_integrity.py`:

```python
"""Every file referenced by index.html and registry.js must exist —
guards the 'adding a card = one file + one line' workflow."""
import re
from pathlib import Path

STATIC = Path("bridge/milo_bridge/webapp/static")


def test_index_references_exist():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert (STATIC / ref).exists(), f"index.html references missing {ref}"


def test_registry_imports_exist():
    js = (STATIC / "js" / "registry.js").read_text(encoding="utf-8")
    for ref in re.findall(r"from\s+['\"]\./(.+?)['\"]", js):
        assert (STATIC / "js" / ref).exists(), f"registry.js imports missing {ref}"


def test_shell_files_exist():
    for f in ["index.html", "css/theme.css", "css/grid.css", "js/main.js",
              "js/registry.js", "js/bus.js", "js/grid.js",
              "js/cards/status.js", "js/cards/log.js"]:
        assert (STATIC / f).exists(), f"missing {f}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest bridge/tests/webapp/test_static_integrity.py -q`
Expected: FAIL — css/js files missing.

- [ ] **Step 3: Implement**

`static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MILO</title>
<link rel="stylesheet" href="/static/css/theme.css">
<link rel="stylesheet" href="/static/css/grid.css">
</head>
<body>
<header id="topbar">
  <span class="brand">MILO</span>
  <span id="conn-dot" class="dot" title="connection"></span>
  <span id="owner-label" class="muted">owner: —</span>
  <span class="spacer"></span>
  <button id="btn-control" class="btn">Take Control</button>
  <button id="btn-stop" class="btn danger">STOP</button>
  <button id="btn-add" class="btn ghost">+ Card</button>
  <button id="btn-reset" class="btn ghost" title="Reset layout">⟲</button>
  <button id="btn-theme" class="btn ghost" title="Toggle theme">◐</button>
</header>
<main id="grid"></main>
<div id="add-menu" class="menu hidden"></div>
<script type="module" src="/static/js/main.js"></script>
</body>
</html>
```

`static/css/theme.css`:

```css
:root {
  --bg: #fafafa; --surface: #ffffff; --ink: #111111; --muted: #777777;
  --line: #dddddd; --ok: #1a7f37; --danger: #c0392b;
  --font: system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
:root[data-theme="dark"] {
  --bg: #0d0d0d; --surface: #161616; --ink: #f2f2f2; --muted: #8a8a8a;
  --line: #2c2c2c; --ok: #2ecc71; --danger: #e74c3c;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--bg); color: var(--ink); font: 14px/1.45 var(--font); }
#topbar {
  position: sticky; top: 0; z-index: 50; display: flex; align-items: center;
  gap: 10px; padding: 8px 14px; background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.brand { font-weight: 700; letter-spacing: 0.18em; }
.spacer { flex: 1; }
.muted { color: var(--muted); }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--danger); display: inline-block; }
.dot.live { background: var(--ok); }
.btn {
  font: inherit; color: var(--ink); background: var(--surface);
  border: 1px solid var(--ink); border-radius: 4px; padding: 5px 12px; cursor: pointer;
}
.btn:hover { background: var(--ink); color: var(--surface); }
.btn.danger { border-color: var(--danger); color: var(--danger); }
.btn.danger:hover { background: var(--danger); color: #fff; }
.btn.ghost { border-color: var(--line); }
.btn.active { background: var(--ink); color: var(--surface); }
input, select, textarea {
  font: inherit; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 4px; padding: 4px 8px;
}
input[type="range"] { padding: 0; }
.menu {
  position: fixed; top: 48px; right: 14px; background: var(--surface);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px; z-index: 60;
  display: flex; flex-direction: column; gap: 4px; min-width: 160px;
}
.menu.hidden { display: none; }
.menu button { text-align: left; border: none; background: none; color: var(--ink);
  padding: 6px 10px; cursor: pointer; font: inherit; border-radius: 4px; }
.menu button:hover { background: var(--bg); }
canvas { display: block; }
```

`static/css/grid.css`:

```css
#grid {
  display: grid; grid-template-columns: repeat(12, 1fr);
  grid-auto-rows: 80px; gap: 10px; padding: 12px;
}
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  display: flex; flex-direction: column; overflow: hidden; position: relative;
}
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
@media (max-width: 800px) {
  #grid { grid-template-columns: repeat(2, 1fr); }
  .card { grid-column: span 2 !important; }
}
```

`static/js/bus.js`:

```js
// Single WebSocket to the robot: JSON topics + binary audio, auto-reconnect.
export function createBus() {
  const listeners = new Map();   // topic -> Set<fn>
  const binHandlers = new Set();
  let ws = null, backoff = 1000, hbTimer = null;
  const bus = { clientId: null, controlled: false, connected: false };

  function emit(topic, data) {
    (listeners.get(topic) || []).forEach((fn) => fn(data));
  }

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      bus.connected = true; backoff = 1000; emit("_open", {});
      hbTimer = setInterval(() => bus.send({ t: "hb" }), 5000);
    };
    ws.onclose = () => {
      bus.connected = false; bus.controlled = false;
      clearInterval(hbTimer); emit("_close", {});
      setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 10000);
    };
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) { binHandlers.forEach((fn) => fn(new Uint8Array(ev.data))); return; }
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.t === "hello") bus.clientId = msg.id;
      if (msg.t === "control") { bus.controlled = !!msg.you; }
      emit(msg.t, msg);
    };
  }

  bus.send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };
  bus.sendBytes = (u8) => { if (ws && ws.readyState === 1) ws.send(u8); };
  bus.on = (topic, fn) => {
    if (!listeners.has(topic)) listeners.set(topic, new Set());
    listeners.get(topic).add(fn);
    return () => listeners.get(topic).delete(fn);
  };
  bus.onBinary = (fn) => { binHandlers.add(fn); return () => binHandlers.delete(fn); };
  connect();
  return bus;
}
```

`static/js/grid.js`:

```js
// CSS-grid card dashboard: drag to reorder, corner-resize, persistence.
const KEY = "milo.layout.v1";

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function saveLayout(layout) { localStorage.setItem(KEY, JSON.stringify(layout)); }

export function initGrid(container, cards, bus) {
  const layout = loadLayout();
  layout.order = (layout.order || []).filter((id) => cards.some((c) => c.id === id));
  for (const c of cards) if (!layout.order.includes(c.id)) layout.order.push(c.id);
  layout.sizes = layout.sizes || {};
  layout.hidden = layout.hidden || [];

  const shells = new Map();

  function render() {
    container.innerHTML = "";
    for (const id of layout.order) {
      if (layout.hidden.includes(id)) continue;
      const card = cards.find((c) => c.id === id);
      const el = buildShell(card);
      shells.set(id, el);
      container.appendChild(el);
    }
    updateLocks();
  }

  function buildShell(card) {
    const size = layout.sizes[card.id] || { w: card.w, h: card.h };
    const el = document.createElement("section");
    el.className = "card";
    el.dataset.id = card.id;
    el.style.gridColumn = `span ${size.w}`;
    el.style.gridRow = `span ${size.h}`;
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

  // -- corner resize --------------------------------------------------------
  function wireResize(el, card) {
    const handle = el.querySelector(".resize");
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const start = { x: e.clientX, y: e.clientY };
      const cell = container.getBoundingClientRect().width / 12;
      const size = layout.sizes[card.id] || { w: card.w, h: card.h };
      const move = (ev) => {
        const w = Math.max(2, Math.min(12, size.w + Math.round((ev.clientX - start.x) / cell)));
        const h = Math.max(2, Math.min(10, size.h + Math.round((ev.clientY - start.y) / 80)));
        el.style.gridColumn = `span ${w}`;
        el.style.gridRow = `span ${h}`;
        layout.sizes[card.id] = { w, h };
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
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

  // -- header helpers -------------------------------------------------------
  const menu = document.getElementById("add-menu");
  document.getElementById("btn-add").onclick = () => {
    menu.classList.toggle("hidden");
    menu.innerHTML = "";
    const hidden = layout.hidden;
    if (!hidden.length) menu.innerHTML = "<button disabled>all cards shown</button>";
    for (const id of [...hidden]) {
      const card = cards.find((c) => c.id === id);
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

`static/js/registry.js`:

```js
// Adding a card = create js/cards/<name>.js + add one line here.
import status from "./cards/status.js";
import log from "./cards/log.js";

export const cards = [status, log];
```

`static/js/main.js`:

```js
import { createBus } from "./bus.js";
import { initGrid } from "./grid.js";
import { cards } from "./registry.js";

// theme
const saved = localStorage.getItem("milo.theme");
if (saved) document.documentElement.dataset.theme = saved;
else if (matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.dataset.theme = "dark";
document.getElementById("btn-theme").onclick = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("milo.theme", next);
};

const bus = createBus();

// connection dot + owner label + control button
const dot = document.getElementById("conn-dot");
const owner = document.getElementById("owner-label");
const btnControl = document.getElementById("btn-control");
bus.on("_open", () => dot.classList.add("live"));
bus.on("_close", () => { dot.classList.remove("live"); owner.textContent = "owner: —"; });
bus.on("control", (m) => {
  owner.textContent = `owner: ${m.owner}`;
  btnControl.textContent = m.you ? "Release Control" : "Take Control";
  btnControl.classList.toggle("active", m.you);
});
btnControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
document.getElementById("btn-stop").onclick = () => bus.send({ t: "stop" });

initGrid(document.getElementById("grid"), cards, bus);
```

`static/js/cards/status.js`:

```js
export default {
  id: "status", title: "Status", w: 4, h: 3,
  mount(el, { bus }) {
    el.innerHTML = `<table style="width:100%;border-collapse:collapse" id="st"></table>`;
    const rows = [
      ["link", "Brain link"], ["owner", "Control owner"], ["gait_backend", "Gait backend"],
      ["cpu_percent", "CPU %"], ["temp_c", "SoC temp °C"], ["mem_percent", "RAM %"],
      ["uptime_s", "Web uptime s"],
    ];
    const table = el.querySelector("#st");
    table.innerHTML = rows.map(([k, label]) =>
      `<tr><td class="muted" style="padding:2px 8px 2px 0">${label}</td>
       <td id="st-${k}" style="text-align:right">—</td></tr>`).join("");
    const off = bus.on("telemetry", (m) => {
      for (const [k] of rows) {
        const cell = el.querySelector(`#st-${k}`);
        if (cell) cell.textContent = m[k] == null ? "n/a" : m[k];
      }
    });
    return off;
  },
};
```

`static/js/cards/log.js`:

```js
export default {
  id: "log", title: "Bridge Log", w: 8, h: 3,
  mount(el, { bus }) {
    el.innerHTML = `<pre id="loglines" style="margin:0;font-size:11px;white-space:pre-wrap"></pre>`;
    const pre = el.querySelector("#loglines");
    const push = (line) => {
      pre.textContent += line + "\n";
      const lines = pre.textContent.split("\n");
      if (lines.length > 300) pre.textContent = lines.slice(-300).join("\n");
      el.scrollTop = el.scrollHeight;
    };
    fetch("/api/logs?n=100").then((r) => r.json())
      .then((d) => d.lines.forEach(push)).catch(() => {});
    return bus.on("log", (m) => push(m.line));
  },
};
```

- [ ] **Step 4: Run tests + manual smoke**

Run: `python -m pytest bridge/tests/webapp -q` → all pass (integrity test now green).
Manual smoke (off-Pi): temporarily run `python -c "..."` bootstrapping `create_app(make_deps())` on port 8080 — or simply proceed; Task 11 has the documented full smoke.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/static bridge/tests/webapp/test_static_integrity.py
git commit -m "feat(web): frontend shell — theme, card grid with drag/resize/persist, bus, status+log cards"
```

---

### Task 9: Media & control cards — Camera, Ears, Voice, Move

**Files:**
- Create: `static/js/cards/camera.js`, `ears.js`, `voice.js`, `move.js`
- Modify: `static/js/registry.js`

**Interfaces:**
- Consumes: bus contract (Task 8), `/stream/camera`, WS binary framing (`0x01` out / `0x02` in), `POST /api/speak`, WS `{"t":"gait"}` / `{"t":"audio"}`.
- Produces: nothing consumed later — leaf modules. Audio constants both sides must match the robot: `SAMPLE_RATE = 16000`, stereo capture from robot (2 ch int16), intercom sent as mono int16 at `SAMPLE_RATE` (bridge `AudioIO.play_pcm` expects its native mono format; if the hardware rate differs, change the single `SAMPLE_RATE` constant at the top of `ears.js`/`voice.js` — called out in docs).

- [ ] **Step 1: Implement all four cards**

`static/js/cards/camera.js`:

```js
export default {
  id: "camera", title: "Camera", w: 5, h: 4,
  mount(el) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <img id="cam" src="/stream/camera" alt="camera offline"
             style="width:100%;flex:1;object-fit:contain;background:#000;border-radius:4px"
             onerror="this.dataset.err=1">
        <div><button class="btn" id="snap">Snapshot</button></div>
      </div>`;
    const img = el.querySelector("#cam");
    el.querySelector("#snap").onclick = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
      c.getContext("2d").drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.href = c.toDataURL("image/jpeg");
      a.download = `milo-${Date.now()}.jpg`;
      a.click();
    };
  },
};
```

`static/js/cards/ears.js`:

```js
const SAMPLE_RATE = 16000;   // must match the robot's capture rate
const CHANNELS = 2;

export default {
  id: "ears", title: "Ears (Listen)", w: 3, h: 3,
  mount(el, { bus }) {
    el.innerHTML = `
      <button class="btn" id="listen">▶ Listen</button>
      <canvas id="vu" width="220" height="48" style="margin-top:10px;width:100%"></canvas>
      <div class="muted" id="ears-note"></div>`;
    const btn = el.querySelector("#listen");
    const vu = el.querySelector("#vu").getContext("2d");
    let ctx = null, playHead = 0, on = false, levels = [0, 0];

    function drawVU() {
      const w = 220, h = 48;
      vu.clearRect(0, 0, w, h);
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      levels.forEach((lv, i) => {
        vu.fillStyle = ink;
        vu.fillRect(0, i * 26, Math.min(1, lv * 4) * w, 18);
      });
      if (on) requestAnimationFrame(drawVU);
    }

    const offBin = bus.onBinary((u8) => {
      if (!on || u8[0] !== 0x01) return;
      const pcm = new Int16Array(u8.buffer, u8.byteOffset + 1, (u8.byteLength - 1) >> 1);
      const frames = pcm.length / CHANNELS;
      const buf = ctx.createBuffer(CHANNELS, frames, SAMPLE_RATE);
      let sum = [0, 0];
      for (let ch = 0; ch < CHANNELS; ch++) {
        const out = buf.getChannelData(ch);
        for (let i = 0; i < frames; i++) {
          const v = pcm[i * CHANNELS + ch] / 32768;
          out[i] = v; sum[ch] += v * v;
        }
      }
      levels = sum.map((s) => Math.sqrt(s / frames));
      const src = ctx.createBufferSource();
      src.buffer = buf; src.connect(ctx.destination);
      playHead = Math.max(playHead, ctx.currentTime + 0.05);
      src.start(playHead);
      playHead += buf.duration;
    });

    btn.onclick = () => {
      on = !on;
      btn.textContent = on ? "◼ Mute" : "▶ Listen";
      btn.classList.toggle("active", on);
      if (on && !ctx) ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (on) { playHead = 0; drawVU(); }
      bus.send({ t: "audio", on });
    };
    return () => { offBin(); if (ctx) ctx.close(); bus.send({ t: "audio", on: false }); };
  },
};
```

`static/js/cards/voice.js`:

```js
const SAMPLE_RATE = 16000;   // intercom send rate — must match robot playback

export default {
  id: "voice", title: "Voice (Speak)", w: 3, h: 3, needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px">
        <button class="btn" id="ptt">🎙 Hold to Talk</button>
        <div style="display:flex;gap:6px">
          <input id="say" placeholder="Type something to say…" style="flex:1">
          <button class="btn" id="speak">Say</button>
        </div>
        <div class="muted" id="voice-note"></div>
      </div>`;
    const note = el.querySelector("#voice-note");
    let ctx = null, stream = null, node = null;

    async function startTalk() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch { note.textContent = "microphone permission denied"; return; }
      ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      const src = ctx.createMediaStreamSource(stream);
      node = ctx.createScriptProcessor(2048, 1, 1);
      node.onaudioprocess = (ev) => {
        const f32 = ev.inputBuffer.getChannelData(0);
        const out = new Uint8Array(1 + f32.length * 2);
        out[0] = 0x02;
        const view = new DataView(out.buffer);
        for (let i = 0; i < f32.length; i++)
          view.setInt16(1 + i * 2, Math.max(-1, Math.min(1, f32[i])) * 32767, true);
        bus.sendBytes(out);
      };
      src.connect(node); node.connect(ctx.destination);
    }
    function stopTalk() {
      if (node) node.disconnect();
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (ctx) ctx.close();
      ctx = stream = node = null;
    }
    const ptt = el.querySelector("#ptt");
    ptt.onpointerdown = startTalk;
    ptt.onpointerup = ptt.onpointerleave = stopTalk;

    el.querySelector("#speak").onclick = async () => {
      const text = el.querySelector("#say").value.trim();
      if (!text) return;
      const r = await fetch("/api/speak", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, client: bus.clientId }),
      }).then((r) => r.json()).catch(() => ({ error: "network" }));
      note.textContent = r.error ? `✗ ${r.error}` : "✓ spoke";
    };
    return stopTalk;
  },
};
```

`static/js/cards/move.js`:

```js
const SEND_MS = 100;

export default {
  id: "move", title: "Move", w: 4, h: 4, needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;gap:14px;height:100%">
        <div id="pad" style="flex:1;max-width:220px;aspect-ratio:1;border:1px solid var(--line);
             border-radius:8px;position:relative;touch-action:none">
          <div id="knob" style="position:absolute;width:26px;height:26px;border-radius:50%;
               background:var(--ink);left:calc(50% - 13px);top:calc(50% - 13px)"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;flex:1">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, Q/E to turn</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const pad = el.querySelector("#pad"), knob = el.querySelector("#knob");
    const speed = el.querySelector("#speed");
    let vec = { vx: 0, vy: 0, yaw: 0 }, timer = null;

    function sending(active) {
      if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
      if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
    }
    const scaled = () => {
      const k = speed.value / 100;
      return { vx: vec.vx * k, vy: vec.vy * k, yaw: vec.yaw * 2 * k };
    };

    // joystick
    pad.addEventListener("pointerdown", (e) => {
      pad.setPointerCapture(e.pointerId);
      const rect = pad.getBoundingClientRect();
      const move = (ev) => {
        const x = Math.max(-1, Math.min(1, ((ev.clientX - rect.left) / rect.width) * 2 - 1));
        const y = Math.max(-1, Math.min(1, ((ev.clientY - rect.top) / rect.height) * 2 - 1));
        knob.style.left = `calc(${(x + 1) * 50}% - 13px)`;
        knob.style.top = `calc(${(y + 1) * 50}% - 13px)`;
        vec = { vx: -y, vy: x, yaw: 0 };
        sending(true);
      };
      const up = () => {
        pad.removeEventListener("pointermove", move);
        knob.style.left = "calc(50% - 13px)"; knob.style.top = "calc(50% - 13px)";
        vec = { vx: 0, vy: 0, yaw: 0 }; sending(false);
      };
      pad.addEventListener("pointermove", move);
      pad.addEventListener("pointerup", up, { once: true });
      move(e);
    });

    // keyboard
    const keys = { w: [1,0,0], s: [-1,0,0], a: [0,-1,0], d: [0,1,0], q: [0,0,-1], e: [0,0,1],
      ArrowUp: [1,0,0], ArrowDown: [-1,0,0], ArrowLeft: [0,0,-1], ArrowRight: [0,0,1] };
    const down = new Set();
    const sync = () => {
      let vx = 0, vy = 0, yaw = 0;
      down.forEach((k) => { const [a,b,c] = keys[k]; vx += a; vy += b; yaw += c; });
      vec = { vx: Math.sign(vx), vy: Math.sign(vy), yaw: Math.sign(yaw) };
      sending(down.size > 0);
    };
    const kd = (e) => { if (keys[e.key] && !e.repeat && e.target.tagName !== "INPUT") { down.add(e.key); sync(); } };
    const ku = (e) => { if (keys[e.key]) { down.delete(e.key); sync(); } };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => { sending(false); window.removeEventListener("keydown", kd); window.removeEventListener("keyup", ku); };
  },
};
```

`static/js/registry.js` becomes:

```js
// Adding a card = create js/cards/<name>.js + add one line here.
import status from "./cards/status.js";
import log from "./cards/log.js";
import camera from "./cards/camera.js";
import ears from "./cards/ears.js";
import voice from "./cards/voice.js";
import move from "./cards/move.js";

export const cards = [status, camera, ears, voice, move, log];
```

- [ ] **Step 2: Verify**

Run: `python -m pytest bridge/tests/webapp/test_static_integrity.py -q` → passes (all new imports resolve).
Manual: serve off-Pi (Task 11 smoke script) — camera card shows fake frames, move card locks/unlocks with control.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static
git commit -m "feat(web): camera, ears, voice, move cards"
```

---

### Task 10: Poses/Emotes, Servo Test, Sensors, Memory Graph cards

**Files:**
- Create: `static/js/cards/poses.js`, `servos.js`, `sensors.js`, `graph.js`
- Modify: `static/js/registry.js`

**Interfaces:**
- Consumes: `/api/poses`, `/api/faces`, `/api/status`, `/api/graph/search`, WS `pose|face|servo`, telemetry `imu`.

- [ ] **Step 1: Implement all four cards**

`static/js/cards/poses.js`:

```js
export default {
  id: "poses", title: "Poses & Emotes", w: 4, h: 3, needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `<div class="muted">Poses</div><div id="pose-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px"></div>
      <div class="muted">Faces</div><div id="face-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"></div>`;
    const fill = (sel, names, type) => {
      const box = el.querySelector(sel);
      names.forEach((name) => {
        const b = document.createElement("button");
        b.className = "btn"; b.textContent = name;
        b.onclick = () => bus.send({ t: type, name });
        box.appendChild(b);
      });
    };
    fetch("/api/poses").then((r) => r.json()).then((d) => fill("#pose-btns", d.poses, "pose"));
    fetch("/api/faces").then((r) => r.json()).then((d) => fill("#face-btns", d.faces, "face"));
  },
};
```

`static/js/cards/servos.js`:

```js
const SERVOS = ["R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4"];

export default {
  id: "servos", title: "Servo Test", w: 4, h: 4, needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = SERVOS.map((s) => `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="width:26px;font-weight:600">${s}</span>
        <input type="range" min="0" max="180" value="90" data-servo="${s}" style="flex:1">
        <span data-val="${s}" style="width:34px;text-align:right">90°</span>
      </div>`).join("") +
      `<button class="btn" id="center" style="margin-top:8px">Center All (90°)</button>`;
    el.querySelectorAll("input[type=range]").forEach((sl) => {
      sl.oninput = () => {
        el.querySelector(`[data-val="${sl.dataset.servo}"]`).textContent = `${sl.value}°`;
        bus.send({ t: "servo", servo: sl.dataset.servo, deg: Number(sl.value) });
      };
    });
    el.querySelector("#center").onclick = () => SERVOS.forEach((s) => {
      const sl = el.querySelector(`[data-servo="${s}"]`);
      sl.value = 90; sl.oninput();
    });
  },
};
```

`static/js/cards/sensors.js`:

```js
export default {
  id: "sensors", title: "Sensors", w: 4, h: 3,
  mount(el, { bus }) {
    el.innerHTML = `
      <canvas id="imu-spark" width="360" height="70" style="width:100%"></canvas>
      <div id="imu-now" class="muted" style="margin:4px 0 10px">imu: —</div>
      <div id="hw"></div>`;
    const hist = [];
    const cv = el.querySelector("#imu-spark"), g = cv.getContext("2d");
    const offT = bus.on("telemetry", (m) => {
      const now = el.querySelector("#imu-now");
      if (!m.imu) { now.textContent = "imu: n/a"; return; }
      now.textContent = `pitch ${m.imu.pitch?.toFixed(1)}°  roll ${m.imu.roll?.toFixed(1)}°`;
      hist.push([m.imu.pitch || 0, m.imu.roll || 0]);
      if (hist.length > 120) hist.shift();
      g.clearRect(0, 0, cv.width, cv.height);
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      [0, 1].forEach((k) => {
        g.strokeStyle = k === 0 ? ink : muted;
        g.beginPath();
        hist.forEach(([p, r], i) => {
          const v = k === 0 ? p : r;
          const y = 35 - (v / 90) * 33;
          i ? g.lineTo(i * 3, y) : g.moveTo(0, y);
        });
        g.stroke();
      });
    });
    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `<span style="margin-right:12px">${ok ? "●" : "○"} ${k}</span>`).join("");
    });
    return offT;
  },
};
```

`static/js/cards/graph.js`:

```js
// Force-directed canvas view of the knowledge graph with text search.
export default {
  id: "graph", title: "Memory Graph", w: 8, h: 5,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <input id="gq" placeholder="Search memory… (name, type, anything)" style="flex:1">
        <button class="btn" id="gsearch">Search</button>
      </div>
      <canvas id="gcv" style="width:100%;height:calc(100% - 78px);background:var(--bg);border-radius:4px"></canvas>
      <div id="gdetail" class="muted" style="height:34px;overflow:auto;font-size:12px"></div>`;
    const cv = el.querySelector("#gcv"), g = cv.getContext("2d");
    let nodes = [], edges = [], selected = null, raf = null;

    function resize() { cv.width = cv.clientWidth; cv.height = cv.clientHeight; }
    resize();

    function load(data) {
      const W = cv.width, H = cv.height;
      nodes = data.nodes.map((n, i) => ({
        ...n, x: W / 2 + Math.cos(i) * 80, y: H / 2 + Math.sin(i) * 80, vx: 0, vy: 0,
      }));
      edges = data.edges;
      selected = null;
      if (!raf) tick();
    }

    function tick() {
      // physics: repulsion + springs + centering
      const W = cv.width, H = cv.height;
      for (const a of nodes) {
        a.vx += (W / 2 - a.x) * 0.001; a.vy += (H / 2 - a.y) * 0.001;
        for (const b of nodes) {
          if (a === b) continue;
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = Math.max(100, dx * dx + dy * dy);
          a.vx += (dx / d2) * 600; a.vy += (dy / d2) * 600;
        }
      }
      for (const e of edges) {
        const a = nodes.find((n) => n.id === e.src), b = nodes.find((n) => n.id === e.dst);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        a.vx += dx * 0.003; a.vy += dy * 0.003;
        b.vx -= dx * 0.003; b.vy -= dy * 0.003;
      }
      for (const n of nodes) {
        n.vx *= 0.85; n.vy *= 0.85; n.x += n.vx; n.y += n.vy;
      }
      draw();
      raf = requestAnimationFrame(tick);
    }

    function draw() {
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      const ok = getComputedStyle(document.documentElement).getPropertyValue("--ok");
      g.clearRect(0, 0, cv.width, cv.height);
      g.strokeStyle = muted;
      for (const e of edges) {
        const a = nodes.find((n) => n.id === e.src), b = nodes.find((n) => n.id === e.dst);
        if (!a || !b) continue;
        g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
      }
      for (const n of nodes) {
        g.fillStyle = n === selected ? ok : ink;
        g.beginPath(); g.arc(n.x, n.y, 7, 0, 7); g.fill();
        g.fillStyle = muted; g.font = "10px sans-serif";
        g.fillText(`${n.props?.name || n.type}#${n.id}`, n.x + 9, n.y + 3);
      }
    }

    cv.onclick = (ev) => {
      const r = cv.getBoundingClientRect();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      selected = nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < 120) || null;
      el.querySelector("#gdetail").textContent = selected
        ? `#${selected.id} [${selected.type}] ${JSON.stringify(selected.props)}`
        : "";
    };

    async function search() {
      resize();
      const q = el.querySelector("#gq").value.trim();
      if (!q) return;
      const data = await fetch(`/api/graph/search?q=${encodeURIComponent(q)}`)
        .then((r) => r.json()).catch(() => ({ nodes: [], edges: [] }));
      load(data);
      el.querySelector("#gdetail").textContent =
        data.nodes.length ? `${data.nodes.length} nodes, ${data.edges.length} edges` : "no matches";
    }
    el.querySelector("#gsearch").onclick = search;
    el.querySelector("#gq").onkeydown = (e) => { if (e.key === "Enter") search(); };

    return () => { if (raf) cancelAnimationFrame(raf); };
  },
};
```

`static/js/registry.js` final form:

```js
// Adding a card = create js/cards/<name>.js + add one line here.
import status from "./cards/status.js";
import log from "./cards/log.js";
import camera from "./cards/camera.js";
import ears from "./cards/ears.js";
import voice from "./cards/voice.js";
import move from "./cards/move.js";
import poses from "./cards/poses.js";
import servos from "./cards/servos.js";
import sensors from "./cards/sensors.js";
import graph from "./cards/graph.js";

export const cards = [status, camera, move, ears, voice, poses, servos, sensors, graph, log];
```

- [ ] **Step 2: Verify**

Run: `python -m pytest bridge/tests/webapp -q` → all pass (static integrity resolves all ten cards).

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static
git commit -m "feat(web): poses/emotes, servo test, sensors, memory graph cards"
```

---

### Task 11: systemd caps, docs, dev smoke script, final verification

**Files:**
- Modify: `bridge/systemd/milo-bridge.service`
- Create: `bridge/tools/webdev.py` (off-Pi dev server with fakes)
- Create: `docs/WEB-DASHBOARD.md`
- Modify: `README.md` (one line in the package table/overview pointing at the new doc)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: systemd unit**

In `bridge/systemd/milo-bridge.service`, inside `[Service]`, add:

```ini
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
```

- [ ] **Step 2: Off-Pi dev server**

`bridge/tools/webdev.py`:

```python
"""Run the web dashboard off-Pi with fake drivers: python bridge/tools/webdev.py
Then open http://localhost:8080 — used for frontend development and smoke tests."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from webapp.fakes import make_deps  # bridge/tests/webapp/fakes.py

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.logbuf import RingBufferLogHandler
from milo_bridge.webapp.media_hub import MediaHub
from milo_bridge.webapp.server import start_web


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log_buffer = RingBufferLogHandler()
    logging.getLogger().addHandler(log_buffer)
    deps = make_deps(broker=ControlBroker(), log_buffer=log_buffer)
    deps.media_hub = MediaHub(camera=deps.camera, audio=deps.audio)
    deps.config.web_port = 8080
    await start_web(deps)
    logging.info("dev dashboard at http://localhost:8080")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
```

(If the `webapp.fakes` import path is awkward, adjust the `sys.path` insert to point at `bridge/tests` and import `webapp.fakes` — or duplicate the 60-line fakes inline; keep the tool zero-dependency. Note: FakeCamera/FakeAudio yield a finite frame list; for a pleasant dev loop, pass `FakeCamera(frames=itertools-cycle-style generator)` — simplest is to construct `make_deps()` then wrap: give FakeCamera a long repeated list, e.g. `FakeCamera(frames=(b"...",) * 10000)`.)

- [ ] **Step 3: docs/WEB-DASHBOARD.md**

Write the doc with these sections (full prose, not stubs):

1. **What it is** — one-paragraph overview + screenshot placeholder line (`_screenshot to be added after first run_` is acceptable here only).
2. **Reaching it: `milo.local`** — exact commands: `sudo raspi-config nonint do_hostname milo`, confirm `avahi-daemon` active, reboot; then `http://milo.local` (port 80 via systemd capabilities; fallback `http://milo.local:8080`).
3. **Feature tour** — one short paragraph per card (10 cards), including: control model (observe free / Take Control for motion / STOP always), audio listen + push-to-talk, TTS needs `sudo apt install espeak-ng`, snapshot, layout drag/resize/persist, theme toggle.
4. **Control & safety** — broker rules, heartbeat expiry, gait staleness (0.5 s), brain-command dropping while web controls.
5. **Writing a new card** — worked example `hello.js` (full code: id/title/w/h/mount returning cleanup) + the one registry line + optional server route via `webapp/api/` register pattern; mention the static-integrity test enforces file existence.
6. **Audio rates** — the `SAMPLE_RATE` constants in `ears.js`/`voice.js` and how to change them if the hardware differs.
7. **Development off-Pi** — `python bridge/tools/webdev.py`, what works with fakes (everything except real media), and the manual smoke checklist:
   - page loads, theme toggles, layout survives reload
   - Take Control → move/voice/servo/pose cards unlock; second tab is denied
   - STOP works from a non-controlling tab
   - camera card streams fake frames; log card shows live lines
   - graph search returns seeded nodes (seed via `/api/graph` upsert ops)

- [ ] **Step 4: README pointer**

In the repo `README.md` package overview, add a line to the bridge section: `bridge/` now also serves the **web dashboard** at `http://milo.local` — see `docs/WEB-DASHBOARD.md`.

- [ ] **Step 5: Full verification**

Run: `python -m pytest bridge/tests common/tests brain/tests training/tests -q`
Expected: entire repo suite passes.
Run: `python bridge/tools/webdev.py` and walk the smoke checklist in a browser (both themes).

- [ ] **Step 6: Commit**

```bash
git add bridge docs README.md
git commit -m "feat(web): systemd port-80 caps, off-Pi dev server, WEB-DASHBOARD docs"
```

---

## Self-review notes

- **Spec coverage:** hosting/`milo.local`/port fallback (T1, T11); ControlBroker + brain gate + STOP exemption (T2, T3, T4); MediaHub + streams refactor + sleep perk-up preserved via `on_audio_level` (T3); WS protocol incl. `hello` id, heartbeat, telemetry, owner broadcast (T5); MJPEG + TTS + intercom (T5, T6); graph search + passthrough + poses/faces + logs (T7); frontend shell, theming, drag/resize/persist, add/reset, control locking (T8); all ten cards (T8-T10); docs + systemd + off-Pi dev (T11). Spec's "server task restarts once after 5 s" simplified to log-and-stay-down in `start_web` — restore by wrapping the body in one retry if desired during T1 (acceptable deviation noted here deliberately: the retry adds little on a `Restart=always` service).
- **Type consistency:** `WebDeps` field names used identically in fakes/telemetry/handlers; `MotionService` method names match ws dispatch table; `Fanout.subscribe/unsubscribe` used by streams, media endpoint, and audio pump; card contract `{id,title,w,h,needsControl,mount}` consistent across grid and all ten cards; `bus` API (`send/sendBytes/on/onBinary/clientId/controlled`) consistent.
- **Known risks called out to the implementer:** existing `bridge/tests` for session/streams will need constructor updates (T3 step 4 says so); `GraphStore` row-unpacking helper name must be copied from `get_node` (T7 notes it); audio sample-rate constants are hardware-dependent (docs task).



