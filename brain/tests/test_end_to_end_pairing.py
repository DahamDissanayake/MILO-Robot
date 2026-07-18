"""Full pairing flow across both real packages, over real localhost
websockets: robot server (milo_bridge) <-> brain connector (milo_brain).

This is the actual new architecture, wired up end to end -- not a fake on
either side of the wire -- verifying the two independently-developed
halves (RobotServer's proactive PIN + RobotConnectorManager's discover/
select/connect loop) actually agree on the wire protocol.
"""

from __future__ import annotations

import asyncio

import websockets

from milo_common.auth import PairedStore

from milo_bridge.config import BridgeConfig
from milo_bridge.net.server import RobotServer

from milo_brain.config import BrainConfig
from milo_brain.net.connector import RobotConnectorManager
from milo_brain.net.discovery import RobotRecord


class NullAdvertiser:
    busy = False
    pairing = False

    def start(self):
        pass

    def update(self, **kw):
        for key, value in kw.items():
            if value is not None:
                setattr(self, key, value)

    def stop(self):
        pass


class FakeDisplay:
    def __init__(self):
        self.shown_pins: list[str] = []

    async def show_pin(self, pin):
        self.shown_pins.append(pin)

    def stop_idle(self):
        pass

    def start_idle(self):
        pass


class FakeDiscovery:
    def __init__(self, records):
        self._records = records

    def snapshot(self):
        return self._records

    def start(self):
        pass

    def stop(self):
        pass


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


def test_enter_pairing_mode_through_connect_robots_to_a_persisted_pairing_and_silent_reconnect(tmp_path):
    async def main():
        # -- robot side: a real RobotServer on an ephemeral localhost port --
        robot_cfg = BridgeConfig(
            robot_id="milo-1", robot_name="milo", data_dir=str(tmp_path / "robot"), robot_ws_port=0,
        )
        display = FakeDisplay()
        robot = RobotServer(robot_cfg, display=display, runner=None, advertiser=NullAdvertiser())
        ws_server = await websockets.serve(robot._on_connection, "127.0.0.1", 0)
        port = ws_server.sockets[0].getsockname()[1]

        try:
            # "Enter Pairing Mode" from the bridge webapp: PIN generated and
            # shown on the OLED *before* the brain has connected at all.
            pin = await robot.pairing.enter_pairing_mode()
            assert display.shown_pins == [pin]

            # -- brain side: a real RobotConnectorManager, told (as if via
            # "Connect Robots" -> refresh) about this one discovered robot --
            brain_cfg = BrainConfig(
                brain_id="brain-1", name="desk", tier="large", data_dir=str(tmp_path / "brain"),
                reconnect_seconds=0.0,
            )
            record = RobotRecord(robot_id="milo-1", name="milo", host="127.0.0.1", port=port, pairing=True)

            async def request_pin(_robot_name: str) -> str:
                return pin  # the user reading the OLED and typing it in

            handled = asyncio.Event()

            async def session_handler(sock, peer):
                handled.set()
                # End the session immediately, like a robot disconnect --
                # nothing further to exercise here.

            connector = RobotConnectorManager(
                brain_cfg, request_pin=request_pin, session_handler=session_handler,
                discovery=FakeDiscovery([record]), connect=websockets.connect,
            )
            connector.request_manual_connect("milo-1")  # the "Connect" click

            await connector._tick()
            await asyncio.wait_for(handled.wait(), timeout=5)

            # Pairing succeeded and closed the window automatically.
            assert robot.pairing.current_pin is None

            # Both sides persisted the *same* token.
            robot_store = PairedStore(robot_cfg.paired_path)
            brain_store = PairedStore(brain_cfg.paired_path)
            assert robot_store.token_for("brain-1") == brain_store.token_for("milo-1")
            assert robot_store.token_for("brain-1") is not None

            # -- a later reconnect must be silent: no PIN prompt at all,
            # even though pairing mode is now off (matches the always-on
            # advertise-for-reconnection design) --
            async def request_pin_must_not_be_called(_robot_name: str) -> str:
                raise AssertionError("a reconnect to an already-paired robot must not prompt for a PIN")

            record_not_pairing = RobotRecord(
                robot_id="milo-1", name="milo", host="127.0.0.1", port=port, pairing=False
            )
            handled2 = asyncio.Event()

            async def session_handler2(sock, peer):
                handled2.set()

            connector2 = RobotConnectorManager(
                brain_cfg, request_pin=request_pin_must_not_be_called, session_handler=session_handler2,
                discovery=FakeDiscovery([record_not_pairing]), connect=websockets.connect,
            )
            await connector2._tick()
            await asyncio.wait_for(handled2.wait(), timeout=5)
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())
