"""Gymnasium environment for Milo gait training.

Obs/action contract mirrors ``bridge/milo_bridge/gait/policy.py`` (OBS_VERSION
1): 30-dim observation, 8-dim normalized delta action, firmware channel order.

The reward and domain-randomization pieces are pure functions/dataclasses so
they are unit-testable without MuJoCo installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:  # keep importable on machines without gymnasium's heavy friends
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover
    gym = None

OBS_DIM = 30
ACTION_DIM = 8
ACTION_LIMIT_RAD = np.deg2rad(25.0)
CONTROL_HZ = 50
EPISODE_SECONDS = 10.0
FALL_ROLL_PITCH_RAD = np.deg2rad(55.0)

MODEL_XML = Path(__file__).resolve().parents[1] / "models" / "milo.xml"


# ---------------------------------------------------------------------------
# Reward (plan D.3): velocity tracking + upright - energy - slip - fall
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RewardWeights:
    velocity: float = 4.0
    yaw: float = 1.5
    upright: float = 0.6
    energy: float = 0.06
    slip: float = 0.4
    fall: float = 20.0
    action_rate: float = 0.15


def compute_reward(
    vx_actual: float,
    vx_cmd: float,
    yaw_actual: float,
    yaw_cmd: float,
    roll: float,
    pitch: float,
    action: np.ndarray,
    prev_action: np.ndarray,
    foot_slip: float,
    fell: bool,
    weights: RewardWeights = RewardWeights(),
) -> float:
    r = 0.0
    r += weights.velocity * float(np.exp(-((vx_actual - vx_cmd) ** 2) / 0.004))
    r += weights.yaw * float(np.exp(-((yaw_actual - yaw_cmd) ** 2) / 0.09))
    r += weights.upright * float(np.cos(roll) * np.cos(pitch))
    r -= weights.energy * float(np.sum(np.square(action)))
    r -= weights.action_rate * float(np.sum(np.square(action - prev_action)))
    r -= weights.slip * foot_slip
    if fell:
        r -= weights.fall
    return r


# ---------------------------------------------------------------------------
# Domain randomization (plan D.4) - what makes sim policies survive real MG90s
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Randomization:
    friction_scale: float      # 0.6 - 1.4
    servo_strength: float      # 0.8 - 1.2
    latency_s: float           # 0.010 - 0.050
    mass_scale: float          # 0.9 - 1.1
    imu_noise_std: float       # rad / rad/s noise
    push_interval_s: float     # 2 - 4
    push_force_n: float        # up to ~1.5 N on a 430 g robot


def sample_randomization(rng: np.random.Generator) -> Randomization:
    return Randomization(
        friction_scale=float(rng.uniform(0.6, 1.4)),
        servo_strength=float(rng.uniform(0.8, 1.2)),
        latency_s=float(rng.uniform(0.010, 0.050)),
        mass_scale=float(rng.uniform(0.9, 1.1)),
        imu_noise_std=float(rng.uniform(0.005, 0.03)),
        push_interval_s=float(rng.uniform(2.0, 4.0)),
        push_force_n=float(rng.uniform(0.3, 1.5)),
    )


def sample_command(rng: np.random.Generator) -> tuple[float, float, float]:
    """Training command distribution: forward up to 0.15 m/s, yaw up to 45 deg/s."""
    mode = rng.integers(0, 3)
    if mode == 0:
        return float(rng.uniform(0.03, 0.15)), 0.0, 0.0
    if mode == 1:
        return 0.0, 0.0, float(rng.uniform(-0.8, 0.8))  # rad/s
    return float(rng.uniform(0.0, 0.12)), 0.0, float(rng.uniform(-0.5, 0.5))


# ---------------------------------------------------------------------------
# The MuJoCo env
# ---------------------------------------------------------------------------

if gym is not None:

    class MiloEnv(gym.Env):
        metadata = {"render_modes": ["human"]}

        def __init__(self, model_path: Path | str = MODEL_XML, render_mode: str | None = None):
            import mujoco

            self._mujoco = mujoco
            self.model = mujoco.MjModel.from_xml_path(str(model_path))
            self.data = mujoco.MjData(self.model)
            self.render_mode = render_mode
            self._viewer = None
            self._steps_per_control = max(1, round(1 / (CONTROL_HZ * self.model.opt.timestep)))

            self.observation_space = spaces.Box(-np.inf, np.inf, (OBS_DIM,), np.float32)
            self.action_space = spaces.Box(-1.0, 1.0, (ACTION_DIM,), np.float32)

            self._rng = np.random.default_rng()
            self._rand: Randomization | None = None
            self._command = (0.0, 0.0, 0.0)
            self._prev_action = np.zeros(ACTION_DIM, dtype=np.float32)
            self._pending_ctrl: list[np.ndarray] = []  # latency queue
            self._t = 0.0
            self._next_push = 0.0
            self._base_friction = self.model.geom_friction.copy()
            self._base_mass = self.model.body_mass.copy()
            self._base_gainprm = self.model.actuator_gainprm.copy()

        # -- helpers --------------------------------------------------------
        def _apply_randomization(self) -> None:
            r = self._rand
            self.model.geom_friction[:] = self._base_friction
            self.model.geom_friction[:, 0] *= r.friction_scale
            self.model.body_mass[:] = self._base_mass * r.mass_scale
            self.model.actuator_gainprm[:] = self._base_gainprm
            self.model.actuator_gainprm[:, 0] *= r.servo_strength

        def _observe(self) -> np.ndarray:
            noise = self._rand.imu_noise_std if self._rand else 0.0
            qpos = self.data.qpos[7:15]  # 8 joints after the free joint
            quat = self.data.qpos[3:7]
            roll, pitch = _quat_to_roll_pitch(quat)
            gyro = self.data.qvel[3:6] + self._rng.normal(0, noise, 3)
            obs = np.zeros(OBS_DIM, dtype=np.float32)
            obs[0:8] = qpos
            obs[8:16] = self._prev_action
            obs[16] = roll + self._rng.normal(0, noise)
            obs[17] = pitch + self._rng.normal(0, noise)
            obs[18:21] = gyro
            obs[21] = -np.sin(pitch)
            obs[22] = np.sin(roll) * np.cos(pitch)
            obs[23] = -np.cos(roll) * np.cos(pitch)
            obs[24:27] = self._command
            return obs

        # -- gym API ---------------------------------------------------------
        def reset(self, *, seed: int | None = None, options: dict | None = None):
            super().reset(seed=seed)
            if seed is not None:
                self._rng = np.random.default_rng(seed)
            self._mujoco.mj_resetData(self.model, self.data)
            self._rand = sample_randomization(self._rng)
            self._apply_randomization()
            self._command = sample_command(self._rng)
            self._prev_action[:] = 0.0
            self._pending_ctrl.clear()
            self._t = 0.0
            self._next_push = self._rand.push_interval_s
            self._mujoco.mj_forward(self.model, self.data)
            return self._observe(), {}

        def step(self, action: np.ndarray):
            action = np.clip(np.asarray(action, dtype=np.float32), -1.0, 1.0)
            target = action * ACTION_LIMIT_RAD

            # Servo latency: delay the command by ~latency worth of control steps.
            delay_steps = max(0, round(self._rand.latency_s * CONTROL_HZ))
            self._pending_ctrl.append(target)
            applied = (
                self._pending_ctrl.pop(0)
                if len(self._pending_ctrl) > delay_steps
                else np.zeros(ACTION_DIM, dtype=np.float32)
            )
            self.data.ctrl[:] = applied

            # Random pushes.
            if self._t >= self._next_push:
                self.data.xfrc_applied[1, :2] = self._rng.uniform(
                    -self._rand.push_force_n, self._rand.push_force_n, 2
                )
                self._next_push = self._t + self._rand.push_interval_s
            else:
                self.data.xfrc_applied[1, :] = 0.0

            for _ in range(self._steps_per_control):
                self._mujoco.mj_step(self.model, self.data)
            self._t += 1.0 / CONTROL_HZ

            roll, pitch = _quat_to_roll_pitch(self.data.qpos[3:7])
            fell = abs(roll) > FALL_ROLL_PITCH_RAD or abs(pitch) > FALL_ROLL_PITCH_RAD
            reward = compute_reward(
                vx_actual=float(self.data.qvel[0]),
                vx_cmd=self._command[0],
                yaw_actual=float(self.data.qvel[5]),
                yaw_cmd=self._command[2],
                roll=roll,
                pitch=pitch,
                action=action,
                prev_action=self._prev_action,
                foot_slip=0.0,  # refined in sim-to-real iteration (D.8)
                fell=fell,
            )
            self._prev_action = action
            terminated = fell
            truncated = self._t >= EPISODE_SECONDS
            if self.render_mode == "human":
                self.render()
            return self._observe(), reward, terminated, truncated, {}

        def render(self):
            if self._viewer is None:
                from mujoco import viewer

                self._viewer = viewer.launch_passive(self.model, self.data)
            self._viewer.sync()

        def close(self):
            if self._viewer is not None:
                self._viewer.close()
                self._viewer = None


def _quat_to_roll_pitch(quat: np.ndarray) -> tuple[float, float]:
    w, x, y, z = quat
    roll = np.arctan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = np.arcsin(np.clip(2 * (w * y - z * x), -1.0, 1.0))
    return float(roll), float(pitch)
