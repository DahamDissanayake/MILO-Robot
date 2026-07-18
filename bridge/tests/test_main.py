"""_optional() and _make_control_change_handler() are small,
hardware-independent pieces of main() -- test them directly with fakes
rather than exercising main() itself (the composition root, exercised only
by the real service, as today)."""
import asyncio

from milo_bridge.main import _make_control_change_handler, _optional


def test_optional_returns_value_and_true_on_success():
    value, ok = _optional(lambda: "real-driver", "widget")
    assert value == "real-driver"
    assert ok is True


def test_optional_returns_none_and_false_on_failure():
    def boom():
        raise RuntimeError("no such device")

    value, ok = _optional(boom, "widget")
    assert value is None
    assert ok is False


class FakeSleepController:
    def __init__(self):
        self.standby_calls = 0
        self.awake_calls = 0

    async def ensure_standby(self):
        self.standby_calls += 1

    async def ensure_awake(self):
        self.awake_calls += 1


async def test_control_change_handler_stands_by_when_owner_becomes_none():
    sleep_controller = FakeSleepController()
    on_change = _make_control_change_handler(sleep_controller)
    on_change("none")
    await asyncio.sleep(0)  # let the fire-and-forget task run
    assert sleep_controller.standby_calls == 1
    assert sleep_controller.awake_calls == 0


async def test_control_change_handler_wakes_for_web_or_brain_owner():
    sleep_controller = FakeSleepController()
    on_change = _make_control_change_handler(sleep_controller)
    on_change("web")
    await asyncio.sleep(0)
    assert sleep_controller.awake_calls == 1

    on_change("brain")
    await asyncio.sleep(0)
    assert sleep_controller.awake_calls == 2
    assert sleep_controller.standby_calls == 0
