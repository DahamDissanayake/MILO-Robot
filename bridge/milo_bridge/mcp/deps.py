"""Dependency bundle for the movement/face/speech/IMU MCP server -- mirrors
webapp/deps.py's WebDeps pattern for the same underlying objects, MCP-side.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class MovementGuard:
    """Tracks the one pose/gait animation run_pose/turn may have in flight,
    so two overlapping calls -- the brain's own tool-calling loop and a
    human's MCP client testing alongside it -- serialize instead of racing
    for the same PoseRunner (mirrors MotionService's existing
    "pose-running" guard in webapp/motion.py)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, coro) -> None:
        self._task = asyncio.ensure_future(coro)


@dataclass
class McpDeps:
    gait: Any             # GaitEngine
    runner: Any            # PoseRunner
    imu: Any | None        # Mpu6050
    broker: Any            # ControlBroker
    servos: Any            # SmoothServos (relax/hold)
    display: Any           # FaceDisplay
    audio: Any | None      # AudioIO
    movement_guard: MovementGuard = field(default_factory=MovementGuard)
