"""Milo web dashboard: aiohttp app factory."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from .api import register_routes
from .deps import WebDeps

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


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
    app = web.Application(client_max_size=2 * 1024 * 1024, middlewares=[_json_error_middleware])
    app["deps"] = deps
    app["ws_clients"] = set()
    register_routes(app)
    from .ws import register_ws
    register_ws(app)
    app.router.add_get("/", _index)
    app.router.add_static("/static", STATIC_DIR)
    return app
