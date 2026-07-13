const SAMPLE_RATE = 16000;   // must match the robot's capture rate
const CHANNELS = 2;

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

    const offBin = bus.onBinary((u8) => {
      if (!on || u8[0] !== 0x01) return;
      const bytes = u8.slice(1); // fresh, zero-offset buffer -- Int16Array requires a 2-byte-aligned offset
      const pcm = new Int16Array(bytes.buffer, 0, bytes.byteLength >> 1);
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
      playHead = Math.max(playHead, ctx.currentTime + 0.05);
      src.start(playHead);
      playHead += buf.duration;
    });

    btn.onclick = () => {
      on = !on;
      btn.textContent = on ? "◼ Mute" : "▶ Listen";
      btn.classList.toggle("active", on);
      if (on && !ctx) ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (on) { playHead = 0; drawVU(); }
      bus.send({ t: "audio", on });
    };
    return () => { offBin(); if (ctx) ctx.close(); bus.send({ t: "audio", on: false }); };
  },
};
