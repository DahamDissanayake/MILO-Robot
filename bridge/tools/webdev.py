"""Run the web dashboard off-Pi with fake drivers: python bridge/tools/webdev.py
Then open http://localhost:8080 — used for frontend development and smoke tests.

Zero extra dependencies: this reuses the same fakes the test suite uses
(bridge/tests/webapp/fakes.py) rather than duplicating them.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parents[1]
# bridge/tests is not an installed package, so put it on sys.path directly
# (ahead of anything else) to import the top-level `webapp` test-helpers
# package there. This is distinct from the real `milo_bridge.webapp`
# package imported below — the names don't collide because one is always
# imported as `webapp.fakes` (top-level) and the other as
# `milo_bridge.webapp....` (dotted, under milo_bridge).
sys.path.insert(0, str(BRIDGE_DIR / "tests"))

from webapp.fakes import FakeCamera, make_deps  # bridge/tests/webapp/fakes.py

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.logbuf import RingBufferLogHandler
from milo_bridge.webapp.media_hub import MediaHub
from milo_bridge.webapp.server import start_web

# A short, looping frame list so the camera card has something to stream
# for more than an instant — real hardware yields forever, the test fake
# does not, so we hand it a long repeated sequence for a pleasant dev loop.
_DEV_FRAME = b"\xff\xd8\xff\xdb" + b"\x00" * 200 + b"\xff\xd9"  # tiny fake JPEG-ish blob
_DEV_FRAMES = (_DEV_FRAME,) * 10000


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log_buffer = RingBufferLogHandler()
    logging.getLogger().addHandler(log_buffer)

    deps = make_deps(broker=ControlBroker(), log_buffer=log_buffer, camera=FakeCamera(frames=_DEV_FRAMES))
    deps.media_hub = MediaHub(camera=deps.camera, audio=deps.audio)
    deps.config.web_port = 8080

    await start_web(deps)
    logging.info("dev dashboard at http://localhost:8080")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
