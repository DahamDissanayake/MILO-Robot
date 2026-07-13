from .client_helpers import authed_client
from .fakes import make_deps


_client = authed_client


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


async def test_status_reports_real_imu_state_as_json_serializable_dict():
    """Regression test: telemetry.py used to call deps.imu.read(), a method
    that doesn't exist on the real Mpu6050 driver (which only has
    read_raw(), calibrate_gyro(), and update()) — every call silently threw
    AttributeError inside a bare `except Exception`, so the IMU tiles in
    the Sensors panel showed permanent n/a despite the hardware being
    physically present and working. FakeImu mirrors the real driver's
    actual interface (update() returning an ImuState dataclass), so this
    only passes once collect_telemetry calls the right method and converts
    the dataclass result to a plain JSON-serializable dict."""
    deps = make_deps()
    client = await _client(deps)
    try:
        data = await (await client.get("/api/status")).json()
        assert data["imu"] == {
            "pitch": 1.0, "roll": -2.0,
            "gyro": [0.1, 0.2, 0.5], "accel": [0.01, -0.02, 0.98],
        }
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


async def test_api_404_returns_json_error():
    client = await _client(make_deps())
    try:
        resp = await client.get("/api/nonexistent")
        assert resp.status == 404
        data = await resp.json()
        assert "error" in data
    finally:
        await client.close()


async def test_static_assets_always_revalidate():
    """Regression test: aiohttp's default static file serving sets no
    Cache-Control header, so browsers apply their own heuristic caching
    and can go on serving a fully stale JS module (e.g. an old registry.js
    that still imports a since-deleted panel file) across a redeploy —
    silently blanking the page, since one failed ES module import aborts
    the whole module graph with no visible error. Every /static/ response
    must force the browser to revalidate with the server on each load."""
    client = await _client(make_deps())
    try:
        resp = await client.get("/static/js/main.js")
        assert resp.status == 200
        assert "no-cache" in resp.headers.get("Cache-Control", "")
    finally:
        await client.close()


async def test_html_shell_always_revalidates():
    """Same staleness risk as static assets applies to the HTML shell
    itself (/ and /login) — both are FileResponse-served and must always
    reflect the current deploy, not a browser-cached prior version."""
    client = await _client(make_deps())
    try:
        resp = await client.get("/")
        assert resp.status == 200
        assert "no-cache" in resp.headers.get("Cache-Control", "")
    finally:
        await client.close()
