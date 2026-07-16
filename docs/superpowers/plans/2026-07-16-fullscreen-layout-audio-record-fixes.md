# Fullscreen Sizing Fix, Two-Panel Layout, Audio Mode Removal, Record-with-Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the fullscreen video sizing cascade bug, split the fullscreen overlay into a balanced two-panel (left/right) layout, remove the Quality/Realtime listening-mode toggle, give the Fullscreen button its own visual style, and make Record capture the robot's incoming audio alongside video.

**Architecture:** `comm.js` first — strip the audio-mode toggle back to a single Listen/Mute button, and add a non-UI `getAudioSession(bus)` export plus a lazily-created `MediaStreamAudioDestinationNode` recording tap on the shared session, so `camera.js` can pull the robot's live audio into a `MediaRecorder` without opening a second playback pipeline. Then `camera.js` + `console.css` together — fix the CSS cascade bug that left `max-width: 560px` unset in fullscreen, split the single overlay into two docked panels, restyle the Fullscreen button, and wire Record to the new audio tap.

**Tech Stack:** Vanilla ES modules (no build step, no framework), CSS. No backend changes this round (the sixth original request, an independent mic/speaker hardware-detection split, was explicitly dropped).

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-16-fullscreen-layout-audio-record-fixes-design.md`.
- No automated JS test suite exists in this repo — verify via `node --check` for syntax and a `bridge/tools/webdev.py` dev-server smoke test; real visual/behavioral confirmation needs a human in a real browser after deploy.
- This machine's global Python has `milo-bridge` pip-installed editable against the main checkout, shared across every git worktree — prefix any backend command in a worktree with `PYTHONPATH="$(pwd)/bridge")`. (Not expected to matter this round since no backend files change, but the full suite should still be run once as a safety net.)
- Every commit is a real git commit on the current branch, one per task, following this repo's existing commit style (no AI co-author trailer — short, present-tense, prefixed `feat:`/`fix:`/`refactor:` as appropriate).

---

## Task 1: `comm.js` — remove Quality/Realtime, add audio session tap for Record

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/comm.js` (full file, 232 lines today)

**Interfaces:**
- Produces: `export function getAudioSession(bus)` — returns the same singleton session object `mountCommunication` already uses internally (`{ listening, setListening(on), getAudioTap() }` are the parts `camera.js`'s Task 2 needs). `mountCommunication`'s own exported signature is unchanged.
- The session's `getAudioTap()` returns a `MediaStream` (from a `MediaStreamAudioDestinationNode`) carrying whatever audio is currently being scheduled for playback — silent if `listening` is false, since nothing is scheduled onto it in that state.

No automated test exists for this file (no JS test suite). Verified manually per Step 3.

- [ ] **Step 1: Replace the full file contents**

```js
// Communication panel: merges the old Ears (listen) and Voice (speak) cards.
// Listening (headphones + VU meter) needs no control; push-to-talk and Say
// are individually locked until this tab holds control.
//
// mountCommunication() is exported so this exact UI can also be mounted a
// second time inside the camera fullscreen overlay. Both instances share
// ONE underlying audio session (see getSession) instead of each opening its
// own AudioContext/playhead: this app's fixed cockpit layout keeps every
// panel mounted simultaneously (nothing ever unmounts), so two independent
// "listening" pipelines running at once would double-schedule and echo the
// same incoming audio. The session is the single source of truth; each
// mounted UI just renders it and calls into it.
//
// getAudioSession() exposes that same session without mounting any UI --
// used by the camera panel's Record feature to tap the robot's incoming
// audio into a video recording without needing its own second listening
// pipeline.
import { ICON_HEADPHONES, ICON_MIC } from "../icons.js";

const SAMPLE_RATE = 16000;   // must match the robot's capture/playback rate
const CHANNELS = 2;
const HOT_THRESHOLD = 0.5;   // level (0-1) above which the VU bar turns red

// Server sends 20ms chunks; scheduling each one as its own AudioBufferSourceNode
// makes playback exquisitely sensitive to network/GC jitter (any late chunk is
// an audible drop). Coalescing a few chunks into one larger buffer before
// scheduling cuts the node-creation rate, and a wider lookahead margin gives
// the pipeline (network + server queue) more room to catch up without an
// audible gap. Fine for "listen to the room", not meant for interactive
// back-and-forth voice.
const COALESCE_CHUNKS = 4;   // ~80ms per scheduled buffer
const LOOKAHEAD_S = 0.15;
// playHead only ever moves forward; nothing about scheduling ahead of time
// brings it back down. If frames ever arrive faster than real-time (a burst
// after a brief stall, a slow start right when the connection opens), the
// backlog compounds and never resyncs -- latency creeps upward for the rest
// of the session. Cap it: once the scheduled backlog exceeds this, snap back
// to "now + lookahead" instead of letting it grow, accepting a brief glitch
// in exchange for bounded, LAN-appropriate latency.
const MAX_LATENCY_S = 0.35;

let session = null;

function getSession(bus) {
  if (session) return session;
  const s = {
    playCtx: null, playHead: 0, listening: false,
    pending: [], pendingSamples: 0,
    recordTap: null,          // lazily-created MediaStreamAudioDestinationNode
    uiCallbacks: new Set(),   // notified on listening change
    levelCallbacks: new Set(), // notified with [levelL, levelR] per chunk
  };

  function scheduleChunk(pcm) {
    const frames = pcm.length / CHANNELS;
    const buf = s.playCtx.createBuffer(CHANNELS, frames, SAMPLE_RATE);
    const levels = [0, 0];
    for (let ch = 0; ch < CHANNELS; ch++) {
      const out = buf.getChannelData(ch);
      let sumSq = 0;
      for (let i = 0; i < frames; i++) {
        const v = pcm[i * CHANNELS + ch] / 32768;
        out[i] = v; sumSq += v * v;
      }
      levels[ch] = Math.sqrt(sumSq / frames) * 4;
    }
    s.levelCallbacks.forEach((fn) => fn(levels));
    const src = s.playCtx.createBufferSource();
    src.buffer = buf;
    src.connect(s.playCtx.destination);
    // AudioBufferSourceNodes are one-shot and garbage-collected once they
    // finish playing, so connecting each one to the recording tap (when a
    // recording is active) never accumulates -- nothing to disconnect.
    if (s.recordTap) src.connect(s.recordTap);
    if (s.playHead - s.playCtx.currentTime > MAX_LATENCY_S) {
      s.playHead = s.playCtx.currentTime + LOOKAHEAD_S; // resync: drop the backlog, bound latency
    } else {
      s.playHead = Math.max(s.playHead, s.playCtx.currentTime + LOOKAHEAD_S);
    }
    src.start(s.playHead);
    s.playHead += buf.duration;
  }

  function flushPending() {
    if (s.pendingSamples === 0) return;
    const merged = new Int16Array(s.pendingSamples);
    let offset = 0;
    for (const chunk of s.pending) { merged.set(chunk, offset); offset += chunk.length; }
    s.pending = []; s.pendingSamples = 0;
    scheduleChunk(merged);
  }

  bus.onBinary((u8) => {
    if (!s.listening || u8[0] !== 0x01) return;
    const bytes = u8.slice(1); // fresh, zero-offset buffer -- Int16Array requires a 2-byte-aligned offset
    const pcm = new Int16Array(bytes.buffer, 0, bytes.byteLength >> 1);
    s.pending.push(pcm);
    s.pendingSamples += pcm.length;
    if (s.pending.length >= COALESCE_CHUNKS) flushPending();
  });

  s.setListening = (on) => {
    s.listening = on;
    if (on && !s.playCtx) s.playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    if (on) {
      s.playHead = 0;
    } else {
      s.pending = []; s.pendingSamples = 0;
      s.levelCallbacks.forEach((fn) => fn([0, 0]));
    }
    bus.send({ t: "audio", on });
    s.uiCallbacks.forEach((fn) => fn());
  };

  // Lazily creates (once) and returns a MediaStream that mirrors whatever
  // audio is currently being scheduled for playback -- used by the camera
  // panel's Record feature to combine the robot's incoming audio with its
  // video canvas into one recorded MediaStream.
  s.getAudioTap = () => {
    if (!s.playCtx) s.playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    if (!s.recordTap) s.recordTap = s.playCtx.createMediaStreamDestination();
    return s.recordTap.stream;
  };

  session = s;
  return s;
}

export function getAudioSession(bus) {
  return getSession(bus);
}

export function mountCommunication(el, { bus }) {
  const s = getSession(bus);
  el.innerHTML = `
    <div class="comm-row">
      <div class="comm-controls">
        <button class="btn" id="headphones">${ICON_HEADPHONES}Listen</button>
        <button class="btn" id="ptt">${ICON_MIC}Hold to Talk</button>
        <div class="comm-say">
          <input id="say" placeholder="Type something to say…">
          <button class="btn" id="speak">Say</button>
        </div>
        <div class="muted" id="comm-note"></div>
      </div>
      <div class="vu-group">
        <div class="vu-col"><div class="vu-vertical" id="vu-l"><div class="vu-fill"></div></div><span class="vu-label">L</span></div>
        <div class="vu-col"><div class="vu-vertical" id="vu-r"><div class="vu-fill"></div></div><span class="vu-label">R</span></div>
      </div>
    </div>`;

  // -- listening (headphones + VU meters): no control required, driven by
  // the shared session so both mounted copies of this panel stay in sync --
  const headphones = el.querySelector("#headphones");
  const vuL = el.querySelector("#vu-l");
  const vuR = el.querySelector("#vu-r");

  function setLevel(bar, level) {
    bar.style.setProperty("--level", Math.min(1, level).toFixed(3));
    bar.classList.toggle("hot", level >= HOT_THRESHOLD);
  }
  const onLevels = ([l, r]) => { setLevel(vuL, l); setLevel(vuR, r); };
  s.levelCallbacks.add(onLevels);

  function render() {
    headphones.innerHTML = s.listening ? `${ICON_HEADPHONES}Mute` : `${ICON_HEADPHONES}Listen`;
    headphones.classList.toggle("active", s.listening);
  }
  render();
  s.uiCallbacks.add(render);

  headphones.onclick = () => s.setListening(!s.listening);

  // -- push-to-talk + Say: need control -------------------------------------
  const note = el.querySelector("#comm-note");
  const ptt = el.querySelector("#ptt");
  const say = el.querySelector("#say");
  const speak = el.querySelector("#speak");
  let recCtx = null, stream = null, node = null;

  function applyGate() {
    const locked = !bus.controlled;
    [ptt, say, speak].forEach((elm) => elm.classList.toggle("locked-control", locked));
    ptt.disabled = say.disabled = speak.disabled = locked;
    if (locked) stopTalk();
  }
  applyGate();
  const offControl = bus.on("control", applyGate);
  const offClose = bus.on("_close", applyGate);

  async function startTalk() {
    if (!bus.controlled) return;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch { note.textContent = "microphone permission denied"; return; }
    recCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    const src = recCtx.createMediaStreamSource(stream);
    node = recCtx.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = (ev) => {
      const f32 = ev.inputBuffer.getChannelData(0);
      const out = new Uint8Array(1 + f32.length * 2);
      out[0] = 0x02;
      const view = new DataView(out.buffer);
      for (let i = 0; i < f32.length; i++)
        view.setInt16(1 + i * 2, Math.max(-1, Math.min(1, f32[i])) * 32767, true);
      bus.sendBytes(out);
    };
    src.connect(node); node.connect(recCtx.destination);
  }
  function stopTalk() {
    if (node) node.disconnect();
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (recCtx) recCtx.close();
    recCtx = stream = node = null;
  }
  ptt.onpointerdown = startTalk;
  ptt.onpointerup = ptt.onpointerleave = stopTalk;

  speak.onclick = async () => {
    if (!bus.controlled) return;
    const text = say.value.trim();
    if (!text) return;
    const r = await fetch("/api/speak", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, client: bus.clientId }),
    }).then((r) => r.json()).catch(() => ({ error: "network" }));
    note.textContent = r.error ? `✗ ${r.error}` : "✓ spoke";
  };

  return () => {
    s.uiCallbacks.delete(render);
    s.levelCallbacks.delete(onLevels);
    offControl(); offClose();
    stopTalk();
  };
}

export default {
  id: "comm", title: "Communication",
  mount(el, { bus }) {
    return mountCommunication(el, { bus });
  },
};
```

- [ ] **Step 2: Syntax check**

Run: `node --check bridge/milo_bridge/webapp/static/js/panels/comm.js`
Expected: no output (success).

- [ ] **Step 3: Manual verification**

Run `python bridge/tools/webdev.py` in the background (it runs forever — background it, curl, then kill it). Confirm `GET /static/js/panels/comm.js` returns 200 with the updated content and no syntax errors reachable through the dev server boot. Full interactive browser verification (clicking Listen, confirming VU meters move, confirming no Quality/Realtime buttons remain) is not possible in this environment — note that in your report; it needs a human in a real browser after deploy.

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/comm.js
git commit -m "fix(webapp): drop Quality/Realtime listening modes, add audio tap for Record"
```

---

## Task 2: `camera.js` + `console.css` — sizing fix, two-panel layout, button style, record-with-audio

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/camera.js` (full file, 194 lines today)
- Modify: `bridge/milo_bridge/webapp/static/css/console.css:37-90` (the `.cam-wrap`/`.cam-overlay` block)

**Interfaces:**
- Consumes: `getAudioSession(bus)` from Task 1's `comm.js` (`{ listening, setListening(on), getAudioTap() }`).
- Produces: no new exports — this task only restructures the camera panel's own markup/wiring.

No automated test exists for these files. Verified manually per Step 4.

- [ ] **Step 1: Replace `console.css`'s `.cam-wrap`/`.cam-overlay` block**

Replace (the block spanning the `.cam-wrap { position: relative; }` rule through the `.cam-dpad { ... }` rule, i.e. current lines 37-86 inclusive — everything between the `#cam { ... }` rule above it and the `/* small segmented button row ... */` comment below it):

```css
.cam-wrap { position: relative; }
.cam-wrap:fullscreen {
  background: #000; display: flex; align-items: center; justify-content: center;
}
.cam-wrap:fullscreen #cam {
  /* Fill the available screen space while keeping the camera's real 4:3
     aspect ratio -- never distorted/stretched (object-fit: contain, not
     fill). SD and HD share the same ratio, so both scale up to the same
     displayed size; SD just renders that size from a lower-res source.
     max-width/max-height/margin must be explicitly reset here: CSS cascades
     per-property, not per-rule, so the base #cam rule's max-width: 560px
     and margin: 0 auto otherwise survive into fullscreen unchanged and cap
     the display at a small box regardless of viewport size -- this was the
     actual cause of a previous "HD looks the same small size as SD" report,
     not a resolution-dependent bug at all. */
  width: 100vw; height: 100vh; max-width: none; max-height: none; margin: 0;
  aspect-ratio: auto; object-fit: contain;
}

/* Two floating control docks, left and right, anchored to the fullscreen
   video's corners -- deliberately not merged into one panel or centered, so
   controls are visually balanced and neither dock blocks most of the view.
   Left holds piloting + camera capture; right holds safety/status controls
   (see camera.js for the exact split). Both anchored top+bottom so their
   height is whatever fits between them, scrollable if content runs long.
   Their own dark glass panel regardless of the site's light/dark theme,
   since they sit over live video (unknown brightness), not the page
   background. */
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
.cam-overlay-divider { border-top: 1px solid rgba(255, 255, 255, 0.14); margin: 2px 0; }
.cam-overlay, .cam-overlay .muted, .cam-overlay .label, .cam-overlay .vu-label,
.cam-overlay .hw-name { color: #f2f2f2; }
.cam-overlay .btn {
  background: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.28); color: #f2f2f2;
}
.cam-overlay .btn:hover { background: rgba(255, 255, 255, 0.2); }
.cam-overlay .btn.active { background: #f2f2f2; color: #111; }
.cam-overlay .btn.danger { border-color: var(--danger); color: #ff8a80; }
.cam-overlay .btn.ghost { background: transparent; border-color: rgba(255, 255, 255, 0.4); }
.cam-overlay .btn.ghost:hover { background: rgba(255, 255, 255, 0.12); }
.cam-overlay input:not([type="range"]) {
  background: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.28); color: #f2f2f2;
}
.cam-overlay .vu-vertical { background: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.28); }
.cam-overlay .emote-popover { background: rgba(15, 15, 15, 0.92); border-color: rgba(255, 255, 255, 0.16); }
.cam-overlay .sensor-tile { border-color: rgba(255, 255, 255, 0.16); }
/* the Communication panel's row layout (controls beside VU meters) is too
   wide for this narrow dock -- stack them instead */
.cam-overlay .comm-row { flex-direction: column; gap: 10px; }
.cam-overlay .vu-group { align-self: flex-start; }
.cam-overlay-left.locked .cam-pilot-control { opacity: 0.4; pointer-events: none; }
.cam-overlay-row { display: flex; gap: 6px; }
.cam-overlay-row .btn { flex: 1; }
.cam-dpad { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }
```

Note the `.locked` selector changed from `.cam-overlay.locked` to `.cam-overlay-left.locked` — only the left (piloting) panel ever gets the `.locked` class now (see Step 2 below), so this selector must target it specifically rather than either panel.

- [ ] **Step 2: Replace the full contents of `camera.js`**

```js
import { createPilotController } from "../pilot.js";
import { mountEmotePopover } from "./poses.js";
import { mountImuPlate } from "./sensors.js";
import { mountCommunication, getAudioSession } from "./comm.js";
import { ICON_RECORD } from "../icons.js";

const MODES = ["raw", "balanced", "angled"];
const MODE_LABEL = { raw: "Raw", balanced: "Balanced", angled: "Angled" };

export default {
  id: "camera", title: "Camera",
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <div id="cam-wrap" class="cam-wrap">
          <img id="cam" src="/stream/camera" alt="camera offline" onerror="this.dataset.err=1">
          <div id="cam-overlay-left" class="cam-overlay cam-overlay-left">
            <div class="cam-overlay-row seg-row cam-pilot-control" id="ov-mode-row">
              ${MODES.map((m) => `<button class="btn" data-mode="${m}">${MODE_LABEL[m]}</button>`).join("")}
            </div>
            <label class="muted cam-pilot-control">Speed <input id="ov-speed" type="range" min="10" max="100" value="60"></label>
            <div class="cam-dpad cam-pilot-control">
              <div></div><button class="btn" data-dpad="up" style="font-size:20px">↑</button><div></div>
              <button class="btn" data-dpad="left" style="font-size:20px">←</button><div></div><button class="btn" data-dpad="right" style="font-size:20px">→</button>
              <div></div><button class="btn" data-dpad="down" style="font-size:20px">↓</button><div></div>
            </div>
            <div class="cam-overlay-row cam-pilot-control">
              <button class="btn" data-dpad="lookup">Look Up</button>
              <button class="btn" data-dpad="lookdown">Look Down</button>
            </div>
            <div class="cam-overlay-divider"></div>
            <div class="cam-overlay-row seg-row">
              <button class="btn" data-res="sd">SD</button>
              <button class="btn" data-res="hd">HD</button>
            </div>
            <div class="cam-overlay-row seg-row">
              <button class="btn snap-btn">Snapshot</button>
              <button class="btn rec-btn">Record</button>
            </div>
          </div>
          <div id="cam-overlay-right" class="cam-overlay cam-overlay-right">
            <button class="btn" id="ov-control">Take Control</button>
            <div class="cam-overlay-row">
              <button class="btn danger" id="ov-stop">STOP</button>
              <button class="btn ghost" id="ov-exit">Exit Fullscreen</button>
            </div>
            <div class="cam-overlay-divider"></div>
            <div id="ov-comm-mount"></div>
            <div class="cam-overlay-divider"></div>
            <div id="ov-emote-mount"></div>
            <div class="cam-overlay-divider"></div>
            <div class="sensor-tile imu-tile" id="ov-imu-mount"></div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn snap-btn">Snapshot</button>
          <button class="btn rec-btn">Record</button>
          <button class="btn ghost" id="fullscreen">Fullscreen</button>
          <div class="seg-row" style="margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
      </div>`;
    const img = el.querySelector("#cam");
    const camWrap = el.querySelector("#cam-wrap");
    const overlayLeft = el.querySelector("#cam-overlay-left");
    const overlayRight = el.querySelector("#cam-overlay-right");
    const commSession = getAudioSession(bus);

    // -- mode buttons + speed slider (mirrors the Move panel's own controls,
    // so piloting from fullscreen has a real adjustable speed instead of a
    // fixed fallback, and mode is switchable without leaving fullscreen) --
    const ovSpeed = overlayLeft.querySelector("#ov-speed");
    function setOvModeButtons(name) {
      overlayLeft.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
    }
    setOvModeButtons("balanced");
    overlayLeft.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });
    const offMode = bus.on("mode", (m) => setOvModeButtons(m.name));

    const pilot = createPilotController(bus, () => ovSpeed.value);
    pilot.bindGaitButton(overlayLeft.querySelector('[data-dpad="up"]'), "ov-up", 1);
    pilot.bindGaitButton(overlayLeft.querySelector('[data-dpad="down"]'), "ov-down", -1);
    pilot.bindTurnButton(overlayLeft.querySelector('[data-dpad="left"]'), "left");
    pilot.bindTurnButton(overlayLeft.querySelector('[data-dpad="right"]'), "right");
    pilot.bindLookButton(overlayLeft.querySelector('[data-dpad="lookup"]'), "up");
    pilot.bindLookButton(overlayLeft.querySelector('[data-dpad="lookdown"]'), "down");

    const ovControl = overlayRight.querySelector("#ov-control");
    ovControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
    const offControl = bus.on("control", (m) => {
      ovControl.textContent = m.you ? "Release Control" : "Take Control";
      ovControl.classList.toggle("active", m.you);
      overlayLeft.classList.toggle("locked", !m.you);
    });
    overlayLeft.classList.toggle("locked", !bus.controlled);
    overlayRight.querySelector("#ov-stop").onclick = () => bus.send({ t: "stop" });
    overlayRight.querySelector("#ov-exit").onclick = () => document.exitFullscreen();

    const offComm = mountCommunication(overlayRight.querySelector("#ov-comm-mount"), { bus });
    mountEmotePopover(overlayRight.querySelector("#ov-emote-mount"), { bus });
    const offImu = mountImuPlate(overlayRight.querySelector("#ov-imu-mount"), { bus });

    el.querySelector("#fullscreen").onclick = () => camWrap.requestFullscreen();

    // -- snapshot: shared handler, bound to every copy on the page (normal
    // row + fullscreen left panel) -- pure client-side canvas grab, no state
    // to keep in sync beyond the click handler itself --
    el.querySelectorAll(".snap-btn").forEach((b) => {
      b.onclick = () => {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
        c.getContext("2d").drawImage(img, 0, 0);
        const a = document.createElement("a");
        a.href = c.toDataURL("image/jpeg");
        a.download = `milo-${Date.now()}.jpg`;
        a.click();
      };
    });

    // -- recording: captures both the canvas video AND the robot's incoming
    // audio, by tapping the same Web Audio graph the Communication panel's
    // Listen feature schedules into (see comm.js's getAudioTap) -- combines
    // that audio track with the canvas's video track into one MediaStream.
    // Audio only flows while the shared session is "listening", so a
    // recording started while Listen is off turns it on for the duration
    // and back off afterward -- but never turns off a listen session the
    // user already had running themselves before recording started.
    // One shared recorder/session, reflected on every button copy on the
    // page (normal row + fullscreen left panel), same reasoning as the
    // audio session -- two independent MediaRecorder instances recording
    // the same canvas at once would just waste CPU and confuse "which
    // recording am I stopping". --
    const recBtns = Array.from(el.querySelectorAll(".rec-btn"));
    let recorder = null, recChunks = [], recTimer = null;
    function setRecButtons(recording) {
      recBtns.forEach((b) => {
        b.innerHTML = recording ? `${ICON_RECORD}Stop & Save` : "Record";
        b.classList.toggle("active", recording);
      });
    }
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
    function stopRecording() {
      recorder?.stop();
      setRecButtons(false);
      recBtns.forEach((b) => { b.disabled = true; });
    }
    recBtns.forEach((b) => {
      b.onclick = () => (recorder && recorder.state === "recording" ? stopRecording() : startRecording());
    });

    // -- resolution: one shared toggle wired across every copy on the page
    // (the normal panel's #res-row and the fullscreen left panel's res row)
    // -- clicking either updates both, since they reflect one piece of
    // server state (see camera.py: resolution is a shared-device setting).
    // Fullscreen does NOT auto-switch resolution -- whatever is selected
    // (even SD) just fills the screen, scaled up; the buttons stay
    // available in both modes so the choice is always the viewer's. --
    function setResButtons(name) {
      el.querySelectorAll("[data-res]").forEach((b) => b.classList.toggle("active", b.dataset.res === name));
    }
    setResButtons("sd");
    el.querySelectorAll("[data-res]").forEach((b) => {
      b.onclick = () => bus.send({ t: "camera_resolution", value: b.dataset.res });
    });
    const offTelemetry = bus.on("telemetry", (m) => {
      if (m.camera_resolution) setResButtons(m.camera_resolution);
      if (m.gait_mode) setOvModeButtons(m.gait_mode);
    });

    return () => {
      offTelemetry();
      offControl();
      offMode();
      offComm();
      offImu();
      pilot.stop();
      if (recorder && recorder.state === "recording") stopRecording();
    };
  },
};
```

- [ ] **Step 3: Syntax check**

Run: `node --check bridge/milo_bridge/webapp/static/js/panels/camera.js`
Expected: no output (success).

Run a CSS brace-balance sanity check (no linter is set up in this repo):
```bash
python -c "
s = open('bridge/milo_bridge/webapp/static/css/console.css', encoding='utf-8').read()
print('braces:', s.count('{'), s.count('}'))
"
```
Expected: both counts equal.

- [ ] **Step 4: Manual verification**

Run `python bridge/tools/webdev.py` in the background, curl `/static/js/panels/camera.js` and `/static/css/console.css` to confirm both serve 200 with the updated content, then kill the server. Full interactive browser verification is not possible in this environment — explicitly list in your report what still needs a human with a real browser after deploy:
- Fullscreen: SD and HD both now visibly fill the screen (not capped to a small box), aspect ratio intact, not stretched.
- Two panels appear, balanced left (piloting/camera) and right (safety/comm/gyro/emotes), not one lopsided stack.
- Communication (both the normal panel and the fullscreen right panel) shows only a Listen/Mute button — no Quality/Realtime buttons anywhere.
- The Fullscreen button visually reads differently from Snapshot/Record (ghost style).
- Recording a clip while Listen was off: Listen visibly turns on during the recording and back off after it stops; the downloaded `.webm` has both picture and the robot's ambient audio when played back.
- Recording a clip while Listen was already on: Listen stays on after the recording stops (not turned off).

- [ ] **Step 5: Run the full backend suite as a regression safety net**

Run (from the repo root): `PYTHONPATH="$(pwd)/bridge" python -m pytest bridge/tests/ -q`
Expected: all pass, unchanged count from before this task (no backend files were touched this round).

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/camera.js bridge/milo_bridge/webapp/static/css/console.css
git commit -m "fix(webapp): fix fullscreen sizing cascade bug, split overlay into balanced left/right panels, style Fullscreen button, record robot audio alongside video"
```

---

## Task 3: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `PYTHONPATH="$(pwd)/bridge" python -m pytest bridge/tests/ -q`
Expected: all pass (unchanged from before this plan — no backend files touched).

- [ ] **Step 2: Full manual walkthrough checklist**

Compile the manual-verification items from Task 1 Step 3 and Task 2 Step 4 into one list for the human doing the real-browser check after deploy. No commit expected from this task unless a regression is found and fixed (in which case, fix it in the file it belongs to and commit as its own small `fix(webapp): ...` commit).
