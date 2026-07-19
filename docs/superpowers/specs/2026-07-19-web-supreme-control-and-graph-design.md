# Webapp-supreme control (suspend the brain) + camera fan-out recovery + interactive graph

Date: 2026-07-19

## Problem

Live use surfaced three tangled issues in the robot's LAN webapp:

1. **Taking control doesn't really work.** When a brain is connected and the
   operator clicks "Take Control" in the webapp, the camera goes blank and the
   manual controls don't respond, while the brain keeps hearing/processing/
   answering. The current `ControlBroker` only arbitrates *motion* — a web pilot
   preempts the brain's movement, but the brain stays fully live (streams,
   cognition, speech). There is no notion of the webapp outranking the brain
   for anything but motion.

2. **Camera blanks out.** `Fanout._run` (webapp/media_hub.py) owns one reader
   task per device and fans frames to every subscriber. On a *natural* reader
   death (an uncaught driver exception / generator end — not a cancellation) it
   logs "fanout reader died" and stops; `_on_task_done` only restarts after a
   *cancellation*, so existing subscribers (the webapp's `/stream/camera` img)
   never recover until a brand-new `subscribe()` happens. Result: the live view
   freezes/blanks, especially around brain connect/disconnect churn.

3. **Memory Graph is static-feeling.** The graph panel (graph.js) is a
   force-directed canvas but is click-only (no dragging nodes, no panning) and
   tuned to spread out, so it doesn't read like graphify's dense circular
   cluster.

## Goals

- The webapp is the supreme control authority. When a human takes pilot control,
  every connected brain is **suspended** — the robot stops feeding it audio/
  video and ignores its speech/actions, so it goes fully quiet — and the pilot's
  camera + manual controls work. Releasing control **resumes** the brain(s)
  instantly (no disconnect, no model re-warm).
- The camera live view auto-recovers from a reader death while anyone is still
  watching — no permanent blank.
- The Memory Graph is directly manipulable: drag nodes, pan the view, and the
  layout settles into a compact graphify-style circular cluster of nodes+edges.

## Non-goals

- No change to which side dials the connection (the brain is still the WS client
  that discovers+dials; the robot is the server). "Assign control to a brain"
  means choosing the active brain among connected ones (existing "Make Active")
  and/or releasing web control so the active brain resumes — not making an
  offline brain connect.
- Suspend is stream-level (stop feeding + ignore output); it does NOT change the
  handshake, pairing, or the per-brain disconnect control already shipped.
- Face-recognition, ASR/LLM behavior unchanged.

## Design

### A. Camera fan-out auto-recovery (`webapp/media_hub.py`)

`Fanout` restarts its reader after a *natural* death while subscribers remain,
with a small backoff so a hard-broken device can't hot-loop:

- `_run`'s `except Exception` path no longer just logs and ends. Instead the
  fanout schedules a restart: after logging, if `self._subs` is non-empty, wait
  a short backoff (e.g. 1s) and start a fresh reader task.
- Implement by having `_on_task_done` restart on a *natural* death too (not only
  cancellation) when `self._subs` is non-empty — gated behind a short
  `call_later`/sleep so repeated immediate failures back off rather than spin.
- Cancellation-driven teardown (all subscribers gone) is unchanged: no restart.

Net effect: a camera/mic hiccup self-heals for everyone still watching.

### B. Webapp-supreme control: suspend the brain (`webapp/control.py`, `net/session.py`, `net/streams.py`, `net/server.py`)

The brain is *suspended* whenever a web client holds control, driven by the
existing broker owner state:

- `ControlBroker` gains `brain_suspended` semantics: it already knows
  `_web_owner`; add an observable "brain should be active" signal. Concretely, a
  `RobotServer`-level `asyncio.Event` `brain_active` (set when no web owner,
  cleared when a web owner acquires). The broker's existing `on_change` callback
  (owner transitions) toggles this event.
- `RobotServer` passes that `brain_active` event into each `RobotSession`.
- `RobotSession`'s outbound pumps (`streams.pump_video`/`pump_audio`) `await`
  `brain_active` before sending each frame — while cleared (web piloting), they
  drain their fanout queue but do not forward to the brain, so the brain's
  VAD/vision receive nothing and it stops thinking/talking. When the event is set
  again (web releases), they resume immediately.
- `RobotSession.dispatch` ignores inbound `T_TTS` from the brain while suspended
  (don't play the robot's speaker for a brain that's mid-thought when a human
  grabs control) — belt-and-suspenders, since the brain shouldn't be producing
  much with no input.
- Motion gating is unchanged (`allow_brain_motion()` already denies brain motion
  while a web owner holds control); suspend layers stream-cutoff on top so the
  brain is fully quiet, not just movement-blocked.

Hierarchy realized: **web owner present → all brains suspended + motion-blocked;
no web owner → the active brain runs normally.** The webapp can always
`acquire_web` (it preempts brains, which are never web owners), and releasing
hands control back to the chosen active brain. "Reclaim anytime, gracefully" =
acquire re-clears `brain_active`; the brain simply stops getting frames and goes
idle, then resumes on release with no reconnect.

### C. Take-control reliability (`webapp/ws.py` + a small JS check)

Root-cause and fix why control feels dead after "Take Control":

- Confirm the heartbeat loop: the client must send `{t:"hb"}` while it holds
  control or `broker.expire()` (10s) silently releases it, re-locking the UI.
  Ensure the front-end sends heartbeats whenever it holds control (verify
  bus/statusbar wiring) so control doesn't lapse mid-session.
- Confirm `bus.controlled` / the `{t:"control", you}` round-trip so the "Take
  Control"↔"Release Control" toggle and the overlay `locked` class reflect real
  ownership (the toggle sends `take: !bus.controlled`, so `bus.controlled` must
  track `m.you`).
- With the fan-out fix (A) the camera stays visible independent of control, so
  "camera blank on takeover" resolves there; verify end to end.

(The exact culprit among heartbeat-expiry / `bus.controlled` tracking is
confirmed during implementation with the real ws message flow; the fix keeps
control held for as long as the pilot holds it and the camera live throughout.)

### D. Interactive, compact Memory Graph (`webapp/static/js/panels/graph.js`)

- **Drag nodes:** pointerdown on a node grabs it; pointermove repositions it
  (its force velocity is pinned while held); pointerup releases it back into the
  simulation. Reuses the existing hit-test from the click handler.
- **Pan:** pointerdown on empty canvas pans the whole view (an `offsetX/offsetY`
  translation applied in `draw()` and subtracted in hit-testing); drag to move.
- **Compact circular cluster:** retune the force sim for a dense graphify-style
  ball — stronger centering pull, shorter repulsion range, slightly stronger
  edge springs — so nodes settle into a filled circle rather than drifting apart,
  with edges drawn inside. Keep the live-grow, search-highlight, and select
  behaviors.
- Clicking (select/detail) vs dragging is disambiguated by a small movement
  threshold; the existing 5s poll + search are unchanged.

## Error handling

- Fan-out: a persistently failing device backs off (bounded restart cadence)
  instead of hot-looping; a device with zero subscribers still tears down.
- Suspend: pumps only gate their *send*; they keep draining their queues so a
  resumed brain doesn't get a stale burst, and cancellation still unwinds
  cleanly.
- Control: heartbeat expiry still releases a genuinely-gone pilot (so a closed
  tab doesn't hold the robot hostage), but an active pilot keeps control.

## Testing

- `media_hub.py`: a factory that raises once then yields frames — the fanout
  restarts and the subscriber keeps receiving after the death; a fanout with no
  subscribers does not restart.
- `control.py` / session: acquiring web control clears `brain_active` (pumps
  stop forwarding); releasing sets it (pumps resume); a suspended session
  ignores inbound TTS. Unit-test the broker→event wiring and the pump's
  gate with fakes (no real hardware).
- `server.py`: `brain_active` event is threaded into the session and toggles
  with web ownership.
- Graph: JS has no unit harness in this project; verify by driving the panel
  (drag moves a node, pan shifts the view, layout stays clustered) and by not
  breaking the existing search/select — manual verification, stated honestly.
