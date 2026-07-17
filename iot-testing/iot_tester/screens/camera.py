"""Camera screen: captures frames via CameraStreamer, saves a snapshot for inspection.

A headless Pi can't preview a JPEG in-terminal, so PASS/FAIL here is scoped to
what the screen can verify automatically: did FRAME_COUNT frames capture
without error. The README tells the tester to scp the saved snapshot down to
check framing/focus/content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.camera import CameraStreamer

from iot_tester.results_log import ResultRecorder

FRAME_COUNT = 3
RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


class CameraScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="test-area")
        yield Footer()

    def on_mount(self) -> None:
        self.run_tests()

    @work()
    async def run_tests(self) -> None:
        container = self.query_one("#test-area", VerticalScroll)
        try:
            camera = CameraStreamer.from_hardware()
        except Exception as exc:
            await container.mount(Static(f"Could not open the camera: {exc}"))
            self.recorder.record("Camera", "Frame capture", False, note=str(exc))
            self.recorder.flush()
            return

        await container.mount(Static(f"Capturing {FRAME_COUNT} frames..."))
        last_frame = b""
        captured = 0
        error_note = ""
        try:
            async for frame in camera.frames():
                if not frame:
                    raise ValueError("captured an empty frame")
                last_frame = frame
                captured += 1
                if captured >= FRAME_COUNT:
                    break
        except Exception as exc:
            error_note = str(exc)

        passed = captured >= FRAME_COUNT and not error_note
        if passed:
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            snapshot_path = RESULTS_DIR / f"camera-test-{timestamp}.jpg"
            snapshot_path.write_bytes(last_frame)
            await container.mount(
                Static(
                    f"Captured {captured}/{FRAME_COUNT} frames. Saved {snapshot_path.name} "
                    "-- scp it down to check framing/focus."
                )
            )
        else:
            await container.mount(
                Static(f"Capture failed after {captured}/{FRAME_COUNT} frames: {error_note}")
            )
        self.recorder.record("Camera", "Frame capture", passed, note=error_note)
        self.recorder.flush()
