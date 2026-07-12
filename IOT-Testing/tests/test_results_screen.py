from datetime import datetime, timezone

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.results import ResultsScreen


def test_results_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    recorder.record("Servo R1", "TC1 Full range sweep", True)
    screen = ResultsScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
