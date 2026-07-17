import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from milo_common.auth import PairedStore
from milo_bridge.mcp.auth import BearerAuthMiddleware, mint_mcp_token


@pytest.fixture()
def store(tmp_path):
    return PairedStore(tmp_path / "paired.json")


def test_mint_mcp_token_persists_and_returns_hex(store):
    token_hex = mint_mcp_token(store, "laptop-1")
    assert len(token_hex) == 64  # 32 bytes hex-encoded
    assert store.token_for("laptop-1") == bytes.fromhex(token_hex)


def _app_with_auth(store):
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/ping", ok)])
    return BearerAuthMiddleware(app, store)


def test_missing_headers_are_rejected(store):
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping")
    assert resp.status_code == 401


def test_unknown_peer_is_rejected(store):
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": "Bearer " + "00" * 32, "X-Milo-Peer": "nobody"})
    assert resp.status_code == 401


def test_wrong_token_is_rejected(store):
    mint_mcp_token(store, "laptop-1")
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": "Bearer " + "11" * 32, "X-Milo-Peer": "laptop-1"})
    assert resp.status_code == 401


def test_correct_token_is_accepted(store):
    token_hex = mint_mcp_token(store, "laptop-1")
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": f"Bearer {token_hex}", "X-Milo-Peer": "laptop-1"})
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_malformed_bearer_token_is_rejected(store):
    mint_mcp_token(store, "laptop-1")
    client = TestClient(_app_with_auth(store))
    resp = client.get("/ping", headers={"Authorization": "Bearer not-hex", "X-Milo-Peer": "laptop-1"})
    assert resp.status_code == 401
