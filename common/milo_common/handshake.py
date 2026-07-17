"""Connection handshake, both sides. The robot is always the WebSocket client.

Paired flow (mutual auth):
    robot -> hello {role, robot_id, name, proto}
    brain -> hello {role, brain_id, name, tier, proto}
    robot -> challenge {nonce}                       # robot authenticates brain
    brain -> auth {response, nonce}                  # + brain's own challenge
    robot -> auth {response}                         # robot proves itself
    brain -> auth_ok {}

Pairing flow (first contact; trust anchor = PIN on the robot's OLED):
    robot -> pair_begin {nonce}                      # robot shows PIN on its face
    brain -> pair_pin {response}                     # HMAC(token_from_typed_pin, nonce)
    robot -> auth_ok {}          (PIN correct: both sides persist the token)
    robot -> error {code:"bad_pin"} + disconnect  (PIN wrong)

The PIN itself never crosses the network — only an HMAC proof of it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from . import auth, protocol
from .auth import PairedStore
from .protocol import MiloSocket, PROTOCOL_VERSION


class HandshakeError(Exception):
    """Authentication or pairing failed; the connection must be closed."""


@dataclass(frozen=True)
class Peer:
    """The authenticated party on the other end of the socket."""

    id: str
    name: str
    tier: str = ""
    mcp_port: int = 0   # the robot's MCP server port, carried in its T_HELLO
    mcp_url: str = ""   # computed brain-side from mcp_port + the connection's remote address (see server.py)


async def _expect(sock: MiloSocket, expected_type: str) -> protocol.Message:
    msg = await sock.recv()
    if msg.t == protocol.T_ERROR:
        raise HandshakeError(f"peer error: {msg.get('code')}")
    if msg.t != expected_type:
        raise HandshakeError(f"expected {expected_type!r}, got {msg.t!r}")
    return msg


async def robot_handshake(
    sock: MiloSocket,
    robot_id: str,
    robot_name: str,
    store: PairedStore,
    show_pin: Callable[[str], Awaitable[None]] | None = None,
    mcp_port: int = 0,
) -> Peer:
    """Run the robot side. ``show_pin`` renders the pairing PIN on the OLED;
    without it, unpaired brains are refused outright. ``mcp_port`` is this
    robot's movement/face/speech/IMU MCP server port, advertised to the
    brain so it can reach it without a second discovery mechanism."""
    await sock.send(
        protocol.T_HELLO, role="robot", robot_id=robot_id, name=robot_name,
        proto=PROTOCOL_VERSION, mcp_port=mcp_port,
    )
    hello = await _expect(sock, protocol.T_HELLO)
    if hello.get("proto") != PROTOCOL_VERSION:
        raise HandshakeError(f"protocol version mismatch: {hello.get('proto')}")
    peer = Peer(id=hello["brain_id"], name=hello.get("name", ""), tier=hello.get("tier", ""))

    token = store.token_for(peer.id)
    if token is None:
        if show_pin is None:
            await sock.send(protocol.T_ERROR, code="unpaired")
            raise HandshakeError(f"brain {peer.id} is not paired")
        return await _robot_pairing(sock, robot_id, peer, store, show_pin)

    # Authenticate the brain.
    nonce = auth.make_challenge()
    await sock.send(protocol.T_CHALLENGE, nonce=nonce.hex())
    reply = await _expect(sock, protocol.T_AUTH)
    if not auth.verify(token, nonce, bytes.fromhex(reply["response"])):
        await sock.send(protocol.T_ERROR, code="bad_auth")
        raise HandshakeError(f"brain {peer.id} failed authentication")
    # Prove ourselves to the brain.
    await sock.send(
        protocol.T_AUTH, response=auth.respond(token, bytes.fromhex(reply["nonce"])).hex()
    )
    await _expect(sock, protocol.T_AUTH_OK)
    return peer


async def _robot_pairing(
    sock: MiloSocket,
    robot_id: str,
    peer: Peer,
    store: PairedStore,
    show_pin: Callable[[str], Awaitable[None]],
) -> Peer:
    pin = auth.generate_pin()
    expected_token = auth.derive_token(pin, robot_id, peer.id)
    await show_pin(pin)
    nonce = auth.make_challenge()
    await sock.send(protocol.T_PAIR_BEGIN, nonce=nonce.hex())
    reply = await _expect(sock, protocol.T_PAIR_PIN)
    if not auth.verify(expected_token, nonce, bytes.fromhex(reply["response"])):
        await sock.send(protocol.T_ERROR, code="bad_pin")
        raise HandshakeError("pairing failed: wrong PIN")
    store.add(peer.id, expected_token, name=peer.name)
    await sock.send(protocol.T_AUTH_OK)
    return peer


async def brain_handshake(
    sock: MiloSocket,
    brain_id: str,
    brain_name: str,
    tier: str,
    store: PairedStore,
    request_pin: Callable[[str], Awaitable[str | None]] | None = None,
) -> Peer:
    """Run the brain (server) side. ``request_pin(robot_name)`` asks the user to
    type the PIN shown on the robot; None/absent declines pairing."""
    hello = await _expect(sock, protocol.T_HELLO)
    peer = Peer(id=hello["robot_id"], name=hello.get("name", ""), mcp_port=hello.get("mcp_port", 0))
    await sock.send(
        protocol.T_HELLO,
        role="brain",
        brain_id=brain_id,
        name=brain_name,
        tier=tier,
        proto=PROTOCOL_VERSION,
    )

    first = await sock.recv()
    if first.t == protocol.T_ERROR:
        raise HandshakeError(f"robot refused: {first.get('code')}")

    if first.t == protocol.T_PAIR_BEGIN:
        if request_pin is None:
            await sock.send(protocol.T_ERROR, code="pairing_disabled")
            raise HandshakeError("robot wants to pair but pairing is disabled")
        pin = await request_pin(peer.name or peer.id)
        if not pin:
            await sock.send(protocol.T_ERROR, code="pairing_cancelled")
            raise HandshakeError("user cancelled pairing")
        token = auth.derive_token(pin, peer.id, brain_id)
        response = auth.respond(token, bytes.fromhex(first["nonce"]))
        await sock.send(protocol.T_PAIR_PIN, response=response.hex())
        await _expect(sock, protocol.T_AUTH_OK)
        store.add(peer.id, token, name=peer.name)
        return peer

    if first.t != protocol.T_CHALLENGE:
        raise HandshakeError(f"expected challenge or pair_begin, got {first.t!r}")
    token = store.token_for(peer.id)
    if token is None:
        await sock.send(protocol.T_ERROR, code="unknown_robot")
        raise HandshakeError(f"robot {peer.id} not in paired store")
    # Answer the robot's challenge and issue our own.
    my_nonce = auth.make_challenge()
    await sock.send(
        protocol.T_AUTH,
        response=auth.respond(token, bytes.fromhex(first["nonce"])).hex(),
        nonce=my_nonce.hex(),
    )
    reply = await _expect(sock, protocol.T_AUTH)
    if not auth.verify(token, my_nonce, bytes.fromhex(reply["response"])):
        await sock.send(protocol.T_ERROR, code="bad_auth")
        raise HandshakeError(f"robot {peer.id} failed authentication")
    await sock.send(protocol.T_AUTH_OK)
    return peer
