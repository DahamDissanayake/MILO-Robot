"""Outbound media pumps: hub-subscribed camera frames and mic audio."""

from __future__ import annotations

import time

from milo_common import protocol
from milo_common.protocol import MiloSocket


async def pump_video(sock: MiloSocket, fanout, should_stream=None) -> None:
    """Send MJPEG frames from the hub until cancelled. While should_stream()
    is False (a web pilot holds control -> the brain is suspended) the queue
    is still drained so it doesn't back up, but nothing is forwarded."""
    q = fanout.subscribe()
    try:
        while True:
            frame = await q.get()
            if should_stream is None or should_stream():
                await sock.send(protocol.T_VIDEO, payload=frame, ts=time.time())
    finally:
        fanout.unsubscribe(q)


async def pump_audio(sock: MiloSocket, fanout, should_stream=None) -> None:
    """Send 20 ms stereo PCM frames from the hub until cancelled; gated by
    should_stream() the same way pump_video is."""
    q = fanout.subscribe()
    try:
        while True:
            chunk = await q.get()
            if should_stream is None or should_stream():
                await sock.send(protocol.T_AUDIO, payload=chunk, ts=time.time())
    finally:
        fanout.unsubscribe(q)
