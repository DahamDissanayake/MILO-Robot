from milo_bridge.webapp.auth import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    stored = hash_password("MILO@gate")
    assert verify_password("MILO@gate", stored)


def test_wrong_password_rejected():
    stored = hash_password("MILO@gate")
    assert not verify_password("wrong", stored)


def test_same_password_hashes_differently_each_time():
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b  # random salt per call
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


def test_hash_format_is_salt_dollar_hash():
    stored = hash_password("x")
    assert stored.count("$") == 1
    salt_hex, hash_hex = stored.split("$")
    assert all(c in "0123456789abcdef" for c in salt_hex)
    assert all(c in "0123456789abcdef" for c in hash_hex)


def test_verify_rejects_malformed_stored_value():
    assert not verify_password("anything", "not-a-valid-stored-hash")
    assert not verify_password("anything", "")
