from datetime import datetime, timezone

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.camera import CameraScreen


def test_camera_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = CameraScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
