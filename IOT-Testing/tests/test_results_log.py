from datetime import datetime, timezone
from pathlib import Path

from iot_tester.results_log import ResultRecorder


def test_record_and_summary(tmp_path: Path) -> None:
    recorder = ResultRecorder(tmp_path, datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc))
    recorder.record("Servo R1", "TC1 Full range sweep", True)
    recorder.record("Servo R1", "TC2 Return to zero", False, note="jitters at 180")
    assert recorder.summary() == (1, 2)


def test_all_results_preserves_order(tmp_path: Path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    recorder.record("IMU", "Gyro calibration", True)
    recorder.record("IMU", "Live tracking", True)
    results = recorder.all_results()
    assert [r.case for r in results] == ["Gyro calibration", "Live tracking"]


def test_flush_writes_log_file(tmp_path: Path) -> None:
    run_started = datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc)
    recorder = ResultRecorder(tmp_path, run_started)
    recorder.record("Servo R1", "TC1 Full range sweep", True)
    recorder.record("Servo R2", "TC1 Full range sweep", False, note="no movement")
    log_path = recorder.flush()
    assert log_path.parent == tmp_path
    assert log_path.name == "session-20260712T100000Z.log"
    text = log_path.read_text(encoding="utf-8")
    assert "Servo R1" in text
    assert "Servo R2" in text
    assert "PASS" in text
    assert "FAIL" in text
    assert "no movement" in text
    assert "1/2 test cases passed" in text


def test_flush_creates_results_dir(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    recorder = ResultRecorder(results_dir, datetime.now(timezone.utc))
    recorder.record("OLED", "Face: idle", True)
    log_path = recorder.flush()
    assert log_path.exists()
    assert results_dir.is_dir()
