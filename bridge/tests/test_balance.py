"""Off-hardware tests for BalanceCorrector: pure roll/pitch -> hip-angle
trim, no hardware dependency."""
from milo_bridge.gait.balance import PARAMS, correct
from milo_bridge.gait.cpg import GAIT_NEUTRAL


def test_raw_mode_returns_angles_unchanged():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=20.0, pitch_deg=10.0, mode="raw")
    assert result == angles


def test_zero_tilt_leaves_angles_unchanged():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=0.0, mode="balanced")
    assert result == angles


def test_roll_correction_opposes_left_and_right_hips_and_knees():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=15.0, pitch_deg=0.0, mode="balanced")
    for left_joint, right_joint in (("L1", "R1"), ("L2", "R2")):
        left_delta = result[left_joint] - angles[left_joint]
        right_delta = result[right_joint] - angles[right_joint]
        assert left_delta != 0
        assert right_delta != 0
        assert (left_delta > 0) != (right_delta > 0)  # opposite directions


def test_pitch_correction_opposes_front_and_rear_joints():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=15.0, mode="balanced")
    for front_joint, rear_joint in (("L1", "L3"), ("L2", "L4")):  # FL vs RL, hip and knee
        front_delta = result[front_joint] - angles[front_joint]
        rear_delta = result[rear_joint] - angles[rear_joint]
        assert front_delta != 0
        assert rear_delta != 0
        assert (front_delta > 0) != (rear_delta > 0)


def test_correction_clamped_to_mode_max():
    angles = dict(GAIT_NEUTRAL)
    huge = correct(angles, roll_deg=500.0, pitch_deg=0.0, mode="balanced")
    max_c = PARAMS["balanced"].max_correction_deg
    for joint in ("L1", "R1", "L3", "R3", "L2", "R2", "L4", "R4"):
        assert abs(huge[joint] - angles[joint]) <= max_c + 1e-6


def test_angled_mode_allows_larger_pitch_correction_than_balanced():
    angles = dict(GAIT_NEUTRAL)
    balanced = correct(angles, roll_deg=0.0, pitch_deg=90.0, mode="balanced")
    angled = correct(angles, roll_deg=0.0, pitch_deg=90.0, mode="angled")
    b_delta = abs(balanced["L1"] - angles["L1"])
    a_delta = abs(angled["L1"] - angles["L1"])
    assert a_delta > b_delta


def test_result_angles_stay_within_servo_range():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=999.0, pitch_deg=-999.0, mode="angled")
    assert all(0.0 <= a <= 180.0 for a in result.values())


def test_does_not_mutate_input_dict():
    angles = dict(GAIT_NEUTRAL)
    original = dict(angles)
    correct(angles, roll_deg=10.0, pitch_deg=10.0, mode="balanced")
    assert angles == original


def test_combined_roll_and_pitch_correction_stays_within_mode_max():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=999.0, pitch_deg=-999.0, mode="angled")
    max_c = PARAMS["angled"].max_correction_deg
    for joint in ("L1", "R1", "L3", "R3", "L2", "R2", "L4", "R4"):
        assert abs(result[joint] - angles[joint]) <= max_c + 1e-6
