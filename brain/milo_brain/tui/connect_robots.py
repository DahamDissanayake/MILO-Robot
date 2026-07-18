"""Connect Robots screen: refreshable list of robots discovered on the LAN
(see net/discovery.py). Selecting one just requests the connector dial it
-- the existing reactive T_PAIR_BEGIN -> request_pin() flow inside
brain_handshake() (unchanged) pops PairingPinScreen exactly as it does
today for the passive auto-reconnect path.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from ..net.connector import DEFAULT_ROBOT_PORT
from .connect_by_ip import ConnectByIpScreen


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

    async def action_refresh(self) -> None:
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
        if not self._records:
            return
        record = self._records[event.list_view.index]
        self._connector.request_manual_connect(record.robot_id)
        self.notify(f"Connecting to {record.name}…")

    def action_connect_by_ip(self) -> None:
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
