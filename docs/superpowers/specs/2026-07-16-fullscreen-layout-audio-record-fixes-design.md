# Fullscreen Sizing Fix, Two-Panel Layout, Audio Mode Removal, Record-with-Audio

**Date:** 2026-07-16
**Status:** Approved for planning

## Problem

The previous fullscreen-overlay round (commit `a9f99d2`) shipped with several real defects:

1. **Fullscreen video sizing bug.** The fullscreen CSS override (`.cam-wrap:fullscreen #cam`) set `width`/`height`/`aspect-ratio`/`object-fit` but never reset the base `#cam` rule's `max-width: 560px` / `max-height` / `margin`. CSS cascades per-property, not per-rule — an unset property in a more-specific/later rule falls through to whatever an earlier matching rule set. The 560px cap therefore silently survived into fullscreen for both SD and HD, capping the display at a small box regardless of viewport size or which resolution was selected. This is almost certainly what read as "HD displays at SD's size."
2. **The single right-side floating panel is visually unbalanced** — everything (piloting, camera, communication, gyro) stacked in one tall right-side dock, nothing on the left.
3. **The Quality/Realtime listening-mode toggle (added two rounds ago) is unwanted** — revert to a single Listen/Mute button.
4. **The Fullscreen button is visually identical to Snapshot/Record**, inviting confusion about what it does.
5. **Record only ever captured video** (`canvas.captureStream()` has no audio track) — it should also capture the robot's incoming audio (the same feed Listen plays), client-side, no server changes.

A sixth item from the original request (splitting the Sensors hardware tile's "audio" status into independently-detected Mics/Speaker rows) was explicitly dropped by the user — out of scope for this spec.

## Goals

- Fix the fullscreen sizing cascade bug so SD and HD both genuinely fill the available screen space (scaled up, aspect ratio preserved, never cropped by a leftover pixel cap).
- Split the single fullscreen overlay into two docked panels — left (piloting + camera capture: mode/speed, d-pad, look, SD/HD, Snapshot, Record) and right (safety + status: Take Control, STOP, Exit Fullscreen, Communication, Emotes, Gyro).
- Remove the Quality/Realtime toggle entirely from `comm.js`; Listen/Mute always uses the original "quality" preset's buffering constants.
- Give the Fullscreen button a distinct visual style (`btn ghost`, matching the existing ghost treatment used by Exit Fullscreen and the tools-drawer close button) so it reads differently from the plain Snapshot/Record buttons.
- Record captures both the canvas video **and** the robot's incoming audio, by tapping the same Web Audio graph the Listen feature already schedules into a `MediaStreamAudioDestinationNode`, and combining that audio track with the canvas's video track into one `MediaStream` for `MediaRecorder`. If Listen wasn't already on when Record starts, it's turned on for the recording's duration and turned back off afterward — but never turned off if the user had already turned it on themselves before recording started.

## Non-goals

- No change to the Sensors hardware tile / mic-speaker split (explicitly dropped by the user this round).
- No change to camera resolution auto-switching behavior (already explicitly disabled in a prior round — SD/HD stay manual in both fullscreen and normal view).
- No new automated test infrastructure — this repo has no JS test suite; these are frontend-only files (`comm.js`, `camera.js`, `console.css`), verified manually.
- No change to the shared audio-session architecture's fundamental design (one singleton session shared across every mounted UI copy, to avoid double/echoed playback) — that stays, just with the mode-toggle UI/logic removed from it.

## Design

### 1. `console.css`: fix the sizing cascade bug

```css
.cam-wrap:fullscreen #cam {
  width: 100vw; height: 100vh; max-width: none; max-height: none; margin: 0;
  aspect-ratio: auto; object-fit: contain;
}
```

Explicitly resetting `max-width`/`max-height`/`margin` (not just adding new properties) is the actual fix — the base `#cam` rule's `max-width: 560px` and `margin: 0 auto` must be overridden, not left to fall through. Using `100vw`/`100vh` instead of percentages avoids any ambiguity about what "100%" resolves to relative to the flex parent.

### 2. Two-panel fullscreen layout

Replace the single `#cam-overlay` with two elements, `#cam-overlay-left` and `#cam-overlay-right`, both sharing a common `.cam-overlay` base class (dark glass panel, scrollable, same look as before) plus a modifier class for left/right positioning:

```css
.cam-overlay {
  position: absolute; top: 18px; bottom: 18px; display: none;
  flex-direction: column; gap: 8px; width: 200px; padding: 12px;
  background: rgba(15, 15, 15, 0.82); border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 12px; backdrop-filter: blur(6px);
  overflow-y: auto; overflow-x: hidden;
}
.cam-overlay-left { left: 18px; }
.cam-overlay-right { right: 18px; }
.cam-wrap:fullscreen .cam-overlay { display: flex; }
```

(All other `.cam-overlay *` rules — buttons, dividers, dark-panel input/VU overrides, `.cam-pilot-control` locked-dimming — stay as they are today; they already target `.cam-overlay` generically, which both panels carry.)

**Left panel contents** (piloting + camera capture):
- Mode buttons (Raw/Balanced/Angled) + speed slider (`.cam-pilot-control`, dimmed when not in control)
- D-pad + Look Up/Down (`.cam-pilot-control`)
- SD/HD switcher
- Snapshot + Record

**Right panel contents** (safety + status):
- Take Control / Release Control
- STOP
- Exit Fullscreen
- Communication (Listen/Mute only — see below)
- Emotes toggle + popover
- Gyro/IMU plate

Both `pilot.bindGaitButton`/`bindTurnButton`/`bindLookButton` calls, the mode-row wiring, and the SD/HD wiring all move to query within `overlayLeft` instead of the old single `overlay`; the control-toggle/STOP/exit/comm/emote/imu wiring move to query within `overlayRight`. The `.locked` class (control-gating) is applied to `overlayLeft` only, since only piloting-related controls need it — the right panel's contents (comm, emotes, gyro, the control toggle itself, STOP, exit) are not control-gated today and stay that way.

### 3. `comm.js`: remove Quality/Realtime

Delete `AUDIO_MODES`, the `audioMode` state, `setAudioMode`, the `#audio-mode-row` markup, and its wiring. Hardcode the original "quality" preset's three constants (`COALESCE_CHUNKS = 4`, `LOOKAHEAD_S = 0.15`, `MAX_LATENCY_S = 0.35`) as plain module constants, used directly in `scheduleChunk`/the binary handler — i.e. revert to the pre-quality-mode buffering behavior, but keep the shared-session singleton (`getSession`) since that's still needed: Communication is still mounted in two places (the normal cockpit panel and the fullscreen right panel), and two independent playback pipelines would still double/echo audio.

Add one new export, alongside the existing `mountCommunication`:

```js
// Exposes the shared audio session without mounting any UI — used by the
// camera panel's Record feature to tap the robot's incoming audio into a
// video recording without needing its own second listening pipeline.
export function getAudioSession(bus) {
  return getSession(bus);
}
```

Add a lazily-created recording tap to the session, and connect every scheduled chunk's source node to it as well as to the speakers:

```js
s.getAudioTap = () => {
  if (!s.playCtx) s.playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
  if (!s.recordTap) s.recordTap = s.playCtx.createMediaStreamDestination();
  return s.recordTap.stream;
};
```

```js
// inside scheduleChunk, after creating `src`:
src.connect(s.playCtx.destination);
if (s.recordTap) src.connect(s.recordTap);
```

(`AudioBufferSourceNode`s are one-shot and garbage-collected after they finish playing, so connecting each one to `recordTap` doesn't accumulate — there's nothing to disconnect.)

### 4. `camera.js`: fullscreen button style, two-panel wiring, record-with-audio

- `#fullscreen` button: `class="btn ghost"` instead of `class="btn"`.
- Import `getAudioSession` alongside `mountCommunication` from `./comm.js`.
- Recording, rewritten to combine video + tapped audio and to auto-manage Listen state:

```js
const commSession = getAudioSession(bus);
...
function startRecording() {
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth || 640;
  canvas.height = img.naturalHeight || 480;
  const ctx = canvas.getContext("2d");
  recTimer = setInterval(() => ctx.drawImage(img, 0, 0, canvas.width, canvas.height), 66);
  recChunks = [];

  const turnedOnListening = !commSession.listening;
  if (turnedOnListening) commSession.setListening(true);

  const videoTrack = canvas.captureStream(15).getVideoTracks()[0];
  const audioTrack = commSession.getAudioTap().getAudioTracks()[0];
  recorder = new MediaRecorder(new MediaStream([videoTrack, audioTrack]), { mimeType: "video/webm" });
  recorder.ondataavailable = (e) => { if (e.data.size > 0) recChunks.push(e.data); };
  recorder.onstop = () => {
    clearInterval(recTimer);
    if (turnedOnListening) commSession.setListening(false);
    const blob = new Blob(recChunks, { type: "video/webm" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `milo-${Date.now()}.webm`;
    a.click();
    URL.revokeObjectURL(a.href);
    recBtns.forEach((b) => { b.disabled = false; });
  };
  recorder.start();
  setRecButtons(true);
}
```

`stopRecording()`/`setRecButtons()`/the shared multi-button wiring (`.rec-btn` class, both the normal-row and left-panel copies) are unchanged from the current implementation.

Markup restructure: split the current single `#cam-overlay` div into `#cam-overlay-left` (class `cam-overlay cam-overlay-left`) and `#cam-overlay-right` (class `cam-overlay cam-overlay-right`), redistributing their contents per the Design §2 split above. `camWrap`/`img`/the normal (non-fullscreen) button row are unchanged.

## Testing

- No automated tests exist for these files (no JS test suite in this repo, confirmed in the prior round). Verified manually via `python bridge/tools/webdev.py` for syntax/boot-level checks (`node --check`, dev-server smoke test), and via real-hardware deployment + the user's own browser walkthrough for actual visual/behavioral confirmation, consistent with how every prior frontend round in this project has been verified.
- Specifically worth a human double-checking after deploy: fullscreen SD and HD now visibly fill the screen equally (not capped small); the two panels appear balanced left/right; Communication only shows Listen/Mute (no mode buttons); Fullscreen button looks visually distinct; a recorded clip, when played back, has both picture and the robot's ambient audio (assuming Listen was inactive beforehand, confirm it turns back off after the recording finishes).
