const SEND_MS = 100;

export default {
  id: "move", title: "Move", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;gap:14px;height:100%">
        <div id="pad" style="flex:1;max-width:220px;aspect-ratio:1;border:1px solid var(--line);
             border-radius:8px;position:relative;touch-action:none">
          <div id="knob" style="position:absolute;width:26px;height:26px;border-radius:50%;
               background:var(--ink);left:calc(50% - 13px);top:calc(50% - 13px)"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;flex:1">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, Q/E to turn</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const pad = el.querySelector("#pad"), knob = el.querySelector("#knob");
    const speed = el.querySelector("#speed");
    let vec = { vx: 0, vy: 0, yaw: 0 }, timer = null;

    function sending(active) {
      if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
      if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
    }
    const scaled = () => {
      const k = speed.value / 100;
      return { vx: vec.vx * k, vy: vec.vy * k, yaw: vec.yaw * 2 * k };
    };

    pad.addEventListener("pointerdown", (e) => {
      pad.setPointerCapture(e.pointerId);
      const rect = pad.getBoundingClientRect();
      const move = (ev) => {
        const x = Math.max(-1, Math.min(1, ((ev.clientX - rect.left) / rect.width) * 2 - 1));
        const y = Math.max(-1, Math.min(1, ((ev.clientY - rect.top) / rect.height) * 2 - 1));
        knob.style.left = `calc(${(x + 1) * 50}% - 13px)`;
        knob.style.top = `calc(${(y + 1) * 50}% - 13px)`;
        vec = { vx: -y, vy: x, yaw: 0 };
        sending(true);
      };
      const up = () => {
        pad.removeEventListener("pointermove", move);
        knob.style.left = "calc(50% - 13px)"; knob.style.top = "calc(50% - 13px)";
        vec = { vx: 0, vy: 0, yaw: 0 }; sending(false);
      };
      pad.addEventListener("pointermove", move);
      pad.addEventListener("pointerup", up, { once: true });
      move(e);
    });

    const keys = { w: [1,0,0], s: [-1,0,0], a: [0,-1,0], d: [0,1,0], q: [0,0,-1], e: [0,0,1],
      ArrowUp: [1,0,0], ArrowDown: [-1,0,0], ArrowLeft: [0,0,-1], ArrowRight: [0,0,1] };
    const down = new Set();
    const sync = () => {
      let vx = 0, vy = 0, yaw = 0;
      down.forEach((k) => { const [a,b,c] = keys[k]; vx += a; vy += b; yaw += c; });
      vec = { vx: Math.sign(vx), vy: Math.sign(vy), yaw: Math.sign(yaw) };
      sending(down.size > 0);
    };
    const kd = (e) => { if (keys[e.key] && !e.repeat && e.target.tagName !== "INPUT") { down.add(e.key); sync(); } };
    const ku = (e) => { if (keys[e.key]) { down.delete(e.key); sync(); } };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => { sending(false); window.removeEventListener("keydown", kd); window.removeEventListener("keyup", ku); };
  },
};
