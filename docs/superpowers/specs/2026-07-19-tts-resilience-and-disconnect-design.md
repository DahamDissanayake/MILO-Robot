# TTS resilience + connection disconnect controls

Date: 2026-07-19

## Problem

From a live brain session against a real robot (192.168.1.15):

1. **TTS crashes every reply.** `PiperVoice.load("en_US-lessac-medium")` throws
   `FileNotFoundError: en_US-lessac-medium.json` — the Piper voice was never
   downloaded (no voice files exist on disk). Because the pipeline retries the
   load on every utterance, this spams a full traceback per reply and the robot
   never speaks.
2. **No way to disconnect a robot from the brain.** `RobotConnectorManager` has
   connect / reconnect / manual-connect but no disconnect. Once connected it
   stays connected (auto-reconnect loop); the only way to stop is to kill the
   whole brain process.
3. **No way to disconnect a brain from the robot's webapp.** `RobotServer`
   accepts multiple brains and can switch which one is *active* (has motion
   rights), but there's no way to close a specific brain's session. The webapp
   Brain card can "Make Active" and toggle pairing, but can't kick a brain.

(Not in scope here but noted for the operator: the `ProtocolError: header
'audio' promised a binary payload, got a text frame` seen in the same log is
already fixed in `common/milo_common/protocol.py` via a send lock — it persists
only because the **robot's** `milo-bridge` service must be redeployed
(`git pull` + reinstall + `systemctl restart milo-bridge`) to pick up the shared
`common` change. The brain is the receiver; restarting it alone can't fix a
sender-side interleave.)

## Goals

- The robot speaks: Piper voice is fetched automatically on first use if missing.
- A missing/unloadable voice degrades gracefully — logged once, surfaced as
  `TTS: error` on the dashboard, robot stays silent — never a per-utterance
  traceback.
- The brain can disconnect from a robot and *stay* disconnected (no auto-
  reconnect) until the operator reconnects.
- The webapp can disconnect (kick) a specific connected brain; the robot's
  existing multi-brain bookkeeping (active-brain reassignment, `busy`
  advertisement) handles the teardown.

## Non-goals

- The existing "Make Active" active-brain switch is unchanged.
- No change to the pairing/handshake flow.
- Whisper transcription quality (CPU hallucination on ambient noise) is a
  separate tuning concern, not addressed here.
- The Pi redeploy for the protocol-lock fix is an operator action, not code.

## Design

### A. TTS auto-download + graceful degrade (`brain/milo_brain/pipelines/tts.py`)

`PiperTts` gains a voices cache directory and injectable download/load hooks:

```python
class PiperTts(LazyLoad):
    def __init__(self, voice="en_US-lessac-medium", voices_dir=None,
                 download=None, loader=None):
        super().__init__()
        self._voice_name = voice
        self._voices_dir = Path(voices_dir) if voices_dir else DEFAULT_VOICES_DIR
        self._download = download          # (name, dir) -> None; default piper's
        self._loader = loader              # (model_path) -> voice; default PiperVoice.load
        self._voice = None
        self._warned = False

    def _load(self):
        from piper import PiperVoice
        from piper.download_voices import download_voice
        download = self._download or download_voice
        loader = self._loader or PiperVoice.load
        model_path = self._voices_dir / f"{self._voice_name}.onnx"
        if not model_path.exists():
            self._voices_dir.mkdir(parents=True, exist_ok=True)
            download(self._voice_name, self._voices_dir)
        self._voice = loader(model_path)

    def synthesize(self, text):
        if self.status == "error":
            return b""                     # known-bad: stay silent, no retry, no spam
        try:
            self.ensure_loaded()
        except Exception:
            if not self._warned:
                log.warning("TTS voice %r unavailable (%s); robot will stay silent "
                            "until restart", self._voice_name, self.error)
                self._warned = True
            return b""
        ... (existing synthesis unchanged) ...
```

- Injectable `download`/`loader` keep the test suite offline (fakes), mirroring
  `SileroSpeechDetector(model=...)` / `FaceVision(analyzer=...)`.
- `DEFAULT_VOICES_DIR = Path.home()/".milo-brain"/"piper-voices"`, and
  `CognitionSessionFactory` passes `Path(cfg.data_dir)/"piper-voices"` so it
  follows the configured data dir.
- The `status == "error"` short-circuit + `_warned` flag together guarantee
  exactly one log line and zero repeated load attempts after a failure — the
  dashboard's pipeline panel already renders `TTS: error` from `LazyLoad.status`.
- One real voice download is performed as manual verification of the task (not
  in the unit suite).

### B. Brain-side disconnect (`brain/milo_brain/net/connector.py`)

`RobotConnectorManager` gains:

- `self._enabled: bool = True` and `self._active_ws = None` (the live websocket,
  set in `_connect_and_run`'s `async with ... as ws`, cleared in its `finally`).
- `request_disconnect() -> bool`: if not currently connected, returns `False`
  (no-op). Otherwise sets `_enabled = False`, records
  `link_state = "disconnected"`, closes `_active_ws` (schedules
  `ws.close()`), and sets the wake event. Returns `True`.
- `_tick()`: at the top, if `not self._enabled`, set `link_state = "disconnected"`,
  `link_target = None`, and `await self._wait_before_retry(<forever-ish>)` (wake
  on the event) instead of connecting — so the loop idles instead of auto-
  reconnecting.
- `request_manual_connect` / `request_manual_ip_connect` / `request_reconnect`
  each set `self._enabled = True` before waking (an explicit connect intent
  clears a manual disconnect).
- `link_state` value set: `"disconnected"` is distinct from `"idle"` (nothing
  discovered) — the dashboard renders them differently ("disconnected (you)"
  vs "no robot connected").

The generic-exception retry path is unchanged *except* it must not fire when the
exception was caused by our own `request_disconnect()` close — guarded by
checking `_enabled` (a manual disconnect set it `False`, so the retry/rescan is
skipped and the loop falls into the idle branch on the next tick).

### C. Brain TUI disconnect key (`brain/milo_brain/tui/app.py`, `tui/dashboard.py`)

- `MiloBrainApp` BINDINGS gain `("d", "disconnect", "Disconnect")`;
  `action_disconnect()` calls `connector.request_disconnect()` and
  `notify("Disconnected")` / `notify("Not connected", severity="warning")` on the
  `False` no-op.
- `ConnectionPanel.render_connection` gains a branch: `link_state ==
  "disconnected"` → `"Robot: disconnected (press c/r to reconnect)"`, distinct
  from the `idle` "no robot connected".

### D. Webapp per-brain disconnect (`bridge/milo_bridge/net/server.py`, `webapp/`)

- `RobotServer` stores each brain's socket: add
  `self._brain_socks: dict[str, MiloSocket] = {}`, populated next to
  `connected_brains[peer.id] = peer` and removed in the same `finally`.
- `RobotServer.disconnect_brain(peer_id) -> bool`: if `peer_id` not connected,
  `False`. Otherwise close that socket (`await sock.close(4003, "disconnected by
  operator")`), returning `True`; the existing `_on_connection` `finally` does
  the rest (drops from `connected_brains`/`_brain_socks`, reassigns
  `active_brain_id`, updates advertiser `busy`).
- `webapp/motion.py` `disconnect_brain(client_id, peer_id) -> dict` (control-
  gated like `switch_active_brain`), calling `robot_server.disconnect_brain`.
- `webapp/ws.py` gains a `t == "disconnect_brain"` handler mirroring
  `switch_active_brain`.
- `webapp/static/js/panels/brain.js`: each connected brain row gets a
  `Disconnect` button (`disconnect-brain-btn`, `data-id`) alongside the existing
  Make-Active button; the `connectedEl.onclick` delegator sends
  `{ t: "disconnect_brain", id }`. `/api/brains` already returns the connected
  list, so the card refreshes within its 5 s poll.

## Error handling

- TTS: all load failure paths converge on "log once, return `b""`, mark errored"
  — no new uncaught exception path.
- Brain disconnect: closing `_active_ws` surfaces as the normal session-end
  path; the `_enabled` guard steers it to idle instead of retry.
- Webapp kick: closing a brain socket surfaces through the existing
  `_on_connection` `except/finally`, which already tolerates arbitrary session-
  end exceptions.

## Testing

- `tts.py`: fake `download`/`loader` — voice-present skips download; voice-
  missing triggers download then load; load failure → `synthesize` returns `b""`,
  logs once (caplog asserts a single record across repeated calls), status is
  `"error"`, and no second load attempt occurs.
- `connector.py`: `request_disconnect` is a no-op (`False`) when not connected;
  when connected it closes the socket, sets `_enabled=False` /
  `link_state="disconnected"`, and a subsequent `_tick` idles instead of
  reconnecting; a following `request_manual_connect`/`reconnect` re-enables.
- `tui`: `action_disconnect` calls through / notifies on no-op;
  `ConnectionPanel` renders the `disconnected` branch.
- `server.py`: `disconnect_brain` unknown id → `False`; known id closes the
  stored socket and the session teardown reassigns `active_brain_id`.
- `webapp`: `motion.disconnect_brain` control-gated + delegates; `ws.py`
  dispatches the new message.
