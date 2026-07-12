"""Servos screen: manual jog panel for all 8 MG90S servos (0-180 degrees)."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from milo_bridge.drivers.servos import SERVO_CHANNELS, ServoDriver

ANGLES = (0, 45, 90, 135, 180)


def angle_button_id(name: str, angle: int) -> str:
    return f"angle-{name}-{angle}"


def parse_angle_button_id(button_id: str) -> tuple[str, int]:
    """'angle-R1-45' -> ('R1', 45)"""
    _, name, angle_str = button_id.split("-", 2)
    return name, int(angle_str)


class ServoScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    DEFAULT_CSS = """
    ServoScreen .servo-block {
        height: auto;
        margin-bottom: 1;
        border: round $panel;
        padding: 0 1;
    }

    ServoScreen .servo-header {
        height: auto;
    }

    ServoScreen .servo-name {
        width: 1fr;
        content-align: left middle;
    }

    ServoScreen .servo-buttons {
        height: auto;
    }

    ServoScreen .angle-btn {
        min-width: 8;
        margin-right: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._driver: ServoDriver | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Servos must be powered from the battery/5A rail, NEVER the Pi's 5V. "
            "Keep the robot on a stand, clear of obstructions.",
            classes="warning",
        )
        yield Button("Connect", id="connect-btn", variant="primary")
        yield VerticalScroll(id="panel-area")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "connect-btn":
            event.button.disabled = True
            self.connect()
        elif button_id == "relax-btn":
            if self._driver is not None:
                self._driver.relax()
        elif button_id.startswith("angle-"):
            self._set_angle_from_button(button_id)

    @work()
    async def connect(self) -> None:
        panel = self.query_one("#panel-area", VerticalScroll)
        try:
            self._driver = ServoDriver.from_hardware()
        except Exception as exc:
            await panel.mount(Static(f"Could not open the PCA9685: {exc}"))
            self.query_one("#connect-btn", Button).disabled = False
            return
        await self._build_panel(panel)

    async def _build_panel(self, panel: VerticalScroll) -> None:
        for name in SERVO_CHANNELS:
            channel = SERVO_CHANNELS[name]
            header = Horizontal(
                Static(f"{name} (channel {channel})", classes="servo-name"),
                Label("last angle: --", id=f"label-{name}"),
                classes="servo-header",
            )
            buttons = Horizontal(
                *[
                    Button(
                        f"{angle}°", id=angle_button_id(name, angle), classes="angle-btn"
                    )
                    for angle in ANGLES
                ],
                classes="servo-buttons",
            )
            await panel.mount(Vertical(header, buttons, classes="servo-block"))
        await panel.mount(Button("Relax All", id="relax-btn", variant="error"))

    def _set_angle_from_button(self, button_id: str) -> None:
        if self._driver is None:
            return
        name, angle = parse_angle_button_id(button_id)
        self._driver.set_angle(name, angle)
        self.query_one(f"#label-{name}", Label).update(f"last angle: {angle}°")
