from .client_helpers import authed_client
from .fakes import make_deps


async def test_imu_zero_calls_driver():
    deps = make_deps()
    client = await authed_client(deps)
    try:
        resp = await client.post("/api/imu/zero")
        assert resp.status == 200
        assert await resp.json() == {"ok": True}
        assert deps.imu.zeroed is True
    finally:
        await client.close()


async def test_imu_zero_without_imu_hardware():
    deps = make_deps(imu=None)
    client = await authed_client(deps)
    try:
        resp = await client.post("/api/imu/zero")
        assert await resp.json() == {"error": "imu unavailable"}
    finally:
        await client.close()
