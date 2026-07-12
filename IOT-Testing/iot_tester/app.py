"""Milo IOT-Testing: TUI hardware tester. Entry point: milo-iot-tester."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.camera import CameraScreen
from iot_tester.screens.display import DisplayScreen
from iot_tester.screens.i2c_scan import I2cScanScreen
from iot_tester.screens.imu import ImuScreen
from iot_tester.screens.microphones import MicScreen
from iot_tester.screens.results import ResultsScreen
from iot_tester.screens.servos import ServoScreen
from iot_tester.screens.speaker import SpeakerScreen
from iot_tester.screens.wiring import WiringScreen

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

MENU_ITEMS = [
    ("wiring", "Wiring Reference"),
    ("i2c", "I2C Bus Scan"),
    ("servos", "Servos"),
    ("display", "Display"),
    ("imu", "IMU"),
    ("camera", "Camera"),
    ("mics", "Microphones"),
    ("speaker", "Speaker"),
    ("results", "Results"),
    ("quit", "Quit"),
]


class MainMenu(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(
            *[ListItem(Label(label), id=f"menu-{key}") for key, label in MENU_ITEMS],
            id="main-menu",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        assert event.item.id is not None
        key = event.item.id.removeprefix("menu-")
        app = self.app
        assert isinstance(app, IotTesterApp)
        if key == "wiring":
            app.push_screen(WiringScreen())
        elif key == "i2c":
            app.push_screen(I2cScanScreen(app.recorder))
        elif key == "servos":
            app.push_screen(ServoScreen(app.recorder))
        elif key == "display":
            app.push_screen(DisplayScreen(app.recorder))
        elif key == "imu":
            app.push_screen(ImuScreen(app.recorder))
        elif key == "camera":
            app.push_screen(CameraScreen(app.recorder))
        elif key == "mics":
            app.push_screen(MicScreen(app.recorder))
        elif key == "speaker":
            app.push_screen(SpeakerScreen(app.recorder))
        elif key == "results":
            app.push_screen(ResultsScreen(app.recorder))
        elif key == "quit":
            app.exit()


class IotTesterApp(App):
    TITLE = "MILO IOT-Testing"

    def __init__(self) -> None:
        super().__init__()
        self.recorder = ResultRecorder(RESULTS_DIR, datetime.now(timezone.utc))

    def on_mount(self) -> None:
        self.push_screen(MainMenu())


def main() -> None:
    IotTesterApp().run()


if __name__ == "__main__":
    main()
