"""Display screen: manual emote panel -- curated face buttons + pairing PIN."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from milo_bridge.drivers.display import AnimMode, FaceDisplay

ASSETS_DIR = Path(__file__).resolve().parents[3] / "bridge" / "assets" / "faces"

EMOTES = ("idle", "happy", "angry", "sad", "excited", "sleepy", "wave", "dance")


class DisplayScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self) -> None:
        super().__init__()
        self._display: FaceDisplay | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Button("Connect", id="connect-btn", variant="primary")
        yield VerticalScroll(id="panel-area")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "connect-btn":
            event.button.disabled = True
            self.connect()
        elif button_id == "pin-btn":
            self.show_pin()
        elif button_id.startswith("emote-"):
            self.show_emote(button_id.removeprefix("emote-"))

    @work()
    async def connect(self) -> None:
        panel = self.query_one("#panel-area", VerticalScroll)
        try:
            self._display = FaceDisplay.from_hardware(ASSETS_DIR)
        except Exception as exc:
            await panel.mount(Static(f"Could not open the OLED display: {exc}"))
            self.query_one("#connect-btn", Button).disabled = False
            return
        buttons: list[Button] = [
            Button(name, id=f"emote-{name}", classes="emote-btn") for name in EMOTES
        ]
        buttons.append(Button("Show Pairing PIN", id="pin-btn", variant="primary"))
        await panel.mount(Horizontal(*buttons, classes="emote-row"))

    @work()
    async def show_emote(self, name: str) -> None:
        if self._display is not None:
            await self._display.set_face(name, AnimMode.ONCE)

    @work()
    async def show_pin(self) -> None:
        if self._display is not None:
            await self._display.show_pin("123456")
