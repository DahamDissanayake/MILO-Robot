"""Pairing and session authentication.

Pairing (once per robot/brain pair):
    1. Robot shows a 4-digit PIN on its OLED (short by design -- pairing
       already requires knowing the robot's IP, shown alongside it on the
       web dashboard, so the PIN only needs to guard against someone who
       already has that).
    2. User types the PIN into the brain UI.
    3. Both sides derive ``token = HKDF-SHA256(PIN, salt=robot_id||brain_id)``
       and persist it (``~/.milo/paired.json`` on the Pi,
       ``~/.milo-brain/paired.json`` on the brain).

Session handshake (every connection):
    robot -> brain: fresh random challenge nonce
    brain -> robot: HMAC-SHA256(token, nonce)
    Robot verifies with a constant-time compare; a wrong token or a replayed
    response for a stale nonce fails because every session gets a new nonce.
    The same primitive runs in reverse so the brain also authenticates the robot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

PIN_LENGTH = 4
TOKEN_BYTES = 32
NONCE_BYTES = 16


def generate_pin() -> str:
    """4-digit pairing PIN, zero-padded, from a CSPRNG."""
    return f"{secrets.randbelow(10 ** PIN_LENGTH):0{PIN_LENGTH}d}"


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = TOKEN_BYTES) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_token(pin: str, robot_id: str, brain_id: str) -> bytes:
    """Shared pairing token. Both sides must pass identical ids in the same order."""
    salt = f"{robot_id}|{brain_id}".encode()
    return _hkdf_sha256(pin.encode(), salt=salt, info=b"milo-pairing-v1")


def make_challenge() -> bytes:
    """Fresh random nonce; never reuse across sessions."""
    return secrets.token_bytes(NONCE_BYTES)


def respond(token: bytes, challenge: bytes) -> bytes:
    return hmac.new(token, challenge, hashlib.sha256).digest()


def verify(token: bytes, challenge: bytes, response: bytes) -> bool:
    return hmac.compare_digest(respond(token, challenge), response)


class PairedStore:
    """Persistent map of paired peer ids -> token + metadata, stored as JSON."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._peers: dict[str, dict] = {}
        if self.path.exists():
            self._peers = json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, peer_id: str, token: bytes, *, name: str = "", priority: int = 0) -> None:
        self._peers[peer_id] = {
            "token": token.hex(),
            "name": name,
            "priority": priority,
        }
        self._save()

    def remove(self, peer_id: str) -> None:
        self._peers.pop(peer_id, None)
        self._save()

    def token_for(self, peer_id: str) -> bytes | None:
        entry = self._peers.get(peer_id)
        return bytes.fromhex(entry["token"]) if entry else None

    def priority_for(self, peer_id: str) -> int:
        entry = self._peers.get(peer_id)
        return entry["priority"] if entry else 0

    def name_for(self, peer_id: str) -> str:
        entry = self._peers.get(peer_id)
        return entry["name"] if entry else peer_id

    def is_paired(self, peer_id: str) -> bool:
        return peer_id in self._peers

    def peer_ids(self) -> list[str]:
        return list(self._peers)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._peers, indent=2), encoding="utf-8")
        os.chmod(self.path, 0o600)
