const SEND_MS = 100;
const MODES = ["raw", "balanced", "angled"];
const MODE_LABEL = { raw: "Raw", balanced: "Balanced", angled: "Angled" };

export default {
  id: "move", title: "Move", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;align-items:center">
        <div style="display:flex;gap:6px;width:100%;max-width:220px" id="mode-row">
          ${MODES.map((m) => `<button class="btn" data-mode="${m}" style="flex:1">${MODE_LABEL[m]}</button>`).join("")}
        </div>
        <div class="muted" id="mode-status">Mode: Raw</div>
        <div style="display:grid;grid-template-columns:56px 56px 56px;gap:6px">
          <div></div><button class="btn" data-dpad="up" style="font-size:20px">↑</button><div></div>
          <button class="btn" data-dpad="left" style="font-size:20px">←</button><div></div><button class="btn" data-dpad="right" style="font-size:20px">→</button>
          <div></div><button class="btn" data-dpad="down" style="font-size:20px">↓</button><div></div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn" data-dpad="turnleft" style="font-size:20px;width:56px">↺</button>
          <button class="btn" data-dpad="turnright" style="font-size:20px;width:56px">↻</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:220px">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, Q/E to turn</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const speed = el.querySelector("#speed");
    const modeStatus = el.querySelector("#mode-status");
    let vec = { vx: 0 }, timer = null;

    function setModeButtons(name) {
      el.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
      modeStatus.textContent = name === "raw" ? "Mode: Raw" : `Mode: ${MODE_LABEL[name]} — enabled`;
    }
    setModeButtons("raw");
    const offMode = bus.on("mode", (m) => setModeButtons(m.name));
    el.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });

    // -- continuous gait: forward/backward only (turning/strafing now use
    // the scripted turn_left/turn_right/crab_left/crab_right gaits below) --
    function sending(active) {
      if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
      if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
    }
    const scaled = () => ({ vx: vec.vx * (speed.value / 100), vy: 0, yaw: 0 });

    const gaitKeys = { w: 1, s: -1, ArrowUp: 1, ArrowDown: -1 };
    const down = new Set();
    const sync = () => {
      let vx = 0;
      down.forEach((k) => { vx += gaitKeys[k]; });
      vec = { vx: Math.sign(vx) };
      sending(down.size > 0);
    };
    const kd = (e) => { if (gaitKeys[e.key] !== undefined && !e.repeat && e.target.tagName !== "INPUT") { down.add(e.key); sync(); } };
    const ku = (e) => { if (gaitKeys[e.key] !== undefined) { down.delete(e.key); sync(); } };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    function bindGaitButton(dir, key) {
      const btn = el.querySelector(`[data-dpad="${dir}"]`);
      const press = (e) => { e.preventDefault(); down.add(key); sync(); };
      const release = () => { down.delete(key); sync(); };
      btn.addEventListener("pointerdown", press);
      btn.addEventListener("pointerup", release);
      btn.addEventListener("pointerleave", release);
      btn.addEventListener("pointercancel", release);
    }
    bindGaitButton("up", "w");
    bindGaitButton("down", "s");

    // -- turn/strafe: scripted gaits, held via a large cycle count on the
    // server and stopped with the existing universal {t:"stop"} message --
    function bindScripted(dir, msg) {
      const btn = el.querySelector(`[data-dpad="${dir}"]`);
      const press = (e) => { e.preventDefault(); bus.send(msg); };
      const release = () => bus.send({ t: "stop" });
      btn.addEventListener("pointerdown", press);
      btn.addEventListener("pointerup", release);
      btn.addEventListener("pointerleave", release);
      btn.addEventListener("pointercancel", release);
    }
    bindScripted("left", { t: "strafe", dir: "left" });
    bindScripted("right", { t: "strafe", dir: "right" });
    bindScripted("turnleft", { t: "turn", dir: "left" });
    bindScripted("turnright", { t: "turn", dir: "right" });

    const turnKeys = { q: "left", e: "right", ArrowLeft: "left", ArrowRight: "right" };
    const strafeKeys = { a: "left", d: "right" };
    const scriptedDown = new Set();
    const skd = (e) => {
      if (e.repeat || e.target.tagName === "INPUT" || scriptedDown.has(e.key)) return;
      if (turnKeys[e.key]) { scriptedDown.add(e.key); bus.send({ t: "turn", dir: turnKeys[e.key] }); }
      else if (strafeKeys[e.key]) { scriptedDown.add(e.key); bus.send({ t: "strafe", dir: strafeKeys[e.key] }); }
    };
    const sku = (e) => {
      if (turnKeys[e.key] || strafeKeys[e.key]) { scriptedDown.delete(e.key); bus.send({ t: "stop" }); }
    };
    window.addEventListener("keydown", skd);
    window.addEventListener("keyup", sku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => {
      sending(false);
      offMode();
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("keydown", skd);
      window.removeEventListener("keyup", sku);
    };
  },
};
