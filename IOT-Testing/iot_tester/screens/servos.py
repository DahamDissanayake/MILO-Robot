"""Servos screen: manual jog panel for all 8 MG90S servos (0-180 degrees)."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
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
            row_widgets: list[Static | Button | Label] = [
                Static(f"{name} (channel {channel})", classes="servo-name")
            ]
            for angle in ANGLES:
                row_widgets.append(
                    Button(
                        f"{angle}°", id=angle_button_id(name, angle), classes="angle-btn"
                    )
                )
            row_widgets.append(Label("last angle: --", id=f"label-{name}"))
            await panel.mount(Horizontal(*row_widgets, classes="servo-row"))
        await panel.mount(Button("Relax All", id="relax-btn", variant="error"))

    def _set_angle_from_button(self, button_id: str) -> None:
        if self._driver is None:
            return
        name, angle = parse_angle_button_id(button_id)
        self._driver.set_angle(name, angle)
        self.query_one(f"#label-{name}", Label).update(f"last angle: {angle}°")
