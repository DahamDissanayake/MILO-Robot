from pathlib import Path

from milo_common import auth


def test_pin_is_six_digits():
    for _ in range(50):
        pin = auth.generate_pin()
        assert len(pin) == 6 and pin.isdigit()


def test_token_derivation_is_deterministic_and_id_bound():
    t1 = auth.derive_token("123456", "robot-a", "brain-a")
    t2 = auth.derive_token("123456", "robot-a", "brain-a")
    assert t1 == t2 and len(t1) == auth.TOKEN_BYTES
    assert auth.derive_token("123456", "robot-a", "brain-b") != t1
    assert auth.derive_token("654321", "robot-a", "brain-a") != t1


def test_challenge_response_roundtrip():
    token = auth.derive_token("000042", "r", "b")
    challenge = auth.make_challenge()
    response = auth.respond(token, challenge)
    assert auth.verify(token, challenge, response)


def test_wrong_token_refused():
    good = auth.derive_token("111111", "r", "b")
    bad = auth.derive_token("222222", "r", "b")
    challenge = auth.make_challenge()
    assert not auth.verify(good, challenge, auth.respond(bad, challenge))


def test_replayed_response_refused_on_new_session():
    token = auth.derive_token("111111", "r", "b")
    old_challenge = auth.make_challenge()
    replayed = auth.respond(token, old_challenge)
    new_challenge = auth.make_challenge()
    assert not auth.verify(token, new_challenge, replayed)


def test_paired_store_roundtrip(tmp_path: Path):
    store = auth.PairedStore(tmp_path / "paired.json")
    token = auth.derive_token("123123", "r", "b")
    store.add("brain-1", token, name="laptop", priority=2)

    reloaded = auth.PairedStore(tmp_path / "paired.json")
    assert reloaded.is_paired("brain-1")
    assert reloaded.token_for("brain-1") == token
    assert reloaded.priority_for("brain-1") == 2
    assert not reloaded.is_paired("brain-2")

    reloaded.remove("brain-1")
    assert not auth.PairedStore(tmp_path / "paired.json").is_paired("brain-1")
