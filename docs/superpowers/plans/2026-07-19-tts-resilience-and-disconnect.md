# TTS Resilience + Disconnect Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Piper TTS self-heal (auto-download voice) and degrade gracefully instead of crashing every reply; add a brain-side "disconnect from robot" control; add a webapp "disconnect this brain" (kick) control on the robot.

**Architecture:** Three independent surfaces. (1) `brain/milo_brain/pipelines/tts.py` — `PiperTts` downloads its voice if missing and stays silent (logged once) if it can't load. (2) `brain/milo_brain/net/connector.py` + `tui/` — the connector gains a manual-disconnect flag that closes the live socket and idles the reconnect loop. (3) `bridge/milo_bridge/net/server.py` + `webapp/` — `RobotServer` can close a specific brain's socket, wired through the webapp Brain card.

**Tech Stack:** Python 3.14, Textual (brain TUI), aiohttp + vanilla JS (bridge webapp), piper-tts, pytest + pytest-asyncio.

## Global Constraints

- `LazyLoad.status` values remain exactly: `"not_loaded" | "loading" | "ready" | "error"` (do not add new ones).
- `link_state` values on `RobotConnectorManager`: this plan adds `"disconnected"` as a distinct value alongside the existing `"idle" | "connecting" | "handshaking" | "connected" | "retrying"`. `"disconnected"` means a *manual* disconnect; `"idle"` means nothing was discovered/selected.
- No unit test performs a real network download or real model load — Piper download/load are injected fakes in the suite. Exactly one real voice download is done as a manual verification step in Task 1.
- No change to the existing "Make Active" active-brain switch, the pairing flow, or the handshake.
- Brain tasks: run `python -m pytest` from `brain/` (baseline 148). Bridge tasks: run from `bridge/` (baseline 392). Never scope down below the full package suite.
- Commit messages: no AI co-author trailer (project convention).

---

### Task 1: PiperTts auto-download + graceful degrade

**Files:**
- Modify: `brain/milo_brain/pipelines/tts.py`
- Modify: `brain/milo_brain/session.py` (factory passes the voices dir)
- Test: `brain/tests/test_pipelines.py`

**Interfaces:**
- Produces: `PiperTts(voice="en_US-lessac-medium", voices_dir=None, download=None, loader=None)`. `download` is `(name: str, dir: Path) -> None` (default `piper.download_voices.download_voice`); `loader` is `(model_path: Path) -> voice` (default `piper.PiperVoice.load`). `synthesize()` never raises on a load failure — it returns `b""`, logs once, and leaves `status == "error"`.
- Consumes: `LazyLoad` (unchanged).

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_pipelines.py` in the TTS section (after `test_resample_halves_and_keeps_duration`):

```python
def test_piper_downloads_the_voice_when_missing(tmp_path):
    from milo_brain.pipelines.tts import PiperTts

    calls = {"download": 0, "load": 0}

    def fake_download(name, directory):
        calls["download"] += 1
        (directory / f"{name}.onnx").write_bytes(b"model")  # simulate the fetch

    def fake_loader(model_path):
        calls["load"] += 1
        assert model_path.exists()
        return object()  # a stand-in "voice"; synthesize isn't exercised here

    tts = PiperTts(voice="en_US-lessac-medium", voices_dir=tmp_path,
                   download=fake_download, loader=fake_loader)
    tts.ensure_loaded()
    assert calls["download"] == 1 and calls["load"] == 1
    assert tts.status == "ready"


def test_piper_skips_download_when_the_voice_is_already_present(tmp_path):
    from milo_brain.pipelines.tts import PiperTts

    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"model")
    calls = {"download": 0}

    def fake_download(name, directory):
        calls["download"] += 1

    tts = PiperTts(voice="en_US-lessac-medium", voices_dir=tmp_path,
                   download=fake_download, loader=lambda p: object())
    tts.ensure_loaded()
    assert calls["download"] == 0  # already on disk -> no fetch


def test_piper_synthesize_stays_silent_and_logs_once_on_load_failure(tmp_path, caplog):
    import logging
    from milo_brain.pipelines.tts import PiperTts

    load_attempts = {"n": 0}

    def failing_download(name, directory):
        load_attempts["n"] += 1
        raise RuntimeError("network down")

    tts = PiperTts(voice="en_US-lessac-medium", voices_dir=tmp_path,
                   download=failing_download, loader=lambda p: object())

    with caplog.at_level(logging.WARNING, logger="milo_brain.pipelines.tts"):
        assert tts.synthesize("hello") == b""
        assert tts.synthesize("again") == b""
        assert tts.synthesize("and again") == b""

    assert tts.status == "error"
    assert load_attempts["n"] == 1  # only the first call tried to load; the rest short-circuit
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # logged exactly once, not per-utterance
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `brain/`): `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k piper -v`
Expected: FAIL — `PiperTts.__init__()` doesn't accept `voices_dir`/`download`/`loader` yet (`TypeError`).

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/pipelines/tts.py`, replace the imports and the `PiperTts` class. Change the top of the file from:

```python
"""Text-to-speech with Piper -> 16 kHz mono s16le, chunked for the wire."""

from __future__ import annotations

import numpy as np

from ._lazy import LazyLoad

TARGET_RATE = 16_000
FRAME_MS = 20
```

to:

```python
"""Text-to-speech with Piper -> 16 kHz mono s16le, chunked for the wire."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ._lazy import LazyLoad

log = logging.getLogger(__name__)

TARGET_RATE = 16_000
FRAME_MS = 20
DEFAULT_VOICES_DIR = Path.home() / ".milo-brain" / "piper-voices"
```

Replace the whole `PiperTts` class with:

```python
class PiperTts(LazyLoad):
    """Loads a Piper voice, downloading it on first use if it isn't cached.
    A voice that can't be fetched/loaded degrades to silence (logged once)
    rather than crashing every reply."""

    def __init__(self, voice: str = "en_US-lessac-medium", voices_dir=None,
                 download=None, loader=None):
        super().__init__()
        self._voice_name = voice
        self._voices_dir = Path(voices_dir) if voices_dir else DEFAULT_VOICES_DIR
        self._download = download
        self._loader = loader
        self._voice = None
        self._warned = False

    def _load(self) -> None:
        download = self._download
        loader = self._loader
        if download is None:
            from piper.download_voices import download_voice
            download = download_voice
        if loader is None:
            from piper import PiperVoice
            loader = PiperVoice.load
        model_path = self._voices_dir / f"{self._voice_name}.onnx"
        if not model_path.exists():
            self._voices_dir.mkdir(parents=True, exist_ok=True)
            log.info("downloading Piper voice %r to %s", self._voice_name, self._voices_dir)
            download(self._voice_name, self._voices_dir)
        self._voice = loader(model_path)

    def synthesize(self, text: str) -> bytes:
        """16 kHz mono s16le for ``{"t":"tts"}`` frames. Returns b"" (silence)
        if the voice can't be loaded, logging the reason exactly once."""
        if self.status == "error":
            return b""
        try:
            self.ensure_loaded()
        except Exception:
            if not self._warned:
                log.warning(
                    "TTS voice %r unavailable (%s); robot will stay silent until restart",
                    self._voice_name, self.error,
                )
                self._warned = True
            return b""
        samples: list[np.ndarray] = []
        src_rate = TARGET_RATE
        for chunk in self._voice.synthesize(text):
            samples.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
            src_rate = chunk.sample_rate
        if not samples:
            return b""
        audio = np.concatenate(samples)
        return resample_s16(audio, src_rate).tobytes()
```

In `brain/milo_brain/session.py`, `CognitionSessionFactory.__init__` currently has:

```python
        self._tts = PiperTts(cfg.piper_voice)
```

Change it to:

```python
        from pathlib import Path
        self._tts = PiperTts(cfg.piper_voice, voices_dir=Path(cfg.data_dir) / "piper-voices")
```

(If `session.py` already imports `Path` at module scope, use that instead of the local import — check the top of the file; as of this plan it does not, so the local import is correct and self-contained.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_pipelines.py -k piper -v`
Expected: PASS (3 new tests).

- [ ] **Step 5: Run the full brain suite**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass, no regressions.

- [ ] **Step 6: Manual verification — one real download**

Confirm the real download path works end to end (this is the only real network/model step; do it once, outside the unit suite). From the repo root:

```bash
.venv/Scripts/python.exe -c "
from pathlib import Path
import tempfile
from milo_brain.pipelines.tts import PiperTts
d = Path(tempfile.mkdtemp())
tts = PiperTts('en_US-lessac-medium', voices_dir=d)
pcm = tts.synthesize('Hello, I am Milo.')
print('status', tts.status, 'bytes', len(pcm))
assert tts.status == 'ready' and len(pcm) > 0
print('OK: voice downloaded and synthesized', d)
"
```
Expected: `status ready`, a non-zero byte count, and the voice files present in the temp dir. If the environment has no network, note that honestly in the report — the injected-fake tests are the authoritative automated verification and the real path is exercised by this manual step when a network is available.

- [ ] **Step 7: Commit**

```bash
git add brain/milo_brain/pipelines/tts.py brain/milo_brain/session.py brain/tests/test_pipelines.py
git commit -m "feat(brain): auto-download the Piper voice and stay silent (not crash) if it can't load"
```

---

### Task 2: Brain-side disconnect on the connector

**Files:**
- Modify: `brain/milo_brain/net/connector.py`
- Test: `brain/tests/test_connector.py`

**Interfaces:**
- Produces (on `RobotConnectorManager`): `request_disconnect() -> bool` (True if it closed a live connection, False no-op when not connected). New internal `_enabled: bool` and `_active_ws`. `link_state` can now be `"disconnected"` (manual). `request_manual_connect`/`request_manual_ip_connect`/`request_reconnect` re-enable (`_enabled = True`).

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_connector.py` (after `test_consecutive_drops_resets_after_a_successful_connect`):

```python
def test_request_disconnect_is_a_noop_when_not_connected(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path))
    connector = RobotConnectorManager(
        cfg, session_handler=lambda sock, peer: None, discovery=FakeDiscoveryEmpty(),
    )
    assert connector.request_disconnect() is False


def test_request_disconnect_closes_the_live_socket_and_stays_disconnected(tmp_path):
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        token = derive_token("123456", "milo-1", "brain-1")
        PairedStore(cfg.paired_path).add("milo-1", token)
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        robot_store.add("brain-1", token)

        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot

        session_running = asyncio.Event()
        session_ended = asyncio.Event()

        async def handler(sock, peer):
            session_running.set()
            try:
                await sock.recv()  # blocks until the socket is closed
            finally:
                session_ended.set()

        discovery = FakeDiscoveryWith(
            [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765)]
        )
        connector = RobotConnectorManager(
            cfg, session_handler=handler, discovery=discovery,
            connect=lambda url: _ConnectCM(raw_brain),
        )

        robot_task = asyncio.create_task(
            robot_handshake(MiloSocket(raw_robot), "milo-1", "milo", robot_store, mcp_port=0)
        )
        tick_task = asyncio.create_task(connector._tick())
        await asyncio.wait_for(session_running.wait(), timeout=2.0)
        await robot_task

        assert connector.request_disconnect() is True
        await asyncio.wait_for(session_ended.wait(), timeout=2.0)
        await asyncio.wait_for(tick_task, timeout=2.0)
        assert connector._enabled is False

        # A follow-up tick must idle (stay "disconnected"), NOT reconnect.
        async def handler_must_not_run(sock, peer):
            raise AssertionError("must not reconnect while manually disconnected")
        connector._session_handler = handler_must_not_run
        idle_tick = asyncio.create_task(connector._tick())
        await asyncio.sleep(0.05)
        assert not idle_tick.done()          # sitting idle, not connecting
        assert connector.link_state == "disconnected"
        connector.request_reconnect()        # re-enable + wake
        assert connector._enabled is True
        idle_tick.cancel()
        try:
            await idle_tick
        except asyncio.CancelledError:
            pass

    asyncio.run(main())
```

`FakeWebSocket` needs a `close()` that unblocks a pending `recv()`. Check `milo_common/testing.py`'s `FakeWebSocket`: its `recv()` awaits `self.outbox.get()` and `close()` just sets a flag — a pending `recv()` would NOT unblock. So this test also requires the connector's `request_disconnect` to close via the real `ws.close()` AND the fake to make a blocked `recv()` raise afterward. Rather than modifying the shared test double, have `request_disconnect` close the socket AND the test's `handler` uses `await sock.recv()` — so you must make the close cause the recv to raise. Simplest robust approach for the test: in `request_disconnect`, after closing, also cancel via the wake path is not enough. **Implementer: verify how `FakeWebSocket.close()` interacts with a blocked `recv()` before finalizing this test; if a blocked `recv()` can't be unblocked by `close()`, adjust the fake locally in the test file (subclass `FakeWebSocket` with a `close()` that puts a sentinel/raises into the outbox) rather than changing the shared double.** Name this in the report.

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_connector.py -k disconnect -v`
Expected: FAIL — `request_disconnect` doesn't exist (`AttributeError`).

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/net/connector.py`:

In `__init__`, after `self._consecutive_drops = 0` (the last line of `__init__`), add:

```python
        # Manual-disconnect latch: request_disconnect() sets this False and
        # closes the live socket; the tick loop then idles instead of
        # auto-reconnecting until an explicit connect action re-enables it.
        self._enabled = True
        self._active_ws = None
```

Add this method after `request_reconnect` (before `run_forever`):

```python
    def request_disconnect(self) -> bool:
        """Close the current robot connection and stop auto-reconnecting
        until an explicit connect/reconnect action. Returns False (no-op)
        if nothing is connected right now."""
        if self.connected_robot is None or self._active_ws is None:
            return False
        self._enabled = False
        self.link_state = "disconnected"
        ws, self._active_ws = self._active_ws, None
        asyncio.create_task(ws.close())
        self._wake.set()
        return True
```

In `request_manual_connect`, `request_manual_ip_connect`, and `request_reconnect`, add `self._enabled = True` as the first line of each method body (an explicit connect intent clears a manual disconnect). For `request_reconnect`, add it before the `if self.last_connected is None` check so a reconnect attempt always re-enables even if it turns out to be a no-op:

```python
    def request_reconnect(self) -> bool:
        self._enabled = True
        if self.last_connected is None:
            return False
        ...
```

In `_tick`, add an idle-when-disabled branch at the very top (before the manual-host-target handling):

```python
    async def _tick(self) -> None:
        if not self._enabled:
            self.link_state = "disconnected"
            self.link_target = None
            await self._wait_before_retry(3600)  # wake on any connect action
            return
        manual_host_target, self._manual_host_target = self._manual_host_target, None
        ...
```

In `_connect_and_run`, store the live socket and guard the retry path against our own disconnect. Change the `async with self._connect(url) as ws:` body so `self._active_ws = ws` is set right after entry and cleared in the session `finally`, and change the generic `except Exception` to bail out when manually disabled. The method becomes:

```python
    async def _connect_and_run(self, url: str, *, offer_pairing: bool) -> None:
        self.link_state = "connecting"
        self.link_target = _parse_host_port(url)
        self.last_error = None
        try:
            async with self._connect(url) as ws:
                self._active_ws = ws
                sock = MiloSocket(ws)
                self.link_state = "handshaking"
                peer = await brain_handshake(
                    sock,
                    self._cfg.brain_id,
                    self._cfg.name,
                    self._cfg.tier,
                    self._store,
                    request_pin=self._request_pin if offer_pairing else None,
                )
                if peer.mcp_port:
                    host = ws.remote_address[0]
                    peer = replace(peer, mcp_url=f"http://{host}:{peer.mcp_port}")
                log.info("connected to robot %s (%s)", peer.name, peer.id)
                self.connected_robot = peer
                self.link_state = "connected"
                self.last_connected = _parse_host_port(url)
                self.consecutive_drops = 0
                try:
                    await self._session_handler(sock, peer)
                finally:
                    self.connected_robot = None
                    self._active_ws = None
                    if self._enabled:
                        self.link_state = "idle"
        except HandshakeError as exc:
            self.link_state = "idle"
            self.last_error = f"handshake failed: {exc}"
            log.warning("handshake with %s failed: %s", url, exc)
            await self._wait_before_retry(self._cfg.reconnect_seconds)
        except Exception as exc:  # connection drop -> fail over on next tick
            self._active_ws = None
            if not self._enabled:
                # Our own request_disconnect() closed the socket -- go idle,
                # don't treat it as a drop to rescan/retry.
                return
            self.consecutive_drops += 1
            backoff = _drop_backoff_seconds(self.consecutive_drops)
            self.link_state = "retrying"
            self.retry_at = time.monotonic() + backoff
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info(
                "robot link lost (%s: %s), rescanning in %.0fs",
                type(exc).__name__, exc, backoff,
            )
            await self._wait_before_retry(backoff)
            self.retry_at = None
```

Note the two changes from the current version: `self._active_ws = ws` after entry; the session `finally` only sets `link_state = "idle"` when still `_enabled` (so a manual disconnect's `"disconnected"` isn't clobbered); and the generic `except` early-returns when `not self._enabled`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_connector.py -v`
Expected: all pass, including the pre-existing connector tests.

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

```bash
git add brain/milo_brain/net/connector.py brain/tests/test_connector.py
git commit -m "feat(brain): let the connector disconnect from a robot and stay disconnected"
```

---

### Task 3: Brain TUI disconnect key + dashboard rendering

**Files:**
- Modify: `brain/milo_brain/tui/app.py`
- Modify: `brain/milo_brain/tui/dashboard.py`
- Test: `brain/tests/test_tui_app.py`, `brain/tests/test_tui_dashboard.py`

**Interfaces:**
- Consumes: `connector.request_disconnect()` (Task 2), `connector.link_state == "disconnected"` (Task 2).
- Produces: a `d` binding + `action_disconnect()` on `MiloBrainApp`; a `"disconnected"` branch in `ConnectionPanel.render_connection`.

- [ ] **Step 1: Write the failing tests**

In `brain/tests/test_tui_app.py`, `FakeConnector` needs a `request_disconnect`. Add to `FakeConnector.__init__` (after `self.reconnect_requested = 0`):

```python
        self.disconnect_requested = 0
        self._disconnect_result = True
```

and add this method to `FakeConnector`:

```python
    def request_disconnect(self):
        self.disconnect_requested += 1
        return self._disconnect_result
```

Add these tests to the end of `brain/tests/test_tui_app.py`:

```python
def test_disconnect_action_calls_through_to_the_connector():
    async def scenario():
        app, connector = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_disconnect()
            await pilot.pause()
            return connector.disconnect_requested

    assert asyncio.run(scenario()) == 1


def test_disconnect_action_notifies_when_nothing_is_connected():
    async def scenario():
        app, connector = make_app()
        connector._disconnect_result = False
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_disconnect()
            await pilot.pause()
            return [n.message for n in app._notifications]

    messages = asyncio.run(scenario())
    assert any("Not connected" in m for m in messages)
```

In `brain/tests/test_tui_dashboard.py`, add:

```python
def test_refresh_from_shows_manual_disconnect_distinct_from_idle():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(link_state="disconnected", last_connected=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "disconnected" in connection
            assert "no robot connected" not in connection

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_app.py tests/test_tui_dashboard.py -k "disconnect" -v`
Expected: FAIL — `action_disconnect` doesn't exist; `ConnectionPanel` has no `"disconnected"` branch.

- [ ] **Step 3: Write the implementation**

In `brain/milo_brain/tui/app.py`, add to `BINDINGS` (after the `("r", "reconnect", "Reconnect")` line):

```python
        ("d", "disconnect", "Disconnect"),
```

Add this action method (after `action_reconnect`):

```python
    def action_disconnect(self) -> None:
        """Disconnect from the current robot and stop auto-reconnecting until
        the operator reconnects (c) or redials the last robot (r)."""
        if self.connector.request_disconnect():
            self.notify("Disconnected")
        else:
            self.notify("Not connected", severity="warning")
```

In `brain/milo_brain/tui/dashboard.py`, in `ConnectionPanel.render_connection`, add a `disconnected` branch. Change the branch chain so that after the `retrying` branch and before the final `else`, there's:

```python
        elif link_state == "disconnected":
            lines.append("Robot: disconnected (press c to connect, r to reconnect)")
```

i.e. the chain becomes `connected / connecting / handshaking / retrying / disconnected / else("no robot connected")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/test_tui_app.py tests/test_tui_dashboard.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full brain suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `brain/`)
Expected: all pass.

```bash
git add brain/milo_brain/tui/app.py brain/milo_brain/tui/dashboard.py brain/tests/test_tui_app.py brain/tests/test_tui_dashboard.py
git commit -m "feat(brain): add a Disconnect key to the TUI and show manual-disconnect state"
```

---

### Task 4: RobotServer.disconnect_brain

**Files:**
- Modify: `bridge/milo_bridge/net/server.py`
- Test: `bridge/tests/` (add to the existing server test file — locate it with `ls bridge/tests | grep -i server`; if none exists, create `bridge/tests/test_server_disconnect.py`)

**Interfaces:**
- Produces: `RobotServer.disconnect_brain(peer_id: str) -> bool` (awaitable) — closes that brain's socket, returns False if the id isn't connected. New `self._brain_socks: dict[str, MiloSocket]` populated/cleared alongside `connected_brains`.

- [ ] **Step 1: Inspect the existing server tests**

Run: `ls bridge/tests | grep -i server` and read whatever server test file exists to match its fixtures/style (how it constructs a `RobotServer`, what fakes it uses for `display`/`runner`/`store`). Base the new test on that. If there is no server test file, create `bridge/tests/test_server_disconnect.py` and construct a minimal `RobotServer` following `net/server.py`'s `__init__` signature with simple fakes/`None`s (only `cfg` with a `paired_path` and the `display` are needed for these tests; media/graph/gait can be `None`).

- [ ] **Step 2: Write the failing tests**

Write tests asserting:
1. `disconnect_brain("nope")` returns `False` when no such brain is connected.
2. With a brain registered — simulate the state `_on_connection` establishes by directly inserting into `connected_brains` and `_brain_socks` a fake `Peer` and a fake socket exposing an awaitable `close()` — `await disconnect_brain(peer_id)` returns `True` and calls the socket's `close()`.

Concrete test (adapt the `RobotServer` construction to match the existing suite's style found in Step 1):

```python
import asyncio
from milo_common.handshake import Peer
from milo_bridge.net.server import RobotServer


class _FakeSock:
    def __init__(self):
        self.closed = False
    async def close(self, code=1000, reason=""):
        self.closed = True


def _make_server(tmp_path):
    class _Cfg:
        paired_path = tmp_path / "paired.json"
        robot_id = "milo-1"
        robot_name = "milo"
        robot_ws_port = 8765
        mcp_port = 8766
    return RobotServer(_Cfg(), display=None, runner=None)


def test_disconnect_brain_unknown_id_is_a_noop(tmp_path):
    server = _make_server(tmp_path)
    assert asyncio.run(server.disconnect_brain("nope")) is False


def test_disconnect_brain_closes_the_connected_brains_socket(tmp_path):
    server = _make_server(tmp_path)
    sock = _FakeSock()
    server.connected_brains["brain-1"] = Peer(id="brain-1", name="desk")
    server._brain_socks["brain-1"] = sock
    server.active_brain_id = "brain-1"

    assert asyncio.run(server.disconnect_brain("brain-1")) is True
    assert sock.closed is True
```

Note: `RobotServer.__init__` may require more than `cfg`/`display`/`runner` — match its actual signature (it has keyword-only `audio`/`graph_api`/`gait`/`media_hub`/`broker`/`advertiser`, all defaulting to `None`, so `RobotServer(cfg, display=None, runner=None)` is valid). It also constructs `RobotAdvertiser(cfg)` and `PairingController` — if those need real cfg attributes, give the fake `_Cfg` whatever attributes they read (inspect `RobotAdvertiser.__init__`). If constructing a full `RobotServer` is too heavy, instantiate via `RobotServer.__new__(RobotServer)` and set only `connected_brains`, `_brain_socks`, `active_brain_id` by hand — but prefer the real constructor if it works. Decide during implementation and note which you used.

- [ ] **Step 3: Run tests to verify they fail**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/test_server_disconnect.py -v` (or wherever the tests landed)
Expected: FAIL — `_brain_socks` / `disconnect_brain` don't exist.

- [ ] **Step 4: Write the implementation**

In `bridge/milo_bridge/net/server.py`, in `RobotServer.__init__`, after `self.active_brain_id: str | None = None`, add:

```python
        # Live socket per connected brain, so the webapp can close a
        # specific brain's session (see disconnect_brain / webapp Brain card).
        self._brain_socks: dict[str, MiloSocket] = {}
```

Add this method (after `set_active_brain`):

```python
    async def disconnect_brain(self, peer_id: str) -> bool:
        """Close a specific connected brain's session. The session's own
        finally in _on_connection does the bookkeeping (drops it from
        connected_brains, reassigns active_brain_id, updates busy). Returns
        False if that brain isn't connected."""
        sock = self._brain_socks.get(peer_id)
        if sock is None:
            return False
        await sock.close(4003, "disconnected by operator")
        return True
```

In `_on_connection`, register the socket alongside the peer. After `self.connected_brains[peer.id] = peer` add:

```python
        self._brain_socks[peer.id] = sock
```

and in the `finally`, alongside `self.connected_brains.pop(peer.id, None)`, add:

```python
            self._brain_socks.pop(peer.id, None)
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/ -k disconnect_brain -v`
Expected: pass.

- [ ] **Step 6: Run the full bridge suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `bridge/`)
Expected: all pass (baseline 392).

```bash
git add bridge/milo_bridge/net/server.py bridge/tests/
git commit -m "feat(bridge): RobotServer.disconnect_brain to close a specific brain's session"
```

---

### Task 5: Webapp disconnect-brain wiring

**Files:**
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Modify: `bridge/milo_bridge/webapp/static/js/panels/brain.js`
- Test: `bridge/tests/` (the webapp test dir — match the existing `switch_active_brain` test's location/style)

**Interfaces:**
- Consumes: `RobotServer.disconnect_brain` (Task 4).
- Produces: `MotionController.disconnect_brain(client_id, peer_id) -> dict` (control-gated, mirrors `switch_active_brain`); a `ws.py` `disconnect_brain` message handler; a per-brain Disconnect button on the Brain card.

- [ ] **Step 1: Locate and read the existing switch_active_brain tests**

Run: `grep -rn "switch_active_brain" bridge/tests` and read the test(s). Mirror their structure for `disconnect_brain` (same control-gating, same fake `robot_server`).

- [ ] **Step 2: Write the failing tests**

Mirroring the existing `switch_active_brain` test(s), add tests asserting:
1. `disconnect_brain` is control-gated: a non-controlling `client_id` gets `{"error": ...}` and never calls the robot server (reuse whatever `_denied`/control fake the existing switch test uses).
2. The controlling client's call delegates to `robot_server.disconnect_brain(peer_id)` and returns `{"ok": True}` on success / `{"error": ...}` when the server returns `False`.

Follow the exact fake-robot-server shape the `switch_active_brain` test already uses (it has a fake with `set_active_brain`; add `disconnect_brain` returning a configurable bool — make it an `async` method since the real one is awaitable).

- [ ] **Step 3: Run tests to verify they fail**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/ -k disconnect_brain -v`
Expected: FAIL — `MotionController.disconnect_brain` doesn't exist.

- [ ] **Step 4: Write the implementation**

In `bridge/milo_bridge/webapp/motion.py`, add after `switch_active_brain` (mirror it exactly, but the server call is awaitable):

```python
    async def disconnect_brain(self, client_id: str, peer_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        robot_server = self._deps.robot_server
        if robot_server is None:
            return {"error": "unavailable"}
        if not await robot_server.disconnect_brain(peer_id):
            return {"error": f"brain {peer_id!r} isn't connected"}
        return {"ok": True}
```

In `bridge/milo_bridge/webapp/ws.py`, add after the `switch_active_brain` handler block:

```python
    if t == "disconnect_brain":
        res = await motion.disconnect_brain(client_id, str(data.get("id", "")))
        if "error" in res:
            await ws.send_json({"t": "err", "for": "disconnect_brain", "error": res["error"]})
        return
```

In `bridge/milo_bridge/webapp/static/js/panels/brain.js`, add a Disconnect button to each connected-brain row. Change the connected-list mapping from:

```javascript
      connectedEl.innerHTML = connected.length
        ? connected.map((b) => `
            <li>
              ${b.name}${b.active ? " <b>(active)</b>" : ""}
              ${b.active ? "" : `<button class="btn switch-brain-btn" data-id="${b.id}">Make Active</button>`}
            </li>`).join("")
        : "";
```

to:

```javascript
      connectedEl.innerHTML = connected.length
        ? connected.map((b) => `
            <li>
              ${b.name}${b.active ? " <b>(active)</b>" : ""}
              ${b.active ? "" : `<button class="btn switch-brain-btn" data-id="${b.id}">Make Active</button>`}
              <button class="btn disconnect-brain-btn" data-id="${b.id}">Disconnect</button>
            </li>`).join("")
        : "";
```

and extend the `connectedEl.onclick` delegator to handle the new button:

```javascript
    connectedEl.onclick = (ev) => {
      const sw = ev.target.closest(".switch-brain-btn");
      if (sw) { bus.send({ t: "switch_active_brain", id: sw.dataset.id }); return; }
      const dc = ev.target.closest(".disconnect-brain-btn");
      if (dc) bus.send({ t: "disconnect_brain", id: dc.dataset.id });
    };
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/ -k disconnect_brain -v`
Expected: pass.

- [ ] **Step 6: Run the full bridge suite**

Run: `../.venv/Scripts/python.exe -m pytest` (from `bridge/`)
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/webapp/ws.py bridge/milo_bridge/webapp/static/js/panels/brain.js bridge/tests/
git commit -m "feat(bridge): webapp control to disconnect a specific brain from the robot"
```
