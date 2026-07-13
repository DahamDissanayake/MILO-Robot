"""Outbound media pumps: hub-subscribed camera frames and mic audio."""

from __future__ import annotations

import time

from milo_common import protocol
from milo_common.protocol import MiloSocket


async def pump_video(sock: MiloSocket, fanout) -> None:
    """Send MJPEG frames from the hub until cancelled."""
    q = fanout.subscribe()
    try:
        while True:
            frame = await q.get()
            await sock.send(protocol.T_VIDEO, payload=frame, ts=time.time())
    finally:
        fanout.unsubscribe(q)


async def pump_audio(sock: MiloSocket, fanout) -> None:
    """Send 20 ms stereo PCM frames from the hub until cancelled."""
    q = fanout.subscribe()
    try:
        while True:
            chunk = await q.get()
            await sock.send(protocol.T_AUDIO, payload=chunk, ts=time.time())
    finally:
        fanout.unsubscribe(q)
