from datetime import datetime, timezone
from pathlib import Path

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.display import ASSETS_DIR, DisplayScreen, discover_face_names


def test_discover_face_names_groups_numbered_frames(tmp_path: Path) -> None:
    for name in ["angry.png", "dance_1.png", "dance_2.png", "idle_blink_1.png", "idle_blink_2.png"]:
        (tmp_path / name).write_bytes(b"")
    names = discover_face_names(tmp_path)
    assert names == ["angry", "dance", "idle_blink"]


def test_discover_face_names_on_real_assets_dir() -> None:
    names = discover_face_names(ASSETS_DIR)
    assert "idle" in names
    assert "happy" in names
    assert "walk" in names


def test_display_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = DisplayScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
