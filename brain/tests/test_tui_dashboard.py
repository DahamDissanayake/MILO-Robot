"""DashboardScreen.refresh_from renders identity/connection/model panels."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.dashboard import ConnectionPanel, DashboardScreen, IdentityPanel, ModelPanel


class _FakePeer:
    def __init__(self, name):
        self.name = name


class _FakeConnector:
    def __init__(self, connected_robot=None, paired=(), last_connected=None):
        self.connected_robot = connected_robot
        self._paired = list(paired)
        self.last_connected = last_connected

    def paired_ids(self):
        return self._paired


class _HostApp(App):
    def compose(self) -> ComposeResult:
        yield DashboardScreen()


def test_refresh_from_renders_all_three_panels():
    async def scenario():
        cfg = BrainConfig(
            brain_id="brain-abc", name="my-laptop", tier="small", gpu="RTX 4050",
            llm_model="llama3.2:3b", whisper_model="small", piper_voice="en_US-lessac-medium",
        )
        connector = _FakeConnector(connected_robot=_FakePeer("milo-1"), paired=["milo-1"])
        tracker = TokenRateTracker()
        tracker.record_prompt_eval(100, 200_000_000)  # 500 tok/s

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, tracker)

            identity = str(screen.query_one(IdentityPanel).content)
            connection = str(screen.query_one(ConnectionPanel).content)
            model = str(screen.query_one(ModelPanel).content)

            assert "my-laptop" in identity and "brain-abc" in identity and "RTX 4050" in identity
            assert "milo-1" in connection and "1" in connection  # paired count
            assert "llama3.2:3b" in model and "500.0" in model

    asyncio.run(scenario())


def test_refresh_from_shows_no_robot_connected():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(connected_robot=None, paired=[])
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "no robot connected" in connection

    asyncio.run(scenario())


def test_refresh_from_hints_reconnect_when_a_previous_target_is_known():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(connected_robot=None, paired=[], last_connected=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "10.0.0.9:8765" in connection
            assert "r to reconnect" in connection

    asyncio.run(scenario())


def test_refresh_from_omits_reconnect_hint_once_actually_connected():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(
            connected_robot=_FakePeer("milo-1"), paired=["milo-1"], last_connected=("10.0.0.9", 8765),
        )
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "10.0.0.9" not in connection

    asyncio.run(scenario())
