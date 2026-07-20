import asyncio

import pytest

from milo_common.auth import PairedStore
from milo_bridge.config import BridgeConfig
from milo_bridge.main import _start_mcp
from milo_bridge.mcp.auth import BearerAuthMiddleware


class FakeGait:
    mode = "balanced"
    backend = "cpg"

    def set_velocity_command(self, *a):
        pass


class FakeBroker:
    owner = "none"

    def allow_brain_motion(self):
        return True


class FakeRobotServer:
    active_brain_id = None

    def __init__(self, store=None):
        self.store = store


def test_start_mcp_serves_and_is_cancellable(tmp_path):
    async def main():
        cfg = BridgeConfig(data_dir=str(tmp_path), mcp_port=0)  # port 0 -- OS picks a free one
        task = asyncio.create_task(
            _start_mcp(
                cfg, FakeGait(), runner=None, imu=None, broker=FakeBroker(), servos=None,
                display=None, audio=None, robot_server=FakeRobotServer(PairedStore(cfg.paired_path)),
            )
        )
        await asyncio.sleep(0.2)  # give uvicorn a moment to start
        assert not task.done()  # still serving, didn't crash
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())


def test_start_mcp_reuses_robot_servers_paired_store(tmp_path, monkeypatch):
    """Regression: _start_mcp used to construct its own PairedStore(cfg.paired_path),
    independent from the RobotServer's -- both read the same paired.json file into
    two separate in-memory dicts that never sync. A brain paired through the WS
    handshake (which only updates RobotServer's store) was therefore invisible to
    the MCP server's BearerAuthMiddleware forever, which 401'd every single MCP
    call and crashed the robot link on every reconnect. The MCP server must be
    wired to the exact same PairedStore instance RobotServer holds, not a fresh one."""
    captured: dict = {}
    orig_init = BearerAuthMiddleware.__init__

    def spy_init(self, app, store):
        captured["store"] = store
        orig_init(self, app, store)

    monkeypatch.setattr(BearerAuthMiddleware, "__init__", spy_init)

    shared_store = PairedStore(tmp_path / "paired.json")

    async def main():
        cfg = BridgeConfig(data_dir=str(tmp_path), mcp_port=0)
        task = asyncio.create_task(
            _start_mcp(
                cfg, FakeGait(), runner=None, imu=None, broker=FakeBroker(), servos=None,
                display=None, audio=None, robot_server=FakeRobotServer(shared_store),
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert captured["store"] is shared_store
