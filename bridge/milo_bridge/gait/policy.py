"""ONNX RL policy backend.

The observation layout is the contract with ``training/milo_training/env.py``
— change one, change both, bump OBS_VERSION.

Observation (30 dims, float32):
    [ 0: 8]  joint positions, radians offset from stand pose, firmware channel order
    [ 8:16]  previous action (normalized deltas, [-1, 1])
    [16:18]  roll, pitch (radians)
    [18:21]  angular velocity (rad/s, x/y/z)
    [21:24]  gravity vector in body frame (unit)
    [24:27]  command: vx (m/s), vy (m/s), yaw rate (rad/s)
    [27:30]  reserved (zeros; padding for future proprioception)

Action (8 dims, float32): target-angle deltas from stand, normalized to
[-1, 1] over ±ACTION_LIMIT_DEG, firmware channel order.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..poses import STAND_ANGLES

OBS_VERSION = 1
OBS_DIM = 30
ACTION_DIM = 8
ACTION_LIMIT_DEG = 25.0

SERVO_ORDER = ("R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4")  # channels 0..7
STAND_VECTOR_DEG = np.array([STAND_ANGLES[n] for n in SERVO_ORDER], dtype=np.float32)


def build_observation(
    joint_angles_deg: np.ndarray,
    prev_action: np.ndarray,
    roll_deg: float,
    pitch_deg: float,
    gyro_dps: tuple[float, float, float],
    command: tuple[float, float, float],
) -> np.ndarray:
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    obs[0:8] = np.radians(joint_angles_deg - STAND_VECTOR_DEG)
    obs[8:16] = prev_action
    obs[16] = roll
    obs[17] = pitch
    obs[18:21] = np.radians(gyro_dps)
    # Gravity direction in body frame from roll/pitch (unit vector).
    obs[21] = -math.sin(pitch)
    obs[22] = math.sin(roll) * math.cos(pitch)
    obs[23] = -math.cos(roll) * math.cos(pitch)
    obs[24] = command[0]
    obs[25] = command[1]
    obs[26] = math.radians(command[2])
    return obs


def action_to_angles(action: np.ndarray) -> dict[str, float]:
    """Normalized action -> absolute servo angles (stand pose + clamped delta)."""
    deltas = np.clip(action, -1.0, 1.0) * ACTION_LIMIT_DEG
    angles = np.clip(STAND_VECTOR_DEG + deltas, 0.0, 180.0)
    return {name: float(angles[i]) for i, name in enumerate(SERVO_ORDER)}


class OnnxPolicy:
    """Runs policy.onnx via onnxruntime; must stay well under 20 ms per step
    to hold the 50 Hz loop (measured <1 ms for a 2x64 MLP on the Zero 2W)."""

    def __init__(self, model_path: Path | str):
        import onnxruntime as ort

        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self.prev_action = np.zeros(ACTION_DIM, dtype=np.float32)

    def step(
        self,
        joint_angles_deg: np.ndarray,
        roll_deg: float,
        pitch_deg: float,
        gyro_dps: tuple[float, float, float],
        command: tuple[float, float, float],
    ) -> dict[str, float]:
        obs = build_observation(
            joint_angles_deg, self.prev_action, roll_deg, pitch_deg, gyro_dps, command
        )
        action = self._session.run(None, {self._input_name: obs[None, :]})[0][0]
        action = np.asarray(action, dtype=np.float32).reshape(ACTION_DIM)
        self.prev_action = np.clip(action, -1.0, 1.0)
        return action_to_angles(action)
