"""Shared test helper: an aiohttp TestClient that's already logged in.

Every route in the real app is gated by the auth middleware, so every test
that exercises an HTTP/WS route needs a client that has already completed
the login handshake. aiohttp's TestClient keeps a cookie jar for its
lifetime, so logging in once here makes every subsequent request/ws_connect
on the same client carry the session cookie automatically.
"""

from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from milo_bridge.webapp import create_app

from .fakes import TEST_PASSWORD, TEST_USERNAME


async def authed_client(deps) -> TestClient:
    app = create_app(deps)
    client = TestClient(TestServer(app))
    await client.start_server()
    resp = await client.post("/api/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD})
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body == {"ok": True}, body
    return client
