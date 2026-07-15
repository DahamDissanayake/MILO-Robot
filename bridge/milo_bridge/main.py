"""milo-bridge service entrypoint.

Composition root: build drivers, gait engine, knowledge graph, and sleep
controller, show a startup hardware checklist, play the boot tilt gesture
(the same look_down/standby motion Q/E trigger by hand) to settle into
stand with a hardware-status-aware idle face, then hand control to the
SessionManager (discovery -> pairing/auth -> streams -> dispatch).

Every peripheral degrades gracefully — a missing camera, policy file, PCA9685,
or OLED logs a warning and falls back to a null stand-in instead of killing
the service.
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
from .drivers.null_hardware import NullDisplay, NullServos
from .drivers.servos import ServoDriver
from .drivers.smooth_servos import SmoothServos
from .gait.engine import GaitEngine
from .graph.api import GraphApi
from .graph.store import GraphStore
from .net.session import SessionManager
from .poses import PoseRunner
from .sleep import SleepController

log = logging.getLogger("milo-bridge")

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "faces"
POLICY_PATH = Path.home() / ".milo" / "policy.onnx"


def _optional(factory, what: str) -> tuple[object | None, bool]:
    try:
        return factory(), True
    except Exception as exc:
        log.warning("%s unavailable (%s: %s) — continuing without it", what, type(exc).__name__, exc)
        return None, False


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = BridgeConfig.load()
    log.info("milo-bridge starting as %s (%s)", cfg.robot_name, cfg.robot_id)

    # Hardware -- every peripheral degrades gracefully to a null stand-in
    # on failure, so one missing/unplugged I2C device never takes the
    # whole service (including the web dashboard) down with it.
    servos, servos_ok = _optional(
        lambda: ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms),
        "servos",
    )
    servos = servos or NullServos()
    motion_servos = SmoothServos(servos, stagger_ms=cfg.servo_stagger_ms)
    motion_servos.start()
    display, display_ok = _optional(lambda: FaceDisplay.from_hardware(ASSETS_DIR), "display")
    display = display or NullDisplay()
    runner = PoseRunner(motion_servos, display)

    imu, imu_ok = _optional(Mpu6050.from_hardware, "IMU")
    if imu is not None:
        log.info("calibrating IMU gyro bias — keep the robot still")
        await asyncio.to_thread(imu.calibrate_gyro)
        log.info("IMU gyro calibration complete")
    camera, camera_ok = _optional(lambda: CameraStreamer.from_hardware(fps=cfg.video_fps), "camera")
    audio, audio_ok = _optional(AudioIO, "audio")
    hardware_status = {
        "servos": servos_ok, "display": display_ok, "imu": imu_ok,
        "camera": camera_ok, "audio": audio_ok,
    }

    gait = GaitEngine(motion_servos, imu=imu, runner=runner, policy_path=POLICY_PATH)
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
        runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=motion_servos
    )

    broker = ControlBroker()
    hub = MediaHub(camera=camera, audio=audio, on_audio_level=sleep_controller.handle_audio_level)

    manager = None

    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=motion_servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
        graph_api=graph_api, graph_store=graph,
        broker=broker, media_hub=hub, log_buffer=log_buffer,
        hardware_status=hardware_status,
        # manager is assigned below; guard the startup window before it exists
        get_link_state=lambda: manager.link_state if manager is not None else "disconnected",
    )
    web_task = asyncio.create_task(start_web(web_deps)) if cfg.web_enabled else None

    await display.show_status(hardware_status)
    # Boot gesture is the same look_down tilt Q/E trigger by hand, immediately
    # followed by the same standby() recovery a released E does -- previously
    # this was a bespoke "wake_up" dip, which read as an unwanted extra pose
    # jump (stand -> dip -> stand) rather than a single deliberate gesture.
    # look_down has end_stand=False (it's meant to hold, not auto-recover), so
    # standby() below is doing the real work of returning to stand, not just
    # confirming what already happened.
    await runner.run("look_down")
    gait.standby()
    # look_down doesn't call start_idle() itself (end_stand=False skips
    # PoseRunner's own recovery tail), so set the hardware-status-aware face
    # directly -- stop_idle() first is just defensive in case an idle loop is
    # already running from an earlier boot path.
    display.stop_idle()
    display.start_idle(base_face="idle" if all(hardware_status.values()) else "confused")
    log.info("boot sequence complete; scanning for brains")

    manager = SessionManager(
        cfg,
        servos=motion_servos,
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
        motion_servos.stop()
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
