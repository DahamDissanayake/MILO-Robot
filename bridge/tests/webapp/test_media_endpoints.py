import asyncio

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.media_hub import MediaHub
from .client_helpers import authed_client
from .fakes import FakeCamera, make_deps


_client = authed_client


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
        for _ in range(10):
            if len(deps.media_hub.video._subs) == 0:
                break
            await asyncio.sleep(0.1)
        assert len(deps.media_hub.video._subs) == 0


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


class _FakeProc:
    def __init__(self, communicate_result=None, returncode=0, hang=False):
        self._result = communicate_result or (b"", b"")
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang and not self.killed:
            await asyncio.sleep(999)
        return self._result

    def kill(self):
        self.killed = True


async def test_synth_pcm_strips_wav_header(monkeypatch):
    import milo_bridge.webapp.api.speak as speak_mod

    fake_proc = _FakeProc(communicate_result=(b"H" * 44 + b"PCMDATA", b""), returncode=0)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    assert await speak_mod.synth_pcm("x") == b"PCMDATA"


async def test_synth_pcm_nonzero_rc_returns_none(monkeypatch):
    import milo_bridge.webapp.api.speak as speak_mod

    fake_proc = _FakeProc(communicate_result=(b"H" * 44 + b"PCMDATA", b""), returncode=1)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    assert await speak_mod.synth_pcm("x") is None


async def test_synth_pcm_timeout_kills_process(monkeypatch):
    import milo_bridge.webapp.api.speak as speak_mod

    fake_proc = _FakeProc(hang=True)

    async def fake_create(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    result = await speak_mod.synth_pcm("x", timeout_s=0.01)
    assert result is None
    assert fake_proc.killed is True
