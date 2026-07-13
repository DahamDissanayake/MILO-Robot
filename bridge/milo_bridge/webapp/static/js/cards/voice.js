const SAMPLE_RATE = 16000;   // intercom send rate — must match robot playback

export default {
  id: "voice", title: "Voice (Speak)", w: 3, h: 3, needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px">
        <button class="btn" id="ptt">🎙 Hold to Talk</button>
        <div style="display:flex;gap:6px">
          <input id="say" placeholder="Type something to say…" style="flex:1">
          <button class="btn" id="speak">Say</button>
        </div>
        <div class="muted" id="voice-note"></div>
      </div>`;
    const note = el.querySelector("#voice-note");
    let ctx = null, stream = null, node = null;

    async function startTalk() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch { note.textContent = "microphone permission denied"; return; }
      ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      const src = ctx.createMediaStreamSource(stream);
      node = ctx.createScriptProcessor(2048, 1, 1);
      node.onaudioprocess = (ev) => {
        const f32 = ev.inputBuffer.getChannelData(0);
        const out = new Uint8Array(1 + f32.length * 2);
        out[0] = 0x02;
        const view = new DataView(out.buffer);
        for (let i = 0; i < f32.length; i++)
          view.setInt16(1 + i * 2, Math.max(-1, Math.min(1, f32[i])) * 32767, true);
        bus.sendBytes(out);
      };
      src.connect(node); node.connect(ctx.destination);
    }
    function stopTalk() {
      if (node) node.disconnect();
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (ctx) ctx.close();
      ctx = stream = node = null;
    }
    const ptt = el.querySelector("#ptt");
    ptt.onpointerdown = startTalk;
    ptt.onpointerup = ptt.onpointerleave = stopTalk;

    el.querySelector("#speak").onclick = async () => {
      const text = el.querySelector("#say").value.trim();
      if (!text) return;
      const r = await fetch("/api/speak", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, client: bus.clientId }),
      }).then((r) => r.json()).catch(() => ({ error: "network" }));
      note.textContent = r.error ? `✗ ${r.error}` : "✓ spoke";
    };
    return stopTalk;
  },
};
