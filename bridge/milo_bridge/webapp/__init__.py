"""Milo web dashboard: aiohttp app factory."""
from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .api import register_routes
from .deps import WebDeps

STATIC_DIR = Path(__file__).parent / "static"


async def _index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


def create_app(deps: WebDeps) -> web.Application:
    app = web.Application(client_max_size=2 * 1024 * 1024)
    app["deps"] = deps
    app["ws_clients"] = set()
    register_routes(app)
    app.router.add_get("/", _index)
    app.router.add_static("/static", STATIC_DIR)
    return app
