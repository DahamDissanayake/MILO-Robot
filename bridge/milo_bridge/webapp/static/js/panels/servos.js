const SERVOS = ["R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4"];

export default {
  id: "servos", title: "Servo Test", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = SERVOS.map((s) => `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="width:26px;font-weight:600">${s}</span>
        <input type="range" min="0" max="180" value="90" data-servo="${s}" style="flex:1">
        <span data-val="${s}" style="width:34px;text-align:right">90°</span>
      </div>`).join("") +
      `<div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn" id="reset" style="flex:1">Reset (90°)</button>
        <button class="btn" id="standby" style="flex:1">Standby</button>
      </div>`;
    el.querySelectorAll("input[type=range]").forEach((sl) => {
      sl.oninput = () => {
        el.querySelector(`[data-val="${sl.dataset.servo}"]`).textContent = `${sl.value}°`;
        bus.send({ t: "servo", servo: sl.dataset.servo, deg: Number(sl.value) });
      };
    });
    el.querySelector("#reset").onclick = () => {
      SERVOS.forEach((s) => {
        el.querySelector(`[data-servo="${s}"]`).value = 90;
        el.querySelector(`[data-val="${s}"]`).textContent = "90°";
      });
      bus.send({ t: "reset" });
    };
    el.querySelector("#standby").onclick = () => bus.send({ t: "standby" });
  },
};
