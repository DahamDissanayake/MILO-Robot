"""Milo web dashboard: aiohttp app factory."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .api import register_routes
from .api.auth import SESSION_COOKIE
from .deps import WebDeps
from .session_auth import LoginThrottle, SessionStore

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

_AUTH_ALLOWLIST_PATHS = {"/login", "/api/login"}
_JSON_401_PATHS_PREFIXES = ("/api/",)
_JSON_401_EXACT_PATHS = {"/ws", "/stream/camera"}


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def _login_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "login.html")


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    path = request.path
    if path in _AUTH_ALLOWLIST_PATHS or path.startswith("/static/"):
        return await handler(request)
    token = request.cookies.get(SESSION_COOKIE)
    sessions: SessionStore = request.app["sessions"]
    if token and sessions.is_valid(token):
        return await handler(request)
    if path.startswith(_JSON_401_PATHS_PREFIXES) or path in _JSON_401_EXACT_PATHS:
        return web.json_response({"error": "unauthorized"}, status=401)
    raise web.HTTPSeeOther(location="/login")


@web.middleware
async def _json_error_middleware(request: web.Request, handler):
    """Return JSON errors for /api/* requests instead of aiohttp's HTML pages."""
    if not request.path.startswith("/api/"):
        return await handler(request)
    try:
        return await handler(request)
    except web.HTTPException as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    except Exception:
        log.exception("unhandled error in %s %s", request.method, request.path)
        return web.json_response({"error": "internal error"}, status=500)


def create_app(deps: WebDeps) -> web.Application:
    app = web.Application(
        client_max_size=2 * 1024 * 1024,
        middlewares=[_auth_middleware, _json_error_middleware],
    )
    app["deps"] = deps
    app["ws_clients"] = set()
    app["sessions"] = SessionStore()
    app["login_throttle"] = LoginThrottle()
    register_routes(app)
    from .ws import register_ws
    register_ws(app)
    if deps.log_buffer is not None:
        from .ws import broadcast_json
        deps.log_buffer.on_line = lambda line: broadcast_json(app, {"t": "log", "line": line})
    app.router.add_get("/", _index)
    app.router.add_get("/login", _login_page)
    app.router.add_static("/static", STATIC_DIR)
    return app
