"""Dependency bundle handed to the web app — everything it may touch.

Typed loosely (Any) on purpose: real drivers on the Pi, fakes in tests,
and None where optional hardware is absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class WebDeps:
    config: Any
    runner: Any            # PoseRunner
    display: Any | None    # FaceDisplay
    servos: Any            # ServoDriver
    camera: Any | None     # CameraStreamer
    audio: Any | None      # AudioIO
    imu: Any | None        # Mpu6050
    gait: Any              # GaitEngine
    graph_api: Any         # GraphApi
    graph_store: Any       # GraphStore
    broker: Any | None     # ControlBroker (Task 2)
    media_hub: Any | None  # MediaHub (Task 4)
    log_buffer: Any | None # RingBufferLogHandler (Task 7)
    get_link_state: Callable[[], str]
