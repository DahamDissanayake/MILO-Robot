# common — shared protocol and pairing/auth

`milo-common` is the wire-format and security layer shared by both sides of
the project: [`bridge/`](../bridge/) (the robot) and [`brain/`](../brain/)
(the desktop app). Neither side depends on the other — they only depend on
this package, so the protocol and pairing logic live in exactly one place.

## What's in here

```
milo_common/
  protocol.py    the wire protocol: one WebSocket, multiplexed JSON control
                 frames + binary payloads (video/audio/tts), header/seq framing
  handshake.py   connection handshake for both sides (robot is always the
                 WebSocket client) — paired and first-contact flows
  auth.py        pairing (PIN-based first contact) and per-session
                 authentication (stored HKDF trust token, HMAC challenge-response)
  testing.py     test doubles shared by the common/bridge/brain test suites
```

## Install

```bash
pip install -e ./common
```

Every other package (`bridge`, `brain`) depends on `milo-common` and installs
it automatically as part of their own `pip install -e`.

## Tests

```bash
python -m pytest common/tests -v
```

No hardware, network, or GPU required — protocol framing, handshake, and auth
are all tested with in-memory fakes.

See [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for how this fits into
the full robot/brain connection lifecycle.
