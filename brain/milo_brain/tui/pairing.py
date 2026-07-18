"""Modal PIN entry when a robot requests pairing."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class PairingPinScreen(ModalScreen[str | None]):
    """Shown when a robot requests pairing. Submitting the input dismisses
    with the typed PIN (or None if blank); Escape dismisses with None
    (declines pairing)."""

    DEFAULT_CSS = """
    PairingPinScreen {
        align: center middle;
    }
    #pairing-box {
        width: 50;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, robot_name: str):
        super().__init__()
        self.robot_name = robot_name

    def compose(self) -> ComposeResult:
        with Vertical(id="pairing-box"):
            yield Static(f"Robot [b]{self.robot_name}[/b] wants to pair.")
            yield Static("Enter the 4-digit PIN shown on its face:")
            yield Input(placeholder="1234", id="pin-input", max_length=4)

    def on_mount(self) -> None:
        self.query_one("#pin-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
