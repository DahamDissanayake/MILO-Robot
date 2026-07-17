from milo_bridge.characterize import ImuSample, MovementReport, analyze_samples


def _samples(rows):
    return [ImuSample(t=t, roll=r, pitch=p, gyro=(gx, gy, gz)) for t, r, p, gx, gy, gz in rows]


def test_peak_and_residual_are_reported():
    samples = _samples([
        (0.0, 0.0, 0.0, 0, 0, 0),
        (0.5, 10.0, -5.0, 20, 0, 0),
        (1.0, 2.0, -1.0, 5, 0, 0),
        (1.5, 0.5, -0.2, 1, 0, 0),
    ])
    report = analyze_samples("wave", samples, movement_end_s=1.0)
    assert report.name == "wave"
    assert report.peak_roll == 10.0
    assert report.peak_pitch == 5.0  # magnitude, not signed
    assert report.residual_roll == 0.5
    assert report.residual_pitch == 0.2
    assert report.peak_gyro == 20.0


def test_settle_time_is_first_point_after_movement_end_holding_below_threshold():
    samples = _samples([
        (0.0, 0.0, 0.0, 0, 0, 0),
        (1.0, 15.0, 0.0, 0, 0, 0),   # during the movement, ignored for settle
        (1.1, 8.0, 0.0, 0, 0, 0),    # still above threshold, after movement_end_s=1.0
        (1.4, 2.0, 0.0, 0, 0, 0),    # under threshold...
        (2.0, 1.0, 0.0, 0, 0, 0),    # ...and stays under for >= settle_hold_s=0.5 from t=1.4
    ])
    report = analyze_samples("bow", samples, movement_end_s=1.0, settle_threshold_deg=3.0, settle_hold_s=0.5)
    assert report.settle_time_s == 1.4 - 1.0  # 0.4s after the movement ended


def test_settle_time_is_none_when_it_never_settles():
    samples = _samples([(t, 20.0, 0.0, 0, 0, 0) for t in [0.0, 0.5, 1.0, 1.5, 2.0]])
    report = analyze_samples("dance", samples, movement_end_s=0.5)
    assert report.settle_time_s is None


def test_unsafe_when_peak_tilt_exceeds_the_ceiling():
    samples = _samples([(0.0, 50.0, 0.0, 0, 0, 0), (0.5, 0.0, 0.0, 0, 0, 0)])
    report = analyze_samples("crab", samples, movement_end_s=0.5, safety_ceiling_deg=45.0)
    assert report.safe is False


def test_safe_when_within_the_ceiling():
    samples = _samples([(0.0, 10.0, 5.0, 0, 0, 0), (0.5, 1.0, 0.5, 0, 0, 0)])
    report = analyze_samples("look_up", samples, movement_end_s=0.5, safety_ceiling_deg=45.0)
    assert report.safe is True
