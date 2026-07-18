"""System-level actions: crash log visibility, full restart, shutdown.

Restart/shutdown are deferred by a short delay after the response is sent
(REBOOT_DELAY_S), so the HTTP response actually reaches the browser before
the Pi goes down -- otherwise the client just sees a dropped connection
with no confirmation. Matches the same delay-then-act idiom motion.py's
existing "Restart Bridge (I2C reset)" button already uses for its own
os._exit(0) (RESTART_DELAY_S there) -- this is a different, complementary
action (a full Pi reboot/poweroff via systemctl, not a service-only exit).
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

log = logging.getLogger(__name__)

REBOOT_DELAY_S = 0.3


async def _run(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(*args)
    await proc.wait()


async def get_crashes(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    crash_log = deps.crash_log
    return web.json_response({"count": crash_log.count(), "entries": crash_log.entries(50)})


async def _deferred_system_command(*args: str) -> None:
    await asyncio.sleep(REBOOT_DELAY_S)
    try:
        await _run(*args)
    except Exception:
        log.exception("system command failed: %s", args)


async def post_restart(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    deps.crash_log.clear()
    asyncio.create_task(_deferred_system_command("sudo", "/usr/bin/systemctl", "reboot"))
    return web.json_response({"ok": True})


async def post_shutdown(request: web.Request) -> web.Response:
    asyncio.create_task(_deferred_system_command("sudo", "/usr/bin/systemctl", "poweroff"))
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_get("/api/crashes", get_crashes)
    app.router.add_post("/api/system/restart", post_restart)
    app.router.add_post("/api/system/shutdown", post_shutdown)
