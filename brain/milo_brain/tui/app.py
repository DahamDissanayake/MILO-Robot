"""MiloBrainApp: the TUI's Textual App -- owns the BrainServer, runs it as a
background worker on the app's own event loop (no separate thread, unlike
the old tray UI), and wires pairing-PIN requests to a modal screen."""

from __future__ import annotations

import asyncio

from textual.app import App

from ..config import BrainConfig
from ..llm.token_rate import TokenRateTracker
from ..server import BrainServer
from .dashboard import DashboardScreen
from .model_picker import ModelPickerScreen
from .pairing import PairingPinScreen

REFRESH_INTERVAL_S = 1.0


class MiloBrainApp(App):
    TITLE = "MILO"
    SUB_TITLE = "Brain"
    BINDINGS = [
        ("p", "toggle_pairing", "Pairing"),
        ("m", "pick_model", "Model"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, server: BrainServer, cfg: BrainConfig, rate_tracker: TokenRateTracker):
        super().__init__()
        self.server = server
        self.cfg = cfg
        self.rate_tracker = rate_tracker
        # Same pattern the tray UI used (server._request_pin = ...), just
        # pointed at a modal screen instead of a QInputDialog.
        self.server._request_pin = self.request_pin_from_user

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())
        self.run_worker(self.server.serve_forever(), name="brain-server")
        self.set_interval(REFRESH_INTERVAL_S, self._refresh_dashboard)

    def _refresh_dashboard(self) -> None:
        dashboard = self._dashboard()
        if dashboard is not None:
            dashboard.refresh_from(self.server, self.cfg, self.rate_tracker)

    def _dashboard(self) -> DashboardScreen | None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    async def request_pin_from_user(self, robot_name: str) -> str | None:
        return await self.push_screen_wait(PairingPinScreen(robot_name))

    async def action_toggle_pairing(self) -> None:
        # Advertiser.update() is zeroconf's synchronous API -- calling it
        # directly here (this coroutine's own loop thread, same as
        # BrainServer.serve_forever()) would deadlock exactly like the bug
        # fixed in Advertiser.start/stop. Same fix: hop to a worker thread.
        await asyncio.to_thread(
            self.server.advertiser.update, pairing=not self.server.advertiser.pairing
        )

    def action_pick_model(self) -> None:
        self.run_worker(self._pick_model())

    async def _pick_model(self) -> None:
        chosen = await self.push_screen_wait(ModelPickerScreen(self.cfg.ollama_url))
        if chosen:
            self.cfg.llm_model = chosen
            self.cfg.save()
