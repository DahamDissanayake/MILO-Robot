"""One WebSocket per browser tab: JSON dispatch + binary audio framing."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid

from aiohttp import WSMsgType, web

from .motion import MotionService
from .telemetry import collect_telemetry, imu_snapshot

log = logging.getLogger(__name__)

TELEMETRY_S = 2.0
IMU_S = 0.1
EXPIRY_S = 1.0
AUDIO_OUT = 0x01
AUDIO_IN = 0x02


async def _send_safe(ws: web.WebSocketResponse, payload: dict) -> None:
    try:
        await ws.send_json(payload)
    except (ConnectionResetError, RuntimeError):
        pass  # socket died between the closed-check and the write


def broadcast_json(app: web.Application, payload: dict) -> None:
    for ws in list(app["ws_clients"]):
        if not ws.closed:
            asyncio.ensure_future(_send_safe(ws, payload))


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
    if t == "mode":
        res = await motion.mode(client_id, data.get("name", ""))
        if "error" in res:
            await ws.send_json({"t": "err", "for": "mode", "error": res["error"]})
        else:
            _broadcast_mode(app, res["mode"])
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
        "servo_batch": lambda: motion.servo_batch(client_id, data.get("angles", {})),
        "reset": lambda: motion.reset(client_id),
        "standby": lambda: motion.standby(client_id),
        "restart": lambda: motion.restart(client_id),
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
            asyncio.ensure_future(_send_safe(ws, {"t": "control", "owner": owner, "you": you}))


def _broadcast_mode(app: web.Application, name: str) -> None:
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            asyncio.ensure_future(_send_safe(ws, {"t": "mode", "name": name}))


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
    pump = None
    try:
        await ws.send_json({"t": "hello", "id": client_id})
        pump = asyncio.ensure_future(_audio_out_pump(app, ws))
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                await _handle_text(app, ws, client_id, data)
            elif msg.type == WSMsgType.BINARY and msg.data[:1] == bytes([AUDIO_IN]):
                if deps.audio is not None and deps.broker and deps.broker.is_web_controller(client_id):
                    deps.audio.play_pcm(msg.data[1:])
    finally:
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
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


async def _imu_loop(app: web.Application) -> None:
    while True:
        await asyncio.sleep(IMU_S)
        deps = app["deps"]
        if deps.imu is not None and app["ws_clients"]:
            snap = imu_snapshot(deps)
            if snap is not None:
                broadcast_json(app, {"t": "imu", **snap})


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
        asyncio.ensure_future(_imu_loop(app)),
        asyncio.ensure_future(_expiry_loop(app)),
    ]


async def _on_cleanup(app: web.Application) -> None:
    app["motion"].stop_watchdog()
    tasks = app.get("bg_tasks", [])
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def register_ws(app: web.Application) -> None:
    app["ws_state"] = {}
    app["motion"] = MotionService(app["deps"])
    app.router.add_get("/ws", websocket_handler)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
