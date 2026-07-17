"""DashboardScreen.refresh_from renders identity/connection/model/pairing panels."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.dashboard import (
    ConnectionPanel,
    DashboardScreen,
    IdentityPanel,
    ModelPanel,
    PairingPanel,
)


class _FakeAdvertiser:
    def __init__(self):
        self.pairing = False
        self.advertised_ip = "192.168.1.14"


class _FakePeer:
    def __init__(self, name):
        self.name = name


class _FakeServer:
    def __init__(self, connected_robot=None, pairing=False):
        self.advertiser = _FakeAdvertiser()
        self.advertiser.pairing = pairing
        self.connected_robot = connected_robot


class _HostApp(App):
    def compose(self) -> ComposeResult:
        yield DashboardScreen()


def test_refresh_from_renders_all_four_panels():
    async def scenario():
        cfg = BrainConfig(
            brain_id="brain-abc", name="my-laptop", tier="small", gpu="RTX 4050",
            port=8765, llm_model="llama3.2:3b", whisper_model="small", piper_voice="en_US-lessac-medium",
        )
        server = _FakeServer(connected_robot=_FakePeer("milo-1"), pairing=True)
        tracker = TokenRateTracker()
        tracker.record_prompt_eval(100, 200_000_000)  # 500 tok/s

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(server, cfg, tracker)

            identity = str(screen.query_one(IdentityPanel).content)
            connection = str(screen.query_one(ConnectionPanel).content)
            model = str(screen.query_one(ModelPanel).content)
            pairing = str(screen.query_one(PairingPanel).content)

            assert "my-laptop" in identity and "brain-abc" in identity and "RTX 4050" in identity
            assert "8765" in connection and "192.168.1.14" in connection and "milo-1" in connection
            assert "llama3.2:3b" in model and "500.0" in model
            assert "ON" in pairing

    asyncio.run(scenario())


def test_refresh_from_shows_no_robot_connected():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        server = _FakeServer(connected_robot=None, pairing=False)
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(server, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            pairing = str(screen.query_one(PairingPanel).content)
            assert "no robot connected" in connection
            assert "OFF" in pairing

    asyncio.run(scenario())
