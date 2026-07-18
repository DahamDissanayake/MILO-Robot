import asyncio
import json

import aiohttp

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.media_hub import MediaHub
from .client_helpers import authed_client
from .fakes import FakeAudio, FakeRobotServer, make_deps


async def _ws(deps):
    client = await authed_client(deps)
    ws = await client.ws_connect("/ws")
    return client, ws


async def _recv_json_until(ws, t, tries=10, timeout=2.0):
    for _ in range(tries):
        msg = await asyncio.wait_for(ws.receive(), timeout)
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


async def test_audio_out_binary_frames():
    deps = make_deps(broker=ControlBroker(), media_hub=MediaHub(audio=FakeAudio(frames=(b"\x00\x01" * 160,) * 50)))
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "audio", "on": True})
        msg = None
        for _ in range(20):
            msg = await asyncio.wait_for(ws.receive(), 2.0)
            if msg.type == aiohttp.WSMsgType.BINARY:
                break
        else:
            raise AssertionError("no binary audio-out frame received")
        assert msg.data[0] == 0x01
        assert len(msg.data) > 1
    finally:
        await client.close()


async def test_telemetry_pushed():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        # Per-receive timeout bumped from 2.0s to 2.5s: the telemetry loop's
        # clock starts at server on_startup (before this ws connects and
        # receives "hello"), so a 2.0s deadline measured from "hello" races
        # the 2.0s telemetry tick and loses by a few ms almost every time
        # (confirmed deterministically, not flaky-by-chance). 2.5s gives
        # slack without shortening the implementation's TELEMETRY_S.
        data = await _recv_json_until(ws, "telemetry", tries=30, timeout=2.5)
        assert data["gait_backend"] == "cpg"
        assert data["owner"] == "none"
    finally:
        await client.close()


async def test_imu_pushed():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        data = await _recv_json_until(ws, "imu", tries=30, timeout=1.0)
        assert data["pitch"] == 1.0
        assert data["roll"] == -2.0
        assert data["yaw"] == 15.0
        assert data["gyro"] == [0.1, 0.2, 0.5]
        assert data["accel"] == [0.01, -0.02, 0.98]
    finally:
        await client.close()


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


async def test_mode_broadcasts_to_all_clients():
    deps = make_deps(broker=ControlBroker())
    client, ws1 = await _ws(deps)
    try:
        ws2 = await client.ws_connect("/ws")
        await ws1.send_json({"t": "control", "take": True})
        await _recv_json_until(ws1, "control")
        await ws1.send_json({"t": "mode", "name": "balanced"})
        data1 = await _recv_json_until(ws1, "mode")
        data2 = await _recv_json_until(ws2, "mode")
        assert data1 == {"t": "mode", "name": "balanced"}
        assert data2 == {"t": "mode", "name": "balanced"}
        assert deps.gait.mode == "balanced"
    finally:
        await client.close()


async def test_mode_denied_without_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "mode", "name": "balanced"})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()


async def test_reset_and_standby_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "reset"})
        await _recv_json_until(ws, "ack")
        assert deps.gait.reset_called is True
        await ws.send_json({"t": "standby"})
        await _recv_json_until(ws, "ack")
        assert deps.gait.standby_called is True
    finally:
        await client.close()


async def test_restart_dispatch_requires_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "restart"})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()


async def test_relax_and_hold_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "relax"})
        await _recv_json_until(ws, "ack")
        assert deps.servos.relaxed is True
        await ws.send_json({"t": "hold"})
        await _recv_json_until(ws, "ack")
        assert deps.servos.held is True
    finally:
        await client.close()


async def test_turn_and_look_pose_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "turn", "dir": "left"})
        await _recv_json_until(ws, "ack")
        assert deps.runner.ran == ["turn_left"]
        await ws.send_json({"t": "stop"})
        await _recv_json_until(ws, "ack")
        assert deps.runner.aborted is True
        await ws.send_json({"t": "pose", "name": "look_up"})
        await _recv_json_until(ws, "ack")
        assert deps.runner.ran == ["turn_left", "look_up"]
        await ws.send_json({"t": "standby"})
        await _recv_json_until(ws, "ack")
        assert deps.gait.standby_called is True
    finally:
        await client.close()


async def test_manual_broadcasts_to_all_clients():
    deps = make_deps(broker=ControlBroker())
    client, ws1 = await _ws(deps)
    try:
        ws2 = await client.ws_connect("/ws")
        await ws1.send_json({"t": "control", "take": True})
        await _recv_json_until(ws1, "control")
        await ws1.send_json({"t": "manual", "on": True})
        data1 = await _recv_json_until(ws1, "manual")
        data2 = await _recv_json_until(ws2, "manual")
        assert data1 == {"t": "manual", "on": True}
        assert data2 == {"t": "manual", "on": True}
        assert deps.gait.manual_on is True
    finally:
        await client.close()


async def test_manual_denied_without_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "manual", "on": True})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()


async def test_enter_pairing_mode_broadcasts_to_all_clients():
    rs = FakeRobotServer()
    deps = make_deps(broker=ControlBroker(), robot_server=rs)
    client, ws1 = await _ws(deps)
    try:
        ws2 = await client.ws_connect("/ws")
        await ws1.send_json({"t": "control", "take": True})
        await _recv_json_until(ws1, "control")
        await ws1.send_json({"t": "enter_pairing_mode", "on": True})
        data1 = await _recv_json_until(ws1, "pairing")
        data2 = await _recv_json_until(ws2, "pairing")
        assert data1 == {"t": "pairing", "on": True}
        assert data2 == {"t": "pairing", "on": True}
        assert rs.pairing.entered == 1
        assert rs.advertiser.pairing is True
        # No message anywhere in this exchange ever carries the raw PIN.
        assert "999999" not in str(data1) and "code" not in data1 and "pin" not in data1
    finally:
        await client.close()


async def test_exit_pairing_mode_broadcasts_off():
    rs = FakeRobotServer()
    deps = make_deps(broker=ControlBroker(), robot_server=rs)
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "enter_pairing_mode", "on": True})
        await _recv_json_until(ws, "pairing")
        await ws.send_json({"t": "enter_pairing_mode", "on": False})
        data = await _recv_json_until(ws, "pairing")
        assert data == {"t": "pairing", "on": False}
        assert rs.pairing.exited == 1
        assert rs.advertiser.pairing is False
    finally:
        await client.close()


async def test_enter_pairing_mode_denied_without_control():
    deps = make_deps(broker=ControlBroker(), robot_server=FakeRobotServer())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "enter_pairing_mode", "on": True})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()


async def test_camera_resolution_accepted_without_control():
    """No control gate on camera_resolution -- observation is never
    brokered in this codebase, only motion is."""
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "camera_resolution", "value": "hd"})
        await _recv_json_until(ws, "ack")
        assert deps.camera.resolution == "hd"
    finally:
        await client.close()


async def test_camera_resolution_rejects_unknown_value():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "camera_resolution", "value": "4k"})
        data = await _recv_json_until(ws, "err")
        assert data["for"] == "camera_resolution"
        assert deps.camera.resolution == "sd"
    finally:
        await client.close()


async def test_camera_resolution_errors_when_camera_unavailable():
    deps = make_deps(broker=ControlBroker(), camera=None)
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "camera_resolution", "value": "hd"})
        data = await _recv_json_until(ws, "err")
        assert data["for"] == "camera_resolution"
        assert data["error"] == "camera unavailable"
    finally:
        await client.close()
