"""MJPEG camera stream — a hub subscription per connected browser."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

log = logging.getLogger(__name__)
BOUNDARY = "milo-frame"


async def camera_stream(request: web.Request) -> web.StreamResponse:
    deps = request.app["deps"]
    hub = deps.media_hub
    if hub is None or hub.video is None:
        return web.json_response({"error": "camera unavailable"}, status=404)
    resp = web.StreamResponse(headers={
        "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        "Cache-Control": "no-store",
    })
    await resp.prepare(request)
    q = hub.video.subscribe()
    try:
        while True:
            frame = await q.get()
            await resp.write(
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n".encode() + frame + b"\r\n"
            )
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.video.unsubscribe(q)
    return resp


def register(app: web.Application) -> None:
    app.router.add_get("/stream/camera", camera_stream)
