"""Bearer-token gate for the movement/face/speech/IMU MCP server, reusing
the existing pairing trust store (common/milo_common/auth.py PairedStore)
instead of standing up a second auth system.

A paired brain authenticates with the token it already shares with this
robot from the WS pairing handshake. A human MCP client (Claude Desktop/
Code) has no such existing relationship, so ``mint_mcp_token`` provisions
one -- a plain random secret added to the same store under a chosen name,
printed once for the operator to paste into their MCP client config.
"""
from __future__ import annotations

import hmac
import secrets

from milo_common.auth import TOKEN_BYTES, PairedStore
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def mint_mcp_token(store: PairedStore, peer_id: str) -> str:
    if store.is_paired(peer_id):
        raise ValueError(f"{peer_id!r} is already paired; choose a different name")
    token = secrets.token_bytes(TOKEN_BYTES)
    store.add(peer_id, token, name=peer_id)
    return token.hex()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, store: PairedStore):
        super().__init__(app)
        self._store = store

    async def dispatch(self, request: Request, call_next):
        peer_id = request.headers.get("X-Milo-Peer", "")
        auth_header = request.headers.get("Authorization", "")
        expected = self._store.token_for(peer_id)
        if expected is None or not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            provided = bytes.fromhex(auth_header[len("Bearer "):])
        except ValueError:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        if not hmac.compare_digest(provided, expected):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
