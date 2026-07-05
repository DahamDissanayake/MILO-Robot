import math

import numpy as np
import pytest

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
from milo_bridge.poses import STAND_ANGLES


class FakeServos:
    def __init__(self):
        self.angles: dict[str, float] = dict(STAND_ANGLES)
        self.writes = 0

    def set_angle(self, name, angle):
        self.angles[name] = angle
        self.writes += 1

    def last_angle(self, name):
        return self.angles.get(name)


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
