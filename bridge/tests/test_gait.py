import asyncio
import math

import numpy as np
import pytest

from milo_bridge.drivers.imu import ImuState
from milo_bridge.drivers.servos import SAFE_ANGLE_MAX, SAFE_ANGLE_MIN
from milo_bridge.gait.cpg import GAIT_NEUTRAL, LEGS, CpgGait
from milo_bridge.gait.engine import GaitEngine
from milo_bridge.gait.policy import (
    ACTION_DIM,
    OBS_DIM,
    SERVO_ORDER,
    STAND_VECTOR_DEG,
    action_to_angles,
    build_observation,
)
from milo_bridge.poses import REST_ANGLES, STAND_ANGLES


def _safe(angles):
    """A target dict as the gait engine now writes it: clamped into the safe
    band, so STAND's rear legs at 0/180 land at 5/175."""
    return {n: min(max(a, SAFE_ANGLE_MIN), SAFE_ANGLE_MAX) for n, a in angles.items()}


class FakeServos:
    def __init__(self):
        self.angles: dict[str, float] = dict(STAND_ANGLES)
        self.writes = 0

    def set_angle(self, name, angle):
        self.angles[name] = angle
        self.writes += 1

    def last_angle(self, name):
        return self.angles.get(name)


def test_gait_writes_stay_within_the_safe_angle_band():
    # A stalled servo at a mechanical hard-stop browns out the shared rail,
    # so no gait write -- discrete stand target (has 0/180) or a live CPG
    # swing -- may ever land outside [SAFE_ANGLE_MIN, SAFE_ANGLE_MAX].
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.standby()  # STAND drives the rear legs to 0deg and 180deg
    assert all(SAFE_ANGLE_MIN <= a <= SAFE_ANGLE_MAX for a in servos.angles.values())

    now = {"t": 0.0}
    engine = GaitEngine(servos, clock=lambda: now["t"])
    engine.set_velocity_command(0.15, 0.0, 45.0)
    for step in range(1, 200):
        now["t"] = step * 0.02
        engine.tick()
        assert all(SAFE_ANGLE_MIN <= a <= SAFE_ANGLE_MAX for a in servos.angles.values())


# --- CPG ------------------------------------------------------------------

def test_cpg_zero_command_is_stand():
    assert CpgGait().angles_at(0.42, 0.0, 0.0, 0.0) == STAND_ANGLES


def test_cpg_angles_always_in_range():
    gait = CpgGait()
    for t in np.linspace(0, 3, 120):
        for cmd in [(0.15, 0, 0), (-0.15, 0, 0), (0, 0, 45), (0.1, 0, -30)]:
            for angle in gait.angles_at(float(t), *cmd).values():
                assert 0.0 <= angle <= 180.0


def test_cpg_diagonal_pairs_move_in_antiphase():
    gait = CpgGait()
    t = 0.1  # somewhere mid-swing
    angles = gait.angles_at(t, 0.15, 0.0, 0.0)
    fl_dev = (angles["L1"] - GAIT_NEUTRAL["L1"]) * -1.0   # unmirror left
    fr_dev = (angles["R1"] - GAIT_NEUTRAL["R1"]) * +1.0
    rr_dev = (angles["R3"] - GAIT_NEUTRAL["R3"]) * +1.0
    assert fl_dev * fr_dev < 0          # opposite diagonals oppose
    assert fl_dev * rr_dev > 0          # same diagonal agrees
    assert abs(fl_dev) > 1.0            # actually moving


def test_cpg_turn_creates_left_right_asymmetry():
    gait = CpgGait()
    fwd = gait.angles_at(0.1, 0.12, 0.0, 0.0)
    turning = gait.angles_at(0.1, 0.12, 0.0, 40.0)
    left_change = abs(turning["L1"] - fwd["L1"])
    right_change = abs(turning["R1"] - fwd["R1"])
    assert left_change != pytest.approx(right_change, abs=0.5)


def test_cpg_is_periodic():
    gait = CpgGait()
    period = 1.0 / gait.params.frequency_hz
    a = gait.angles_at(0.2, 0.1, 0.0, 0.0)
    b = gait.angles_at(0.2 + period, 0.1, 0.0, 0.0)
    for name in a:
        assert a[name] == pytest.approx(b[name], abs=1e-6)


def test_cpg_leg_map_covers_all_eight_servos():
    covered = {s for hip, knee, _, _ in LEGS.values() for s in (hip, knee)}
    assert covered == set(STAND_ANGLES)


# --- policy plumbing --------------------------------------------------------

def test_observation_layout():
    obs = build_observation(
        joint_angles_deg=STAND_VECTOR_DEG.copy(),
        prev_action=np.zeros(8, dtype=np.float32),
        roll_deg=0.0,
        pitch_deg=0.0,
        gyro_dps=(0.0, 0.0, 0.0),
        command=(0.1, 0.0, 30.0),
    )
    assert obs.shape == (OBS_DIM,) and obs.dtype == np.float32
    assert np.allclose(obs[0:8], 0.0)            # at stand pose
    assert obs[23] == pytest.approx(-1.0)        # gravity straight down
    assert obs[24] == pytest.approx(0.1)
    assert obs[26] == pytest.approx(math.radians(30.0))


def test_action_to_angles_clamps():
    zero = action_to_angles(np.zeros(ACTION_DIM))
    assert zero == {n: pytest.approx(STAND_ANGLES[n]) for n in SERVO_ORDER}
    extreme = action_to_angles(np.full(ACTION_DIM, 10.0))  # way out of range
    for name in SERVO_ORDER:
        assert extreme[name] == pytest.approx(min(STAND_ANGLES[name] + 25.0, 180.0))


def test_onnx_policy_runs_a_tiny_model(tmp_path):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper
    import numpy as np

    from milo_bridge.gait.policy import OnnxPolicy

    # obs[1,30] @ W[30,8] -> action[1,8], W chosen to output 0.5 everywhere
    weight = np.full((OBS_DIM, ACTION_DIM), 0.0, dtype=np.float32)
    bias = np.full((ACTION_DIM,), 0.5, dtype=np.float32)
    graph = helper.make_graph(
        nodes=[helper.make_node("Gemm", ["obs", "W", "B"], ["action"])],
        name="tiny",
        inputs=[helper.make_tensor_value_info("obs", TensorProto.FLOAT, [1, OBS_DIM])],
        outputs=[helper.make_tensor_value_info("action", TensorProto.FLOAT, [1, ACTION_DIM])],
        initializer=[
            helper.make_tensor("W", TensorProto.FLOAT, weight.shape, weight.flatten()),
            helper.make_tensor("B", TensorProto.FLOAT, bias.shape, bias),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    path = tmp_path / "policy.onnx"
    onnx.save(model, path)

    policy = OnnxPolicy(path)
    angles = policy.step(
        STAND_VECTOR_DEG.copy(), 0.0, 0.0, (0.0, 0.0, 0.0), (0.1, 0.0, 0.0)
    )
    # action 0.5 -> +12.5 deg from stand on every channel (clamped at 180)
    for name in SERVO_ORDER:
        expected = min(STAND_ANGLES[name] + 12.5, 180.0)
        assert angles[name] == pytest.approx(expected)
    assert np.allclose(policy.prev_action, 0.5)


# --- engine -----------------------------------------------------------------

def test_engine_idle_until_commanded():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    assert engine.backend == "cpg"
    assert engine.tick() is None
    assert servos.writes == 0


def test_engine_walks_on_command_and_stops():
    now = {"t": 0.0}
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: now["t"])
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 0.15
    written = engine.tick()
    assert written is not None and servos.writes == 8
    engine.set_velocity_command(0.0, 0.0, 0.0)
    assert engine.tick() is None


def test_engine_missing_policy_file_falls_back(tmp_path):
    engine = GaitEngine(FakeServos(), policy_path=tmp_path / "nope.onnx")
    assert engine.backend == "cpg"


# --- mode / reset / standby --------------------------------------------------

class FakeImu:
    def __init__(self, roll=0.0, pitch=0.0):
        self.roll = roll
        self.pitch = pitch

    def update(self):
        return ImuState(roll=self.roll, pitch=self.pitch, yaw=0.0, gyro=(0.0, 0.0, 0.0), accel=(0.0, 0.0, 1.0))


class FakeRunner:
    def __init__(self):
        self.is_running = False


def test_mode_defaults_to_balanced_and_validates():
    engine = GaitEngine(FakeServos())
    assert engine.mode == "balanced"
    engine.set_mode("raw")
    assert engine.mode == "raw"
    with pytest.raises(ValueError):
        engine.set_mode("sideways")


def test_reset_writes_rest_angles_and_stops_active_gait():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    engine.reset()
    assert servos.angles == REST_ANGLES
    assert engine.tick() is None  # gait command was cleared, not just paused


def test_standby_writes_stand_angles():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.standby()
    assert servos.angles == _safe(STAND_ANGLES)


def test_auto_standby_on_stop_in_balanced_mode_only():
    servos_balanced = FakeServos()
    engine_balanced = GaitEngine(servos_balanced, clock=lambda: 0.0)
    engine_balanced.set_mode("balanced")
    engine_balanced.set_velocity_command(0.1, 0.0, 0.0)
    servos_balanced.angles = {}  # isolate the stop's effect from the walk-start write
    engine_balanced.set_velocity_command(0.0, 0.0, 0.0)
    assert servos_balanced.angles == _safe(STAND_ANGLES)

    servos_raw = FakeServos()
    engine_raw = GaitEngine(servos_raw, clock=lambda: 0.0)
    engine_raw.set_mode("raw")  # default is now balanced; this half tests raw specifically
    engine_raw.set_velocity_command(0.1, 0.0, 0.0)
    servos_raw.angles = {}
    engine_raw.set_velocity_command(0.0, 0.0, 0.0)
    assert servos_raw.angles == {}  # raw mode: no auto-standby


# --- deference to a running pose ----------------------------------------------

def test_tick_defers_to_a_running_pose():
    servos = FakeServos()
    runner = FakeRunner()
    engine = GaitEngine(servos, runner=runner, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    runner.is_running = True
    assert engine.tick() is None
    assert servos.writes == 0


# --- balance integration -------------------------------------------------------

def test_balanced_mode_applies_imu_correction_while_walking():
    now = {"t": 0.0}
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: now["t"])
    engine.set_mode("balanced")
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 0.15
    raw_cpg = CpgGait().angles_at(0.15, 0.1, 0.0, 0.0)
    written = engine.tick()
    assert written["L1"] != pytest.approx(raw_cpg["L1"])  # balance nudged it


def test_raw_mode_ignores_imu_even_with_tilt():
    now = {"t": 0.0}
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: now["t"])
    engine.set_mode("raw")  # default is now balanced; this test is raw-specific
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 0.15
    written = engine.tick()
    expected = CpgGait().angles_at(0.15, 0.1, 0.0, 0.0)
    assert written == expected


def test_balanced_mode_self_levels_at_standstill():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    written = engine.tick()
    assert written is not None
    assert written["L1"] != pytest.approx(STAND_ANGLES["L1"])


def test_raw_mode_idle_does_not_self_level():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("raw")  # default is now balanced; this test is raw-specific
    assert engine.tick() is None
    assert servos.writes == 0


def test_reset_in_balanced_mode_survives_the_next_tick():
    servos = FakeServos()
    imu = FakeImu(roll=0.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.reset()
    assert servos.angles == REST_ANGLES
    engine.tick()  # simulates the next background tick
    assert servos.angles == REST_ANGLES  # must NOT have snapped to STAND_ANGLES


def test_reset_does_not_jerk_under_real_nonzero_imu_tilt():
    # The actual field bug: with a real IMU (never exactly roll=pitch=0),
    # hold-level used to keep re-deriving a *different* balance-corrected
    # target from REST_ANGLES on every single tick, jerking the legs
    # (worst on R1/R2) instead of settling. balance.correct()'s trim math
    # is only valid for a standing leg geometry, not the folded rest pose,
    # so reset() must disable self-leveling entirely rather than feed it a
    # pose it can't correctly reason about.
    servos = FakeServos()
    imu = FakeImu(roll=6.0, pitch=-3.0)  # realistic nonzero tilt reading
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.reset()
    assert servos.angles == REST_ANGLES
    servos.writes = 0
    for _ in range(10):  # simulate a run of background ticks
        assert engine.tick() is None
    assert servos.writes == 0  # never re-corrected/re-written
    assert servos.angles == REST_ANGLES  # stayed put, no jerking


def test_standby_in_balanced_mode_survives_the_next_tick():
    servos = FakeServos()
    imu = FakeImu(roll=0.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.standby()
    assert servos.angles == _safe(STAND_ANGLES)
    engine.tick()
    assert servos.angles == _safe(STAND_ANGLES)


def test_standby_still_self_levels_under_nonzero_imu_tilt():
    # Levelable holds (standby's STAND_ANGLES) must keep correcting --
    # only the folded/non-standing holds (reset's REST_ANGLES, a just-
    # finished pose) should stop.
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.standby()
    written = engine.tick()
    assert written is not None
    assert written["L1"] != pytest.approx(STAND_ANGLES["L1"])  # correction applied


def test_new_gait_command_clears_a_stale_holding_target():
    servos = FakeServos()
    imu = FakeImu(roll=0.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.reset()  # raw mode: one-shot REST write, sets _holding_target
    engine.set_velocity_command(0.1, 0.0, 0.0)  # start walking -> must clear the stale target
    engine.set_velocity_command(0.0, 0.0, 0.0)  # stop again, still raw mode (no auto-standby)
    engine.set_mode("balanced")
    engine.tick()
    assert servos.angles == _safe(STAND_ANGLES)  # falls back to STAND_ANGLES, not the stale REST target


# --- manual servo mode -------------------------------------------------------

def test_set_manual_stops_all_writes_and_clears_active_command():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    engine.set_manual(True)
    assert engine.tick() is None
    assert servos.writes == 0


def test_manual_mode_blocks_balanced_self_leveling_too():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.set_manual(True)
    assert engine.tick() is None
    assert servos.writes == 0


def test_set_suspended_stops_all_writes():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    engine.set_suspended(True)
    assert engine.tick() is None
    assert servos.writes == 0


def test_suspended_blocks_balanced_self_leveling_too():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.set_suspended(True)
    assert engine.tick() is None
    assert servos.writes == 0


def test_pose_completing_holds_current_servos_not_a_stale_target():
    # Regression: a scripted pose that intentionally ends off-stand (e.g.
    # "dead"/"point", end_stand=False) must not get yanked back to
    # STAND_ANGLES the instant it finishes just because balanced mode's
    # hold-level treats "no active command" as its cue to self-level
    # toward a stale target. Nor should hold-level actively re-correct the
    # pose's ending posture at all -- balance.correct()'s roll/pitch trim
    # assumes a standing leg geometry, so applying it to an arbitrary
    # pose-end posture (unknown whether it's standing-like) would jerk the
    # legs the same way reset()'s REST_ANGLES hold used to. The fix leaves
    # a just-finished pose's posture alone: no further writes until an
    # explicit standby()/walk command takes over.
    servos = FakeServos()
    imu = FakeImu(roll=0.0, pitch=0.0)
    runner = FakeRunner()
    engine = GaitEngine(servos, imu=imu, runner=runner, clock=lambda: 0.0)
    engine.set_mode("balanced")
    runner.is_running = True
    assert engine.tick() is None  # deferring while the pose runs

    off_stand_angles = {n: 45.0 for n in STAND_ANGLES}
    servos.angles = dict(off_stand_angles)  # simulates PoseRunner's writes
    servos.writes = 0
    runner.is_running = False
    written = engine.tick()  # pose just finished; must not be re-driven
    assert written is None
    assert servos.writes == 0
    assert servos.angles == off_stand_angles  # left exactly where the pose ended it


def test_gait_resumes_with_a_fresh_phase_after_a_pose_defers_it():
    now = {"t": 0.0}
    servos = FakeServos()
    runner = FakeRunner()
    engine = GaitEngine(servos, runner=runner, clock=lambda: now["t"])
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 5.0
    runner.is_running = True
    assert engine.tick() is None  # deferring while the pose runs
    now["t"] = 5.5
    assert engine.tick() is None  # still deferring
    runner.is_running = False
    now["t"] = 5.52
    written = engine.tick()  # pose just finished; gait resumes
    expected = CpgGait().angles_at(0.0, 0.1, 0.0, 0.0)  # phase restarted at t=0, not the stale elapsed time
    assert written == expected


def test_run_survives_a_tick_exception_instead_of_dying_silently():
    # Same reasoning as SmoothServos: an IMU or servo-write glitch inside
    # one tick must not permanently stop the gait loop.
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    calls = {"n": 0}
    real_tick = engine.tick

    def flaky_tick():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated glitch")
        return real_tick()

    engine.tick = flaky_tick

    async def drive():
        task = asyncio.create_task(engine.run())
        await asyncio.sleep(0.07)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert calls["n"] >= 2
