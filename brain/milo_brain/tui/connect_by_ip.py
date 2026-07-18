"""Modal IP entry: connect to a robot directly, bypassing mDNS discovery.

For networks where multicast doesn't reach between devices (some routers
don't forward it between WiFi clients) but plain unicast still works --
the robot's IP is shown on its own web dashboard's Brain card while
pairing mode is on.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class ConnectByIpScreen(ModalScreen[str | None]):
    """Submitting the input dismisses with the typed text (host, or
    host:port); Escape or blank input dismisses with None."""

    DEFAULT_CSS = """
    ConnectByIpScreen {
        align: center middle;
    }
    #ip-box {
        width: 50;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="ip-box"):
            yield Static("Connect to a robot by IP (bypasses discovery):")
            yield Static("Find it on the robot's web dashboard's Brain card.")
            yield Input(placeholder="192.168.1.15 or 192.168.1.15:8765", id="ip-input")

    def on_mount(self) -> None:
        self.query_one("#ip-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
