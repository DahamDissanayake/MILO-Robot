"""milo-bridge service entrypoint.

Composition root: build drivers, gait engine, knowledge graph, and sleep
controller, boot to rest with the idle face, then hand control to the
SessionManager (discovery -> pairing/auth -> streams -> dispatch).

Optional hardware degrades gracefully — a missing camera or policy file logs
a warning instead of killing the service; the PCA9685 and OLED are required.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import BridgeConfig
from .drivers.audio import AudioIO
from .drivers.camera import CameraStreamer
from .drivers.display import FaceDisplay
from .drivers.imu import Mpu6050
from .drivers.servos import ServoDriver
from .gait.engine import GaitEngine
from .graph.api import GraphApi
from .graph.store import GraphStore
from .net.session import SessionManager
from .poses import PoseRunner
from .sleep import SleepController

log = logging.getLogger("milo-bridge")

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "faces"
POLICY_PATH = Path.home() / ".milo" / "policy.onnx"


def _optional(factory, what: str):
    try:
        return factory()
    except Exception as exc:
        log.warning("%s unavailable (%s: %s) — continuing without it", what, type(exc).__name__, exc)
        return None


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = BridgeConfig.load()
    log.info("milo-bridge starting as %s (%s)", cfg.robot_name, cfg.robot_id)

    # Required hardware.
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
    display = FaceDisplay.from_hardware(ASSETS_DIR)
    runner = PoseRunner(servos, display)

    # Optional hardware/components.
    imu = _optional(Mpu6050.from_hardware, "IMU")
    if imu is not None:
        log.info("calibrating IMU gyro bias — keep the robot still")
        await asyncio.to_thread(imu.calibrate_gyro)
        log.info("IMU gyro calibration complete")
    camera = _optional(lambda: CameraStreamer.from_hardware(fps=cfg.video_fps), "camera")
    audio = _optional(AudioIO, "audio")

    gait = GaitEngine(servos, imu=imu, policy_path=POLICY_PATH)
    log.info("gait backend: %s", gait.backend)

    graph = GraphStore(cfg.graph_db_path)
    graph_api = GraphApi(graph)

    from .webapp.control import ControlBroker
    from .webapp.deps import WebDeps
    from .webapp.logbuf import RingBufferLogHandler
    from .webapp.media_hub import MediaHub
    from .webapp.server import start_web

    log_buffer = RingBufferLogHandler()
    logging.getLogger().addHandler(log_buffer)

    sleep_controller = SleepController(
        runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=servos
    )

    broker = ControlBroker()
    hub = MediaHub(camera=camera, audio=audio, on_audio_level=sleep_controller.handle_audio_level)

    manager = None

    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
        graph_api=graph_api, graph_store=graph,
        broker=broker, media_hub=hub, log_buffer=log_buffer,
        # manager is assigned below; guard the startup window before it exists
        get_link_state=lambda: manager.link_state if manager is not None else "disconnected",
    )
    web_task = asyncio.create_task(start_web(web_deps)) if cfg.web_enabled else None

    await runner.run("rest")
    display.start_idle()
    log.info("resting with idle face; scanning for brains")

    manager = SessionManager(
        cfg,
        servos=servos,
        display=display,
        runner=runner,
        audio=audio,
        graph_api=graph_api,
        gait=gait,
        media_hub=hub,
        broker=broker,
        sleep_controller=sleep_controller,
    )

    gait_task = asyncio.create_task(gait.run())
    backup_task = asyncio.create_task(_nightly_backup(graph, Path(cfg.data_dir) / "backups"))
    try:
        await manager.run_forever()
    finally:
        gait_task.cancel()
        backup_task.cancel()
        if web_task is not None:
            web_task.cancel()
        graph.close()


async def _nightly_backup(graph: GraphStore, dest_dir: Path) -> None:
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            dest = graph.backup(dest_dir)
            log.info("graph backed up to %s", dest)
        except Exception as exc:
            log.warning("graph backup failed: %s", exc)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
