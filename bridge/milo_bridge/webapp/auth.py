"""Password hashing for the dashboard login. Stdlib only (hashlib.scrypt).

Stored format is ``"<salt_hex>$<hash_hex>"`` so the salt travels with the
hash in a single config string, matching how ``~/.milo/config.json``
already stores everything else as plain JSON scalars.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16
_KEY_LEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_LEN)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=len(expected))
    return hmac.compare_digest(candidate, expected)
