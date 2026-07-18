"""Dependency bundle handed to the web app — everything it may touch.

Typed loosely (Any) on purpose: real drivers on the Pi, fakes in tests.
camera/audio/imu are None where that optional hardware is absent;
servos/display are never None -- they fall back to NullServos/NullDisplay
on failure instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class WebDeps:
    config: Any
    runner: Any            # PoseRunner
    display: Any            # FaceDisplay (never None -- NullDisplay stands in on failure)
    servos: Any            # ServoDriver (never None -- NullServos stands in on failure)
    camera: Any | None     # CameraStreamer
    audio: Any | None      # AudioIO
    imu: Any | None        # Mpu6050
    gait: Any              # GaitEngine
    graph_api: Any         # GraphApi
    graph_store: Any       # GraphStore
    broker: Any | None     # ControlBroker (Task 2)
    media_hub: Any | None  # MediaHub (Task 4)
    log_buffer: Any | None # RingBufferLogHandler (Task 7)
    crash_log: Any          # CrashLog -- always constructed, never None
    hardware_status: dict[str, bool]  # servos/display/imu/camera/audio presence at boot
    get_link_state: Callable[[], str]
    robot_server: Any = None  # RobotServer -- .connected_brains, .active_brain_id, .pairing, .advertiser, .paired_brains()
