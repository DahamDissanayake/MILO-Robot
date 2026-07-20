"""Connect Robots screen: refreshable list of robots discovered on the LAN
(see net/discovery.py). Selecting one locks the list and requests the
connector dial it -- the existing reactive T_PAIR_BEGIN -> request_pin()
flow inside brain_handshake() (unchanged) pops PairingPinScreen exactly as
it does today for the passive auto-reconnect path.

The list stays locked from the moment of selection until the connector
reports a definite outcome *for that attempt* -- connected, or a
HandshakeError/connection failure -- so a click can't be mistaken for a
no-op and repeated while it's still in flight, and the user always gets a
visible result instead of a silent retry loop. Attempts are correlated via
connector.attempt_id/last_attempt_error rather than the shared link_state
alone, since link_state can't tell "hasn't started yet" apart from
"already failed".
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from ..net.connector import DEFAULT_ROBOT_PORT
from .connect_by_ip import ConnectByIpScreen

ATTEMPT_POLL_INTERVAL_S = 0.3
ATTEMPT_TIMEOUT_S = 20.0


class ConnectRobotsScreen(Screen):
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("i", "connect_by_ip", "Connect by IP"),
        ("escape", "back", "Back"),
    ]

    def __init__(self, connector):
        super().__init__()
        self._connector = connector
        self._records = []
        self._pending_target: str | None = None  # robot_id of the in-flight attempt, if any
        self._pending_expected_attempt = 0
        self._pending_elapsed = 0.0
        self._poll_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Discovered robots  (r to refresh, enter to connect, "
            "i to connect by IP, esc to go back)"
        )
        yield ListView(id="device-list")
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def on_unmount(self) -> None:
        self._stop_polling()

    async def action_refresh(self) -> None:
        if self._pending_target is not None:
            return  # don't reshuffle rows (or the indices on_list_view_selected relies on) mid-attempt
        list_view = self.query_one("#device-list", ListView)
        await list_view.clear()
        self._records = list(self._connector.discovery.snapshot())
        if not self._records:
            await list_view.append(ListItem(Label("No robots found -- press r to refresh")))
            return
        connected = self._connector.connected_robot
        connected_id = connected.id if connected else None
        for record in self._records:
            if record.robot_id == connected_id:
                state = "connected"
            elif self._connector.is_paired(record.robot_id):
                state = "paired"
            elif record.pairing:
                state = "pairing"
            else:
                state = "unpaired"
            await list_view.append(ListItem(Label(f"{record.name}  [{state}]")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not self._records or self._pending_target is not None:
            return
        record = self._records[event.list_view.index]
        self._pending_target = record.robot_id
        self._pending_expected_attempt = self._connector.attempt_id + 1
        self._pending_elapsed = 0.0
        self.query_one("#device-list", ListView).disabled = True
        self._connector.request_manual_connect(record.robot_id)
        self.notify(f"Connecting to {record.name}…")
        self._poll_timer = self.set_interval(ATTEMPT_POLL_INTERVAL_S, self._poll_pending_attempt)

    def _poll_pending_attempt(self) -> None:
        target = self._pending_target
        if target is None:
            return
        connected = self._connector.connected_robot
        if connected is not None and connected.id == target:
            self._resolve_pending(success=True, message=f"Connected to {connected.name}")
            return
        error = self._connector.last_attempt_error
        if error is not None and error[0] >= self._pending_expected_attempt:
            self._resolve_pending(success=False, message=self._explain_failure(error[1]))
            return
        self._pending_elapsed += ATTEMPT_POLL_INTERVAL_S
        if self._pending_elapsed >= ATTEMPT_TIMEOUT_S:
            self._resolve_pending(success=False, message="Timed out waiting for a response")

    @staticmethod
    def _explain_failure(reason: str) -> str:
        if "unpaired" in reason:
            return 'Robot isn\'t in pairing mode -- press "Enter Pairing Mode" on its dashboard first'
        if "cancelled" in reason:
            return "Pairing cancelled"
        if "bad_pin" in reason:
            return "Wrong PIN -- pairing failed"
        if "bad_auth" in reason:
            return "Authentication failed (token mismatch) -- re-pairing may be needed"
        if "unknown_robot" in reason:
            return "This robot no longer recognizes this brain -- re-pairing needed"
        return f"Connect failed: {reason}"

    def _resolve_pending(self, *, success: bool, message: str) -> None:
        self._pending_target = None
        self._stop_polling()
        self.query_one("#device-list", ListView).disabled = False
        self.notify(message, severity="information" if success else "error")
        self.run_worker(self.action_refresh())

    def _stop_polling(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

    def action_connect_by_ip(self) -> None:
        if self._pending_target is not None:
            return
        self.app.run_worker(self._connect_by_ip())

    async def _connect_by_ip(self) -> None:
        raw = await self.app.push_screen_wait(ConnectByIpScreen())
        if not raw:
            return
        host, _, port_str = raw.partition(":")
        host = host.strip()
        if not host:
            self.notify("No IP entered", severity="error")
            return
        port = DEFAULT_ROBOT_PORT
        if port_str:
            try:
                port = int(port_str)
            except ValueError:
                self.notify(f"Invalid port {port_str!r}", severity="error")
                return
        self._connector.request_manual_ip_connect(host, port)
        self.notify(f"Connecting to {host}:{port}…")

    def action_back(self) -> None:
        self.app.pop_screen()
