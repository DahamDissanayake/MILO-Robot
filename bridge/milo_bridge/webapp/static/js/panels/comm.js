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
