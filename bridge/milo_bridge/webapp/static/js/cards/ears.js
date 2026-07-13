const SAMPLE_RATE = 16000;   // must match the robot's capture rate
const CHANNELS = 2;
// Server sends 20ms chunks; scheduling each one as its own AudioBufferSourceNode
// makes playback exquisitely sensitive to network/GC jitter (any late chunk is
// an audible drop). Coalescing a few chunks into one larger buffer before
// scheduling cuts the node-creation rate 4x, and a wider lookahead margin
// gives the pipeline (network + server queue) more room to catch up without
// an audible gap. Both trade a bit of latency for smoothness -- fine for
// "listen to the room", not meant for interactive back-and-forth voice.
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

export default {
  id: "ears", title: "Ears (Listen)", w: 3, h: 3,
  mount(el, { bus }) {
    el.innerHTML = `
      <button class="btn" id="listen">▶ Listen</button>
      <canvas id="vu" width="220" height="48" style="margin-top:10px;width:100%"></canvas>
      <div class="muted" id="ears-note"></div>`;
    const btn = el.querySelector("#listen");
    const vu = el.querySelector("#vu").getContext("2d");
    let ctx = null, playHead = 0, on = false, levels = [0, 0];
    let pending = [], pendingSamples = 0;

    function drawVU() {
      const w = 220, h = 48;
      vu.clearRect(0, 0, w, h);
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      levels.forEach((lv, i) => {
        vu.fillStyle = ink;
        vu.fillRect(0, i * 26, Math.min(1, lv * 4) * w, 18);
      });
      if (on) requestAnimationFrame(drawVU);
    }

    function schedule(pcm) {
      const frames = pcm.length / CHANNELS;
      const buf = ctx.createBuffer(CHANNELS, frames, SAMPLE_RATE);
      let sum = [0, 0];
      for (let ch = 0; ch < CHANNELS; ch++) {
        const out = buf.getChannelData(ch);
        for (let i = 0; i < frames; i++) {
          const v = pcm[i * CHANNELS + ch] / 32768;
          out[i] = v; sum[ch] += v * v;
        }
      }
      levels = sum.map((s) => Math.sqrt(s / frames));
      const src = ctx.createBufferSource();
      src.buffer = buf; src.connect(ctx.destination);
      if (playHead - ctx.currentTime > MAX_LATENCY_S) {
        playHead = ctx.currentTime + LOOKAHEAD_S; // resync: drop the backlog, bound latency
      } else {
        playHead = Math.max(playHead, ctx.currentTime + LOOKAHEAD_S);
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
      if (!on || u8[0] !== 0x01) return;
      const bytes = u8.slice(1); // fresh, zero-offset buffer -- Int16Array requires a 2-byte-aligned offset
      const pcm = new Int16Array(bytes.buffer, 0, bytes.byteLength >> 1);
      pending.push(pcm);
      pendingSamples += pcm.length;
      if (pending.length >= COALESCE_CHUNKS) flushPending();
    });

    btn.onclick = () => {
      on = !on;
      btn.textContent = on ? "◼ Mute" : "▶ Listen";
      btn.classList.toggle("active", on);
      if (on && !ctx) ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (on) { playHead = 0; drawVU(); } else { pending = []; pendingSamples = 0; }
      bus.send({ t: "audio", on });
    };
    return () => { offBin(); if (ctx) ctx.close(); bus.send({ t: "audio", on: false }); };
  },
};
