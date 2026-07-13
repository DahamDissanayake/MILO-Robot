from milo_bridge.webapp.session_auth import LoginThrottle, SessionStore


def test_session_create_and_validate():
    store = SessionStore()
    token = store.create("dama")
    assert store.is_valid(token)
    assert not store.is_valid("not-a-real-token")


def test_session_revoke():
    store = SessionStore()
    token = store.create("dama")
    store.revoke(token)
    assert not store.is_valid(token)


def test_session_tokens_are_unique():
    store = SessionStore()
    a = store.create("dama")
    b = store.create("dama")
    assert a != b


def test_throttle_allows_under_the_limit():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(4):
        assert throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")


def test_throttle_blocks_at_five_failures_within_60s():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(5):
        assert throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert not throttle.allow("1.2.3.4")


def test_throttle_unblocks_after_cooldown():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(5):
        throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert not throttle.allow("1.2.3.4")
    clock[0] = 31.0  # past the 30s cooldown
    assert throttle.allow("1.2.3.4")


def test_throttle_is_per_ip():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(5):
        throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert not throttle.allow("1.2.3.4")
    assert throttle.allow("5.6.7.8")


def test_throttle_success_clears_failure_count():
    clock = [0.0]
    throttle = LoginThrottle(now=lambda: clock[0])
    for _ in range(4):
        throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    throttle.record_success("1.2.3.4")
    for _ in range(4):
        assert throttle.allow("1.2.3.4")
        throttle.record_failure("1.2.3.4")
