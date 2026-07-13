from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app
from .fakes import TEST_PASSWORD, TEST_USERNAME, make_deps


async def _raw_client(deps):
    """A client that has NOT logged in — for testing the gate itself."""
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_unauthenticated_root_redirects_to_login():
    client = await _raw_client(make_deps())
    try:
        resp = await client.get("/", allow_redirects=False)
        assert resp.status == 303
        assert resp.headers["Location"] == "/login"
    finally:
        await client.close()


async def test_unauthenticated_api_returns_401_json():
    client = await _raw_client(make_deps())
    try:
        resp = await client.get("/api/status")
        assert resp.status == 401
        data = await resp.json()
        assert data["error"] == "unauthorized"
    finally:
        await client.close()


async def test_unauthenticated_ws_handshake_rejected():
    client = await _raw_client(make_deps())
    try:
        import aiohttp
        try:
            await client.ws_connect("/ws")
            raised = False
        except aiohttp.WSServerHandshakeError:
            raised = True
        assert raised
    finally:
        await client.close()


async def test_login_page_and_static_reachable_without_auth():
    client = await _raw_client(make_deps())
    try:
        resp = await client.get("/login")
        assert resp.status == 200
        resp2 = await client.get("/static/js/bus.js")
        assert resp2.status == 200
    finally:
        await client.close()


async def test_login_wrong_password_fails():
    client = await _raw_client(make_deps())
    try:
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": "wrong"})
        assert resp.status == 200
        data = await resp.json()
        assert data["error"] == "invalid credentials"
        assert "milo_session" not in client.session.cookie_jar.filter_cookies("http://127.0.0.1")
    finally:
        await client.close()


async def test_login_correct_password_sets_cookie_and_unlocks_api():
    client = await _raw_client(make_deps())
    try:
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert resp.status == 200
        data = await resp.json()
        assert data == {"ok": True}
        # cookie now present; subsequent requests on the same client succeed
        resp2 = await client.get("/api/status")
        assert resp2.status == 200
    finally:
        await client.close()


async def test_logout_revokes_session():
    client = await _raw_client(make_deps())
    try:
        await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert (await client.get("/api/status")).status == 200
        await client.post("/api/logout")
        assert (await client.get("/api/status")).status == 401
    finally:
        await client.close()


async def test_login_throttled_after_five_failures():
    client = await _raw_client(make_deps())
    try:
        for _ in range(5):
            await client.post("/api/login", json={"username": TEST_USERNAME, "password": "wrong"})
        resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
        assert resp.status == 429
    finally:
        await client.close()
