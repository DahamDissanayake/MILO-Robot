// Communication panel: merges the old Ears (listen) and Voice (speak) cards.
// Listening (headphones + VU meter) needs no control; push-to-talk and Say
// are individually locked until this tab holds control.
import { ICON_HEADPHONES, ICON_MIC } from "../icons.js";

const SAMPLE_RATE = 16000;   // must match the robot's capture/playback rate
const CHANNELS = 2;
const HOT_THRESHOLD = 0.5;   // level (0-1) above which the VU bar turns red

// Two listening presets, switchable live from the UI (see #audio-mode-row):
//
// Server sends 20ms chunks; scheduling each one as its own AudioBufferSourceNode
// makes playback exquisitely sensitive to network/GC jitter (any late chunk is
// an audible drop). Coalescing a few chunks into one larger buffer before
// scheduling cuts the node-creation rate, and a wider lookahead margin gives
// the pipeline (network + server queue) more room to catch up without an
// audible gap -- "quality" trades latency for that smoothness, fine for
// "listen to the room", not meant for interactive back-and-forth voice.
// "realtime" shrinks all three toward zero for much lower latency, accepting
// that a network hiccup is now more likely to produce an audible gap instead
// of being silently absorbed. Purely client-side -- the server's own queue
// depth (media_hub.py's AUDIO_QUEUE_SIZE) is a separate, shared latency floor
// this toggle doesn't touch.
const AUDIO_MODES = {
  quality: { coalesce: 4, lookahead: 0.15, maxLatency: 0.35 },
  realtime: { coalesce: 1, lookahead: 0.03, maxLatency: 0.08 },
};

export default {
  id: "comm", title: "Communication",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="comm-row">
        <div class="comm-controls">
          <div class="comm-listen-row">
            <button class="btn" id="headphones">${ICON_HEADPHONES}Listen</button>
            <div class="seg-row" id="audio-mode-row">
              <button class="btn" data-audio-mode="quality">Quality</button>
              <button class="btn" data-audio-mode="realtime">Realtime</button>
            </div>
          </div>
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

    // -- listening (headphones + VU meters): no control required -------------
    const headphones = el.querySelector("#headphones");
    const vuL = el.querySelector("#vu-l");
    const vuR = el.querySelector("#vu-r");
    let playCtx = null, playHead = 0, listening = false;
    let pending = [], pendingSamples = 0;
    let audioMode = "quality";

    const modeRow = el.querySelector("#audio-mode-row");
    function setAudioModeButtons(name) {
      modeRow.querySelectorAll("[data-audio-mode]").forEach((b) => b.classList.toggle("active", b.dataset.audioMode === name));
    }
    setAudioModeButtons(audioMode);
    modeRow.querySelectorAll("[data-audio-mode]").forEach((b) => {
      b.onclick = () => { audioMode = b.dataset.audioMode; setAudioModeButtons(audioMode); };
    });

    function setLevel(bar, level) {
      bar.style.setProperty("--level", Math.min(1, level).toFixed(3));
      bar.classList.toggle("hot", level >= HOT_THRESHOLD);
    }

    function schedule(pcm) {
      const frames = pcm.length / CHANNELS;
      const buf = playCtx.createBuffer(CHANNELS, frames, SAMPLE_RATE);
      const bars = [vuL, vuR];
      for (let ch = 0; ch < CHANNELS; ch++) {
        const out = buf.getChannelData(ch);
        let sumSq = 0;
        for (let i = 0; i < frames; i++) {
          const v = pcm[i * CHANNELS + ch] / 32768;
          out[i] = v; sumSq += v * v;
        }
        if (bars[ch]) setLevel(bars[ch], Math.sqrt(sumSq / frames) * 4);
      }
      const src = playCtx.createBufferSource();
      src.buffer = buf; src.connect(playCtx.destination);
      const { lookahead, maxLatency } = AUDIO_MODES[audioMode];
      if (playHead - playCtx.currentTime > maxLatency) {
        playHead = playCtx.currentTime + lookahead; // resync: drop the backlog, bound latency
      } else {
        playHead = Math.max(playHead, playCtx.currentTime + lookahead);
      }
      src.start(playHead);
      playHead += buf.duration;
    }

    function flushPending() {
      if (pendingSamples === 0) return;
      const merged = new Int16Array(pendingSamples);
      let offset = 0;
      for (const chunk of pending) { merged.set(chunk, offset); offset += chunk.length; }
      pending = []; pendingSamples = 0;
      schedule(merged);
    }

    const offBin = bus.onBinary((u8) => {
      if (!listening || u8[0] !== 0x01) return;
      const bytes = u8.slice(1); // fresh, zero-offset buffer -- Int16Array requires a 2-byte-aligned offset
      const pcm = new Int16Array(bytes.buffer, 0, bytes.byteLength >> 1);
      pending.push(pcm);
      pendingSamples += pcm.length;
      if (pending.length >= AUDIO_MODES[audioMode].coalesce) flushPending();
    });

    headphones.onclick = () => {
      listening = !listening;
      headphones.innerHTML = listening ? `${ICON_HEADPHONES}Mute` : `${ICON_HEADPHONES}Listen`;
      headphones.classList.toggle("active", listening);
      if (listening && !playCtx) playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (listening) {
        playHead = 0;
      } else {
        pending = []; pendingSamples = 0; setLevel(vuL, 0); setLevel(vuR, 0);
      }
      bus.send({ t: "audio", on: listening });
    };

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
      offBin(); offControl(); offClose();
      if (playCtx) playCtx.close();
      bus.send({ t: "audio", on: false });
      stopTalk();
    };
  },
};
