"""Brain card data: connected brain, known/paired brains, pairing status.

Never returns the pairing PIN -- that only ever appears on the robot's own
OLED (see net/pairing.py); the webapp can turn pairing mode on/off (see
ws.py's enter_pairing_mode handler) but never observes the code itself.
"""
from __future__ import annotations

from aiohttp import web


async def get_brains(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    rs = deps.robot_server
    if rs is None:
        return web.json_response({"connected": None, "paired": [], "pairing": False, "ip": "", "port": 0})
    connected = rs.connected_brain
    return web.json_response({
        "connected": {"id": connected.id, "name": connected.name} if connected else None,
        "paired": rs.paired_brains(),
        "pairing": rs.advertiser.pairing,
        # For manually connecting when mDNS discovery doesn't reach the
        # brain machine (some routers don't forward multicast between
        # WiFi clients) -- always included, not gated on pairing, since
        # an already-paired brain reconnecting manually needs it too.
        "ip": rs.advertiser.advertised_ip,
        "port": rs.port,
    })


def register(app: web.Application) -> None:
    app.router.add_get("/api/brains", get_brains)
