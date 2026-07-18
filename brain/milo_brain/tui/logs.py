"""Logs screen: live view of the brain's own log ring buffer.

This is the only place background-task errors (a failed handshake, a
dropped/lost connection, zeroconf noise) are visible while the TUI is
running -- writing straight to stderr from a background task would
otherwise corrupt or vanish into Textual's alternate screen buffer
instead of appearing anywhere the user can actually read.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

REFRESH_INTERVAL_S = 1.0
MAX_LINES = 300


class LogsScreen(Screen):
    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, log_buffer):
        super().__init__()
        self._log_buffer = log_buffer
        self._shown_count = -1  # forces the first refresh to always render

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="log-scroll"):
            yield Static("", id="log-text")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(REFRESH_INTERVAL_S, self._refresh)

    def _refresh(self) -> None:
        lines = self._log_buffer.lines(MAX_LINES)
        if len(lines) == self._shown_count:
            return  # nothing new -- skip the render/scroll churn
        self._shown_count = len(lines)
        self.query_one("#log-text", Static).update("\n".join(lines) or "(no logs yet)")
        self.query_one("#log-scroll", VerticalScroll).scroll_end(animate=False)

    def action_back(self) -> None:
        self.app.pop_screen()
