"""Text-to-speech through Milo's speaker via espeak-ng."""
from __future__ import annotations

import asyncio
import logging
import shutil

from aiohttp import web

log = logging.getLogger(__name__)
WAV_HEADER = 44


def tts_available() -> bool:
    return shutil.which("espeak-ng") is not None


async def synth_pcm(text: str, timeout_s: float = 10.0) -> bytes | None:
    proc = await asyncio.create_subprocess_exec(
        "espeak-ng", "--stdout", "-a", "120", "--", text,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        log.warning("espeak-ng timed out; killed")
        return None
    if proc.returncode != 0 or len(out) <= WAV_HEADER:
        log.warning("espeak-ng failed (rc=%s, %d bytes)", proc.returncode, len(out))
        return None
    return out[WAV_HEADER:]


async def post_speak(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    body = await request.json()
    client_id = body.get("client", "")
    if deps.broker is None or not deps.broker.is_web_controller(client_id):
        return web.json_response({"error": "not-controlling"})
    if deps.audio is None:
        return web.json_response({"error": "audio unavailable"})
    if not tts_available():
        return web.json_response({"error": "tts-unavailable"})
    text = str(body.get("text", ""))[:500]
    if not text.strip():
        return web.json_response({"error": "empty text"})
    try:
        pcm = await synth_pcm(text)
    except Exception:
        log.exception("tts failed")
        pcm = None
    if pcm is None:
        return web.json_response({"error": "tts-failed"})
    deps.audio.play_pcm(pcm)
    return web.json_response({"ok": True})


def register(app: web.Application) -> None:
    app.router.add_post("/api/speak", post_speak)
