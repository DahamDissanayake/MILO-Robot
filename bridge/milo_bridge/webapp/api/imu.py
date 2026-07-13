"""IMU tare/zero action — recenter the sensor's current orientation to flat."""
from __future__ import annotations

from aiohttp import web


async def post_zero(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    if deps.imu is None:
        return web.json_response({"error": "imu unavailable"})
    try:
        deps.imu.zero()
    except Exception as exc:
        return web.json_response({"error": f"{type(exc).__name__}: {exc}"})
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_post("/api/imu/zero", post_zero)
