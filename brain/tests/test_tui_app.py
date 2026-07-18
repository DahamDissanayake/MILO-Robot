"""MiloBrainApp wiring: connector startup, dashboard push, connect/model actions."""

from __future__ import annotations

import asyncio

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.app import MiloBrainApp
from milo_brain.logbuf import RingBufferLogHandler
from milo_brain.tui.connect_robots import ConnectRobotsScreen
from milo_brain.tui.dashboard import DashboardScreen
from milo_brain.tui.logs import LogsScreen


class FakeDiscovery:
    def snapshot(self):
        return []


class FakeConnector:
    def __init__(self):
        self.discovery = FakeDiscovery()
        self.connected_robot = None
        self.last_connected = None
        self._request_pin = None
        self._manual_targets: list[str] = []
        self.reconnect_requested = 0
        self._reconnect_result = True
        self.ran = asyncio.Event()

    def paired_ids(self):
        return []

    def is_paired(self, robot_id):
        return False

    def request_manual_connect(self, robot_id):
        self._manual_targets.append(robot_id)

    def request_reconnect(self):
        self.reconnect_requested += 1
        return self._reconnect_result

    async def run_forever(self):
        self.ran.set()
        await asyncio.Future()  # run until cancelled, like the real one


def make_app() -> tuple[MiloBrainApp, FakeConnector]:
    connector = FakeConnector()
    cfg = BrainConfig(brain_id="b", name="n", tier="small")
    app = MiloBrainApp(connector, cfg, TokenRateTracker())
    return app, connector


def test_dashboard_is_pushed_and_connector_starts_on_mount():
    async def scenario():
        app, connector = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, DashboardScreen)
            await asyncio.wait_for(connector.ran.wait(), timeout=5)

    asyncio.run(scenario())


def test_request_pin_is_wired_to_the_app_on_construction():
    app, connector = make_app()
    assert connector._request_pin == app.request_pin_from_user


def test_connect_robots_action_pushes_the_screen():
    async def scenario():
        app, connector = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_connect_robots()
            await pilot.pause()
            return isinstance(app.screen, ConnectRobotsScreen)

    assert asyncio.run(scenario()) is True


def test_reconnect_action_calls_through_to_the_connector():
    async def scenario():
        app, connector = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_reconnect()
            await pilot.pause()
            return connector.reconnect_requested

    assert asyncio.run(scenario()) == 1


def test_reconnect_action_notifies_when_nothing_to_reconnect_to():
    async def scenario():
        app, connector = make_app()
        connector._reconnect_result = False
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_reconnect()
            await pilot.pause()
            return [n.message for n in app._notifications]

    messages = asyncio.run(scenario())
    assert any("No previous connection" in m for m in messages)


def test_logs_action_pushes_the_screen():
    async def scenario():
        app, connector = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_logs()
            await pilot.pause()
            return isinstance(app.screen, LogsScreen)

    assert asyncio.run(scenario()) is True


def test_log_buffer_defaults_to_an_empty_one_when_not_provided():
    app, _connector = make_app()
    assert isinstance(app.log_buffer, RingBufferLogHandler)
    assert app.log_buffer.lines() == []


def test_provided_log_buffer_is_used_as_is():
    connector = FakeConnector()
    cfg = BrainConfig(brain_id="b", name="n", tier="small")
    log_buffer = RingBufferLogHandler()
    app = MiloBrainApp(connector, cfg, TokenRateTracker(), log_buffer)
    assert app.log_buffer is log_buffer
