from milo_bridge.webapp.control import ControlBroker


def test_owner_none_then_brain():
    b = ControlBroker()
    assert b.owner == "none"
    b.set_brain_connected(True)
    assert b.owner == "brain"
    assert b.allow_brain_motion() is True


def test_web_acquire_and_exclusivity():
    b = ControlBroker()
    b.set_brain_connected(True)
    assert b.acquire_web("c1") is True
    assert b.owner == "web"
    assert b.allow_brain_motion() is False
    assert b.acquire_web("c2") is False          # second client denied
    assert b.is_web_controller("c1") is True
    assert b.is_web_controller("c2") is False
    b.release_web("c1")
    assert b.owner == "brain"
    assert b.allow_brain_motion() is True


def test_release_by_non_owner_is_noop():
    b = ControlBroker()
    b.acquire_web("c1")
    b.release_web("c2")
    assert b.owner == "web"


def test_heartbeat_timeout_releases():
    b = ControlBroker(timeout_s=10.0)
    b.acquire_web("c1")
    b.heartbeat("c1")
    t0 = b._last_hb
    assert b.expire(now=t0 + 9.0) is False
    assert b.owner == "web"
    assert b.expire(now=t0 + 10.1) is True
    assert b.owner == "none"


def test_on_change_fires_on_transitions():
    seen = []
    b = ControlBroker(on_change=seen.append)
    b.set_brain_connected(True)   # none -> brain
    b.acquire_web("c1")           # brain -> web
    b.acquire_web("c1")           # re-acquire by same owner: no event
    b.release_web("c1")           # web -> brain
    assert seen == ["brain", "web", "brain"]
