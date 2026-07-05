"""Outbound media pumps: camera frames and mic audio onto the brain socket."""

from __future__ import annotations

import time

from milo_common import protocol
from milo_common.protocol import MiloSocket


async def pump_video(sock: MiloSocket, camera) -> None:
    """Send MJPEG frames until cancelled; the camera paces itself to its fps."""
    async for frame in camera.frames():
        await sock.send(protocol.T_VIDEO, payload=frame, ts=time.time())


async def pump_audio(sock: MiloSocket, audio, on_level=None) -> None:
    """Send 20 ms stereo PCM frames until cancelled.

    ``on_level(rms)`` also feeds the sleep controller's loud-sound perk-up.
    """
    from ..drivers.audio import rms

    async for chunk in audio.capture_frames():
        if on_level is not None:
            on_level(rms(chunk))
        await sock.send(protocol.T_AUDIO, payload=chunk, ts=time.time())
