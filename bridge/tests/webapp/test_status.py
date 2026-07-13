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
        assert data["hardware"] == {"camera": False, "audio": False, "imu": False, "display": False}
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
