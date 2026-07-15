"""_optional() is a small, hardware-independent function -- test it
directly with fake factories rather than exercising main() itself (the
composition root, exercised only by the real service, as today)."""
from milo_bridge.main import _optional


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
