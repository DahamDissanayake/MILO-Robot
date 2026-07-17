"""MiloBrainApp wiring: server startup, dashboard push, pairing/model actions."""

from __future__ import annotations

import asyncio

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.app import MiloBrainApp
from milo_brain.tui.dashboard import DashboardScreen


class FakeAdvertiser:
    def __init__(self):
        self.pairing = False
        self.busy = False
        self.advertised_ip = "192.168.1.14"
        self.updates: list[dict] = []

    def start(self):
        pass

    def update(self, **kw):
        self.updates.append(kw)
        for key, value in kw.items():
            if value is not None:
                setattr(self, key, value)

    def stop(self):
        pass


class FakeServer:
    def __init__(self):
        self.advertiser = FakeAdvertiser()
        self.connected_robot = None
        self._request_pin = None
        self.served = asyncio.Event()

    async def serve_forever(self):
        self.served.set()
        await asyncio.Future()  # run until cancelled, like the real one


def make_app() -> tuple[MiloBrainApp, FakeServer]:
    server = FakeServer()
    cfg = BrainConfig(brain_id="b", name="n", tier="small")
    app = MiloBrainApp(server, cfg, TokenRateTracker())
    return app, server


def test_dashboard_is_pushed_and_server_starts_on_mount():
    async def scenario():
        app, server = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, DashboardScreen)
            await asyncio.wait_for(server.served.wait(), timeout=5)

    asyncio.run(scenario())


def test_request_pin_is_wired_to_the_app_on_construction():
    app, server = make_app()
    assert server._request_pin == app.request_pin_from_user


def test_toggle_pairing_action_flips_the_advertiser():
    async def scenario():
        app, server = make_app()
        async with app.run_test():
            assert server.advertiser.pairing is False
            await app.action_toggle_pairing()
            assert server.advertiser.pairing is True
            await app.action_toggle_pairing()
            assert server.advertiser.pairing is False

    asyncio.run(scenario())
