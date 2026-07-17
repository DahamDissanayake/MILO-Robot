from datetime import datetime, timezone

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.imu import ImuScreen, format_readout


def test_format_readout_includes_all_values() -> None:
    text = format_readout(1.5, -2.5, (0.1, 0.2, 0.3))
    assert "1.5" in text
    assert "-2.5" in text
    assert "0.1" in text
    assert "0.2" in text
    assert "0.3" in text


def test_imu_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = ImuScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
