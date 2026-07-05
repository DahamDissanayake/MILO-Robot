import asyncio
from pathlib import Path

import pytest

from milo_common import auth
from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, brain_handshake, robot_handshake
from milo_common.testing import socket_pair

ROBOT_ID, BRAIN_ID = "milo-abc", "brain-xyz"


def stores(tmp_path: Path, paired: bool) -> tuple[PairedStore, PairedStore]:
    robot_store = PairedStore(tmp_path / "robot.json")
    brain_store = PairedStore(tmp_path / "brain.json")
    if paired:
        token = auth.derive_token("123456", ROBOT_ID, BRAIN_ID)
        robot_store.add(BRAIN_ID, token, name="desk")
        brain_store.add(ROBOT_ID, token, name="milo")
    return robot_store, brain_store


def run_both(robot_coro, brain_coro):
    async def main():
        return await asyncio.gather(robot_coro, brain_coro, return_exceptions=True)

    return asyncio.run(main())


def test_paired_mutual_auth_succeeds(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=True)
    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store),
    )
    assert not isinstance(robot_result, Exception), robot_result
    assert not isinstance(brain_result, Exception), brain_result
    assert robot_result.id == BRAIN_ID and robot_result.tier == "large"
    assert brain_result.id == ROBOT_ID


def test_brain_with_wrong_token_refused(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=True)
    brain_store.add(ROBOT_ID, auth.derive_token("999999", ROBOT_ID, BRAIN_ID))
    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store),
    )
    assert isinstance(robot_result, HandshakeError)
    assert isinstance(brain_result, HandshakeError)


def test_unpaired_brain_refused_when_pairing_unavailable(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=False)
    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store, show_pin=None),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store),
    )
    assert isinstance(robot_result, HandshakeError)
    assert isinstance(brain_result, HandshakeError)


def test_pairing_with_correct_pin(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=False)
    shown: list[str] = []

    async def show_pin(pin: str):
        shown.append(pin)

    async def request_pin(robot_name: str):
        return shown[0]  # "user" reads the OLED and types it in

    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store, show_pin=show_pin),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store, request_pin=request_pin),
    )
    assert not isinstance(robot_result, Exception), robot_result
    assert not isinstance(brain_result, Exception), brain_result
    # Both sides persisted the same token; future sessions authenticate.
    assert robot_store.token_for(BRAIN_ID) == brain_store.token_for(ROBOT_ID)
    assert robot_store.token_for(BRAIN_ID) is not None


def test_pairing_with_wrong_pin_refused(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=False)

    async def show_pin(pin: str):
        pass

    async def request_pin(robot_name: str):
        return "000000"  # user typo (worst case: guessing attacker)

    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store, show_pin=show_pin),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store, request_pin=request_pin),
    )
    assert isinstance(robot_result, HandshakeError)
    assert isinstance(brain_result, HandshakeError)
    assert not robot_store.is_paired(BRAIN_ID)


def test_pairing_cancelled_by_user(tmp_path):
    robot_store, brain_store = stores(tmp_path, paired=False)

    async def show_pin(pin: str):
        pass

    async def request_pin(robot_name: str):
        return None

    rs, bs = socket_pair()
    robot_result, brain_result = run_both(
        robot_handshake(rs, ROBOT_ID, "milo", robot_store, show_pin=show_pin),
        brain_handshake(bs, BRAIN_ID, "desk", "large", brain_store, request_pin=request_pin),
    )
    assert isinstance(robot_result, HandshakeError)
    assert isinstance(brain_result, HandshakeError)


def test_replayed_auth_fails_across_sessions(tmp_path):
    """Capture the brain's auth response in session 1, replay it in session 2."""
    robot_store, brain_store = stores(tmp_path, paired=True)
    token = brain_store.token_for(ROBOT_ID)

    async def main():
        # Session 1: sniff a valid response by acting as a passive relay.
        from milo_common import protocol

        rs, bs = socket_pair()
        captured: dict = {}

        async def sniffing_brain():
            hello = await bs.recv()
            await bs.send(
                protocol.T_HELLO, role="brain", brain_id=BRAIN_ID, name="d", tier="l",
                proto=protocol.PROTOCOL_VERSION,
            )
            challenge = await bs.recv()
            from milo_common.auth import respond, make_challenge

            response = respond(token, bytes.fromhex(challenge["nonce"]))
            captured["response"] = response.hex()
            await bs.send(protocol.T_AUTH, response=response.hex(), nonce=make_challenge().hex())
            reply = await bs.recv()
            await bs.send(protocol.T_AUTH_OK)

        r1 = asyncio.create_task(robot_handshake(rs, ROBOT_ID, "milo", robot_store))
        await sniffing_brain()
        await r1

        # Session 2: a fresh handshake gets a fresh nonce -> the replay fails.
        rs2, bs2 = socket_pair()

        async def replaying_brain():
            await bs2.recv()
            await bs2.send(
                protocol.T_HELLO, role="brain", brain_id=BRAIN_ID, name="d", tier="l",
                proto=protocol.PROTOCOL_VERSION,
            )
            await bs2.recv()  # new challenge, but we replay the old response
            await bs2.send(protocol.T_AUTH, response=captured["response"], nonce="00" * 16)

        r2 = asyncio.create_task(robot_handshake(rs2, ROBOT_ID, "milo", robot_store))
        await replaying_brain()
        with pytest.raises(HandshakeError):
            await r2

    asyncio.run(main())
