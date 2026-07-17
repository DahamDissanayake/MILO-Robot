import asyncio

import pytest

from milo_bridge.config import BridgeConfig
from milo_bridge.main import _start_mcp


class FakeGait:
    mode = "balanced"
    backend = "cpg"

    def set_velocity_command(self, *a):
        pass


class FakeBroker:
    owner = "none"

    def allow_brain_motion(self):
        return True


def test_start_mcp_serves_and_is_cancellable(tmp_path):
    async def main():
        cfg = BridgeConfig(data_dir=str(tmp_path), mcp_port=0)  # port 0 -- OS picks a free one
        task = asyncio.create_task(
            _start_mcp(cfg, FakeGait(), runner=None, imu=None, broker=FakeBroker(), servos=None, display=None, audio=None)
        )
        await asyncio.sleep(0.2)  # give uvicorn a moment to start
        assert not task.done()  # still serving, didn't crash
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
