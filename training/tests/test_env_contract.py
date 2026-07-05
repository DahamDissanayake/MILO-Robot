"""Contract + pure-function tests; MuJoCo/SB3 not required."""

import numpy as np
import pytest

from milo_training import env as tr


def test_obs_action_contract_matches_bridge_policy():
    bridge_policy = pytest.importorskip(
        "milo_bridge.gait.policy", reason="bridge not installed here"
    )
    assert tr.OBS_DIM == bridge_policy.OBS_DIM == 30
    assert tr.ACTION_DIM == bridge_policy.ACTION_DIM == 8
    assert tr.ACTION_LIMIT_RAD == pytest.approx(np.deg2rad(bridge_policy.ACTION_LIMIT_DEG))


def test_reward_prefers_tracking_the_command():
    base = dict(
        yaw_actual=0.0, yaw_cmd=0.0, roll=0.0, pitch=0.0,
        action=np.zeros(8), prev_action=np.zeros(8), foot_slip=0.0, fell=False,
    )
    on_track = tr.compute_reward(vx_actual=0.10, vx_cmd=0.10, **base)
    off_track = tr.compute_reward(vx_actual=0.00, vx_cmd=0.10, **base)
    assert on_track > off_track


def test_reward_penalizes_falls_heavily():
    base = dict(
        vx_actual=0.1, vx_cmd=0.1, yaw_actual=0.0, yaw_cmd=0.0, roll=0.0, pitch=0.0,
        action=np.zeros(8), prev_action=np.zeros(8), foot_slip=0.0,
    )
    assert tr.compute_reward(fell=False, **base) - tr.compute_reward(fell=True, **base) == pytest.approx(20.0)


def test_reward_penalizes_energy_and_thrash():
    base = dict(
        vx_actual=0.1, vx_cmd=0.1, yaw_actual=0.0, yaw_cmd=0.0, roll=0.0, pitch=0.0,
        foot_slip=0.0, fell=False,
    )
    calm = tr.compute_reward(action=np.zeros(8), prev_action=np.zeros(8), **base)
    thrashing = tr.compute_reward(action=np.ones(8), prev_action=-np.ones(8), **base)
    assert calm > thrashing


def test_randomization_ranges_match_plan():
    rng = np.random.default_rng(0)
    for _ in range(200):
        r = tr.sample_randomization(rng)
        assert 0.6 <= r.friction_scale <= 1.4
        assert 0.8 <= r.servo_strength <= 1.2
        assert 0.010 <= r.latency_s <= 0.050
        assert 0.9 <= r.mass_scale <= 1.1
        assert 2.0 <= r.push_interval_s <= 4.0


def test_command_distribution_within_engine_limits():
    rng = np.random.default_rng(1)
    for _ in range(200):
        vx, vy, yaw = tr.sample_command(rng)
        assert 0.0 <= vx <= 0.15
        assert vy == 0.0
        assert abs(yaw) <= 0.8


def test_quat_to_roll_pitch_identity_and_tilt():
    roll, pitch = tr._quat_to_roll_pitch(np.array([1.0, 0.0, 0.0, 0.0]))
    assert roll == pytest.approx(0.0) and pitch == pytest.approx(0.0)
    # 90-degree roll about x
    s = np.sin(np.pi / 4)
    roll, _ = tr._quat_to_roll_pitch(np.array([np.cos(np.pi / 4), s, 0.0, 0.0]))
    assert roll == pytest.approx(np.pi / 2)


def test_mujoco_env_smoke_if_available():
    pytest.importorskip("mujoco")
    env = tr.MiloEnv()
    obs, _ = env.reset(seed=0)
    assert obs.shape == (tr.OBS_DIM,)
    obs, reward, terminated, truncated, _ = env.step(np.zeros(tr.ACTION_DIM))
    assert obs.shape == (tr.OBS_DIM,)
    assert isinstance(reward, float)
    env.close()
