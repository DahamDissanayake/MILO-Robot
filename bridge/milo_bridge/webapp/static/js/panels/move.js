import { createPilotController } from "../pilot.js";

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
          <button class="btn" data-dpad="lookup" style="width:56px">Up</button>
          <button class="btn" data-dpad="lookdown" style="width:56px">Down</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:220px">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, A/D to turn, hold Q/E to look up/down</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const speed = el.querySelector("#speed");
    const modeStatus = el.querySelector("#mode-status");
    const pilot = createPilotController(bus, () => speed.value);

    function setModeButtons(name) {
      el.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
      modeStatus.textContent = name === "raw" ? "Mode: Raw" : `Mode: ${MODE_LABEL[name]} — enabled`;
    }
    // Balanced is the robot's actual default (set in GaitEngine), not "raw" --
    // this is just the best guess until the first telemetry tick confirms the
    // real mode, which also covers a tab opened after someone else changed it.
    setModeButtons("balanced");
    const offMode = bus.on("mode", (m) => setModeButtons(m.name));
    const offTelemetry = bus.on("telemetry", (m) => { if (m.gait_mode) setModeButtons(m.gait_mode); });
    el.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });

    // -- continuous gait: forward/backward only (turning uses the scripted
    // turn_left/turn_right gait below; look up/down are held poses, not
    // part of this velocity-command path) --
    pilot.bindGaitButton(el.querySelector('[data-dpad="up"]'), "btn-up", 1);
    pilot.bindGaitButton(el.querySelector('[data-dpad="down"]'), "btn-down", -1);

    const gaitKeys = { w: 1, s: -1, ArrowUp: 1, ArrowDown: -1 };
    const kd = (e) => { if (gaitKeys[e.key] !== undefined && !e.repeat && e.target.tagName !== "INPUT") pilot.gaitPress(e.key, gaitKeys[e.key]); };
    const ku = (e) => { if (gaitKeys[e.key] !== undefined) pilot.gaitRelease(e.key); };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    // -- turn: scripted gait, held via a large cycle count on the server
    // and stopped with the existing universal {t:"stop"} message --
    pilot.bindTurnButton(el.querySelector('[data-dpad="left"]'), "left");
    pilot.bindTurnButton(el.querySelector('[data-dpad="right"]'), "right");

    const turnKeys = { a: "left", d: "right", ArrowLeft: "left", ArrowRight: "right" };
    const scriptedDown = new Set();
    const skd = (e) => {
      if (e.repeat || e.target.tagName === "INPUT" || scriptedDown.has(e.key) || !turnKeys[e.key]) return;
      scriptedDown.add(e.key);
      pilot.turnPress(turnKeys[e.key]);
    };
    const sku = (e) => {
      if (turnKeys[e.key]) { scriptedDown.delete(e.key); pilot.turnRelease(); }
    };
    window.addEventListener("keydown", skd);
    window.addEventListener("keyup", sku);

    // -- look up/down: held, not toggled. Press and hold to move to the
    // tilted pose and stay there; release to return to stand. pilot.js
    // owns the actual bus messages -- this block only owns the .active
    // highlight, which is specific to this panel's own buttons. --
    function setLookButtons(dir) {
      el.querySelector('[data-dpad="lookup"]').classList.toggle("active", dir === "up");
      el.querySelector('[data-dpad="lookdown"]').classList.toggle("active", dir === "down");
    }
    pilot.bindLookButton(el.querySelector('[data-dpad="lookup"]'), "up");
    pilot.bindLookButton(el.querySelector('[data-dpad="lookdown"]'), "down");
    ["lookup", "lookdown"].forEach((id) => {
      const btn = el.querySelector(`[data-dpad="${id}"]`);
      const dir = id === "lookup" ? "up" : "down";
      const on = () => setLookButtons(dir);
      const off = () => setLookButtons(null);
      btn.addEventListener("pointerdown", on);
      btn.addEventListener("pointerup", off);
      btn.addEventListener("pointerleave", off);
      btn.addEventListener("pointercancel", off);
    });

    const lookKeys = { q: "up", e: "down" };
    const lookKeyDown = new Set();
    const lkd = (e) => {
      if (e.repeat || e.target.tagName === "INPUT" || !lookKeys[e.key] || lookKeyDown.has(e.key)) return;
      lookKeyDown.add(e.key);
      pilot.lookPress(lookKeys[e.key]);
      setLookButtons(lookKeys[e.key]);
    };
    const lku = (e) => {
      if (lookKeys[e.key]) { lookKeyDown.delete(e.key); pilot.lookRelease(); setLookButtons(null); }
    };
    window.addEventListener("keydown", lkd);
    window.addEventListener("keyup", lku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => {
      pilot.stop();
      offMode();
      offTelemetry();
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("keydown", skd);
      window.removeEventListener("keyup", sku);
      window.removeEventListener("keydown", lkd);
      window.removeEventListener("keyup", lku);
    };
  },
};
