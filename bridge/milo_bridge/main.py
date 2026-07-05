"""Service entrypoint: boot to rest pose with the idle face, then hand control
to the connectivity layer (discovery -> session -> streams; see net/).

Phase B scope: drivers up, rest pose, idle face. The brain link attaches here
in Phase C via net.session.SessionManager.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import BridgeConfig
from .drivers.display import FaceDisplay
from .drivers.servos import ServoDriver
from .poses import PoseRunner

log = logging.getLogger("milo-bridge")

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "faces"


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = BridgeConfig.load()
    log.info("milo-bridge starting as %s (%s)", cfg.robot_name, cfg.robot_id)

    servos = ServoDriver.from_hardware(trims=cfg.servo_trims, stagger_ms=cfg.servo_stagger_ms)
    display = FaceDisplay.from_hardware(ASSETS_DIR)
    runner = PoseRunner(servos, display)

    await runner.run("rest")
    display.start_idle()
    log.info("resting with idle face; waiting for a brain")

    from .net.session import SessionManager  # imported late: optional until Phase C wiring

    manager = SessionManager(cfg, servos=servos, display=display, runner=runner)
    await manager.run_forever()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
