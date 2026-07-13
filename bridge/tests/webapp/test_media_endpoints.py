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
