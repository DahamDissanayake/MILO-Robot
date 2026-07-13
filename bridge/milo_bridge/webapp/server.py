"""Bind and serve the dashboard; port 80 with one 8080 fallback."""
from __future__ import annotations

import logging
import socket

from aiohttp import web

from . import create_app

log = logging.getLogger(__name__)
FALLBACK_PORT = 8080


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int, port_free=_port_free) -> int:
    if port_free(preferred):
        return preferred
    log.warning("port %d unavailable, falling back to %d", preferred, FALLBACK_PORT)
    return FALLBACK_PORT


async def start_web(deps) -> None:
    """Run the dashboard forever. Exceptions are logged, never propagated."""
    try:
        app = create_app(deps)
        port = pick_port(deps.config.web_port)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log.info("web dashboard on http://0.0.0.0:%d (milo.local)", port)
    except Exception:
        log.exception("web dashboard failed to start")
