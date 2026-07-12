"""Display screen: cycles every face asset on the OLED via FaceDisplay."""

from __future__ import annotations

import re
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from milo_bridge.drivers.display import AnimMode, FaceDisplay

from iot_tester.results_log import ResultRecorder
from iot_tester.widgets import ask_pass_fail

ASSETS_DIR = Path(__file__).resolve().parents[3] / "bridge" / "assets" / "faces"

_FRAME_SUFFIX = re.compile(r"^(.+)_(\d+)$")


def discover_face_names(assets_dir: Path) -> list[str]:
    """Distinct face names in assets_dir, grouping <name>_<n>.png sequences by stem."""
    names: set[str] = set()
    for path in sorted(Path(assets_dir).glob("*.png")):
        stem = path.stem
        match = _FRAME_SUFFIX.match(stem)
        names.add(match.group(1) if match else stem)
    return sorted(names)


class DisplayScreen(Screen):
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
            display = FaceDisplay.from_hardware(ASSETS_DIR)
        except Exception as exc:
            await container.mount(Static(f"Could not open the OLED display: {exc}"))
            return

        for name in discover_face_names(ASSETS_DIR):
            await container.mount(Static(f"Showing face: {name}"))
            await display.set_face(name, AnimMode.ONCE)
            passed, note = await ask_pass_fail(
                container, f"Did '{name}' render correctly on the OLED?"
            )
            self.recorder.record("Display", f"Face: {name}", passed, note)
            self.recorder.flush()

        await container.mount(Static("Showing pairing PIN screen"))
        await display.show_pin("123456")
        passed, note = await ask_pass_fail(container, "Did the pairing-PIN screen render legibly?")
        self.recorder.record("Display", "Pairing PIN render", passed, note)
        self.recorder.flush()

        await container.mount(Static("Display tests complete. Press Escape to return to menu."))
