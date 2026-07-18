"""MiloBrainApp: the TUI's Textual App -- owns the RobotConnectorManager,
runs it as a background worker on the app's own event loop, and wires
pairing-PIN requests to a modal screen. Pairing is now robot-initiated (via
the bridge webapp's "Enter Pairing Mode" button), so there's no local
pairing toggle here -- "c" opens Connect Robots instead, where the
reactive PIN prompt still surfaces exactly as it always has."""

from __future__ import annotations

from textual.app import App

from ..config import BrainConfig
from ..llm.token_rate import TokenRateTracker
from ..net.connector import RobotConnectorManager
from .connect_robots import ConnectRobotsScreen
from .dashboard import DashboardScreen
from .model_picker import ModelPickerScreen
from .pairing import PairingPinScreen

REFRESH_INTERVAL_S = 1.0


class MiloBrainApp(App):
    TITLE = "MILO"
    SUB_TITLE = "Brain"
    BINDINGS = [
        ("c", "connect_robots", "Connect Robots"),
        ("m", "pick_model", "Model"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, connector: RobotConnectorManager, cfg: BrainConfig, rate_tracker: TokenRateTracker):
        super().__init__()
        self.connector = connector
        self.cfg = cfg
        self.rate_tracker = rate_tracker
        # Same pattern the old tray UI used (server._request_pin = ...),
        # just pointed at a modal screen instead of a QInputDialog.
        self.connector._request_pin = self.request_pin_from_user

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())
        self.run_worker(self.connector.run_forever(), name="robot-connector")
        self.set_interval(REFRESH_INTERVAL_S, self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.refresh_from(self.connector, self.cfg, self.rate_tracker)

    def _dashboard(self) -> DashboardScreen | None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    async def request_pin_from_user(self, robot_name: str) -> str | None:
        return await self.push_screen_wait(PairingPinScreen(robot_name))

    def action_connect_robots(self) -> None:
        self.push_screen(ConnectRobotsScreen(self.connector))

    def action_pick_model(self) -> None:
        self.run_worker(self._pick_model())

    async def _pick_model(self) -> None:
        chosen = await self.push_screen_wait(ModelPickerScreen(self.cfg.ollama_url))
        if chosen:
            self.cfg.llm_model = chosen
            self.cfg.save()
