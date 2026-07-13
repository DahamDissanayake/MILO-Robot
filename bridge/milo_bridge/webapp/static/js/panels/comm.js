// Communication panel: merges the old Ears (listen) and Voice (speak) cards.
// Listening (headphones + VU meter) needs no control; push-to-talk and Say
// are individually locked until this tab holds control.
const SAMPLE_RATE = 16000;   // must match the robot's capture/playback rate
const CHANNELS = 2;
const HOT_THRESHOLD = 0.5;   // level (0-1) above which the VU bar turns red

export default {
  id: "comm", title: "Communication",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="comm-row">
        <div class="comm-controls">
          <button class="btn" id="headphones">🎧 Listen</button>
          <button class="btn" id="ptt">🎙 Hold to Talk</button>
          <div class="comm-say">
            <input id="say" placeholder="Type something to say…">
            <button class="btn" id="speak">Say</button>
          </div>
          <div class="muted" id="comm-note"></div>
        </div>
        <div class="vu-vertical" id="vu"><div class="vu-fill"></div></div>
      </div>`;

    // -- listening (headphones + VU meter): no control required -------------
    const headphones = el.querySelector("#headphones");
    const vu = el.querySelector("#vu");
    let playCtx = null, playHead = 0, listening = false;

    function setLevel(level) {
      vu.style.setProperty("--level", Math.min(1, level).toFixed(3));
      vu.classList.toggle("hot", level >= HOT_THRESHOLD);
    }

    const offBin = bus.onBinary((u8) => {
      if (!listening || u8[0] !== 0x01) return;
      const pcm = new Int16Array(u8.buffer, u8.byteOffset + 1, (u8.byteLength - 1) >> 1);
      const frames = pcm.length / CHANNELS;
      const buf = playCtx.createBuffer(CHANNELS, frames, SAMPLE_RATE);
      let sumSq = 0;
      for (let ch = 0; ch < CHANNELS; ch++) {
        const out = buf.getChannelData(ch);
        for (let i = 0; i < frames; i++) {
          const v = pcm[i * CHANNELS + ch] / 32768;
          out[i] = v; sumSq += v * v;
        }
      }
      setLevel(Math.sqrt(sumSq / (frames * CHANNELS)) * 4);
      const src = playCtx.createBufferSource();
      src.buffer = buf; src.connect(playCtx.destination);
      playHead = Math.max(playHead, playCtx.currentTime + 0.05);
      src.start(playHead);
      playHead += buf.duration;
    });

    headphones.onclick = () => {
      listening = !listening;
      headphones.textContent = listening ? "🎧 Mute" : "🎧 Listen";
      headphones.classList.toggle("active", listening);
      if (listening && !playCtx) playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (listening) playHead = 0; else setLevel(0);
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
