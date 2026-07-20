"""ConnectRobotsScreen refresh/select behavior, driven headlessly via Textual's Pilot."""

from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import ListView

from milo_brain.net.discovery import RobotRecord
from milo_brain.tui.connect_robots import ConnectRobotsScreen


class FakeDiscovery:
    def __init__(self, records):
        self._records = records

    def snapshot(self):
        return self._records


class FakeConnector:
    def __init__(self, records=(), connected_robot=None, paired=()):
        self.discovery = FakeDiscovery(list(records))
        self.connected_robot = connected_robot
        self._paired = set(paired)
        self.manual_connect_requests: list[str] = []
        self.manual_ip_requests: list[tuple[str, int]] = []
        # attempt_id/last_attempt_error: same fields RobotConnectorManager
        # exposes (see net/connector.py) -- let a test simulate an attempt
        # resolving by bumping attempt_id and setting last_attempt_error, or
        # by setting connected_robot directly.
        self.attempt_id = 0
        self.last_attempt_error: tuple[int, str] | None = None

    def is_paired(self, robot_id):
        return robot_id in self._paired

    def request_manual_connect(self, robot_id):
        self.manual_connect_requests.append(robot_id)

    def request_manual_ip_connect(self, host, port=8765):
        self.manual_ip_requests.append((host, port))

    def fail_attempt(self, reason: str) -> None:
        """Simulate the connector's next attempt resolving to a HandshakeError
        or connection failure, e.g. connector.fail_attempt("robot refused: unpaired")."""
        self.attempt_id += 1
        self.last_attempt_error = (self.attempt_id, f"handshake failed: {reason}")


class _Peer:
    def __init__(self, id, name=""):
        self.id = id
        self.name = name or id


def rec(robot_id, name=None, pairing=False):
    return RobotRecord(robot_id=robot_id, name=name or robot_id, host="10.0.0.5", port=8765, pairing=pairing)


class _HostApp(App):
    def __init__(self, connector):
        super().__init__()
        self.connector = connector

    async def on_mount(self) -> None:
        await self.push_screen(ConnectRobotsScreen(self.connector))


async def _labels(app):
    list_view = app.screen.query_one("#device-list")
    return [str(item.query_one("Label").content) for item in list_view.children]


def test_refresh_populates_the_list_from_discovery():
    connector = FakeConnector(records=[rec("milo-1", "milo"), rec("milo-2", "spot")])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            return await _labels(app)

    labels = asyncio.run(scenario())
    assert len(labels) == 2
    assert "milo" in labels[0]
    assert "spot" in labels[1]


def test_selecting_an_item_requests_a_manual_connect():
    connector = FakeConnector(records=[rec("milo-1", "milo")])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert connector.manual_connect_requests == ["milo-1"]


def test_selecting_locks_the_list_until_the_attempt_resolves():
    connector = FakeConnector(records=[rec("milo-1", "milo", pairing=True)])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            list_view = app.screen.query_one("#device-list", ListView)
            return list_view.disabled

    assert asyncio.run(scenario()) is True


def test_a_second_selection_while_pending_is_ignored():
    connector = FakeConnector(records=[rec("milo-1", "milo", pairing=True)])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")  # list is disabled -- must be a no-op
            await pilot.pause()

    asyncio.run(scenario())
    assert connector.manual_connect_requests == ["milo-1"]


def test_a_failed_attempt_is_never_silent_and_re_enables_the_list():
    # Regression: a manual connect to a robot that isn't currently in
    # pairing mode used to just log a warning -- the operator saw nothing,
    # the row looked exactly as clickable as before, and there was no PIN
    # prompt either. Every attempt must now end in a visible result.
    connector = FakeConnector(records=[rec("milo-1", "milo", pairing=False)])

    def request_and_fail(robot_id):
        connector.manual_connect_requests.append(robot_id)
        connector.fail_attempt("robot refused: unpaired")

    connector.request_manual_connect = request_and_fail

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await asyncio.sleep(0.4)  # let the poll timer notice the failure
            await pilot.pause()
            list_view = app.screen.query_one("#device-list", ListView)
            return list_view.disabled, [n.message for n in app._notifications]

    disabled, messages = asyncio.run(scenario())
    assert disabled is False
    assert any("pairing mode" in m for m in messages)


def test_a_successful_connect_is_surfaced_and_re_enables_the_list():
    connector = FakeConnector(records=[rec("milo-1", "milo", pairing=True)])

    def request_and_connect(robot_id):
        connector.manual_connect_requests.append(robot_id)
        connector.connected_robot = _Peer(robot_id, "milo")

    connector.request_manual_connect = request_and_connect

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await asyncio.sleep(0.4)
            await pilot.pause()
            list_view = app.screen.query_one("#device-list", ListView)
            return list_view.disabled, [n.message for n in app._notifications]

    disabled, messages = asyncio.run(scenario())
    assert disabled is False
    assert any("Connected" in m for m in messages)


def test_wrong_pin_failure_names_the_pin_as_the_problem():
    connector = FakeConnector(records=[rec("milo-1", "milo", pairing=True)])

    def request_and_fail(robot_id):
        connector.manual_connect_requests.append(robot_id)
        connector.fail_attempt("peer error: bad_pin")

    connector.request_manual_connect = request_and_fail

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await asyncio.sleep(0.4)
            await pilot.pause()
            return [n.message for n in app._notifications]

    messages = asyncio.run(scenario())
    assert any("PIN" in m for m in messages)


def test_empty_discovery_shows_a_message_instead_of_crashing():
    connector = FakeConnector(records=[])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            label = app.screen.query_one("Label")
            return str(label.content)

    assert "No robots found" in asyncio.run(scenario())


def test_states_are_labeled_connected_paired_pairing_or_unpaired():
    connector = FakeConnector(
        records=[rec("a"), rec("b"), rec("c", "pairing-one", pairing=True), rec("d", "plain")],
        connected_robot=_Peer("a"),
        paired=["b"],
    )

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            return await _labels(app)

    labels = asyncio.run(scenario())
    assert "[connected]" in labels[0]
    assert "[paired]" in labels[1]
    assert "[pairing]" in labels[2]
    assert "[unpaired]" in labels[3]


def test_escape_returns_to_the_previous_screen():
    connector = FakeConnector(records=[])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ConnectRobotsScreen)
            await pilot.press("escape")
            await pilot.pause()
            return isinstance(app.screen, ConnectRobotsScreen)

    assert asyncio.run(scenario()) is False


def test_connect_by_ip_key_prompts_and_forwards_host_and_port():
    connector = FakeConnector(records=[])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            await pilot.click("#ip-input")
            await pilot.press(*"10.0.0.9:9000")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert connector.manual_ip_requests == [("10.0.0.9", 9000)]


def test_connect_by_ip_defaults_to_the_standard_port_when_omitted():
    connector = FakeConnector(records=[])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            await pilot.click("#ip-input")
            await pilot.press(*"10.0.0.9")
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(scenario())
    assert connector.manual_ip_requests == [("10.0.0.9", 8765)]


def test_connect_by_ip_cancelled_requests_nothing():
    connector = FakeConnector(records=[])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("i")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(scenario())
    assert connector.manual_ip_requests == []


def test_refresh_key_re_reads_the_discovery_snapshot():
    connector = FakeConnector(records=[])

    async def scenario():
        app = _HostApp(connector)
        async with app.run_test() as pilot:
            await pilot.pause()
            connector.discovery._records = [rec("milo-1", "milo")]
            await pilot.press("r")
            await pilot.pause()
            return await _labels(app)

    labels = asyncio.run(scenario())
    assert len(labels) == 1 and "milo" in labels[0]
