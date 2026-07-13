export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <canvas id="imu-spark" width="360" height="70" style="width:100%"></canvas>
      <div id="imu-now" class="muted" style="margin:4px 0 10px">imu: —</div>
      <div id="hw"></div>`;
    const hist = [];
    const cv = el.querySelector("#imu-spark"), g = cv.getContext("2d");
    const offT = bus.on("telemetry", (m) => {
      const now = el.querySelector("#imu-now");
      if (!m.imu) { now.textContent = "imu: n/a"; return; }
      now.textContent = `pitch ${m.imu.pitch?.toFixed(1)}°  roll ${m.imu.roll?.toFixed(1)}°`;
      hist.push([m.imu.pitch || 0, m.imu.roll || 0]);
      if (hist.length > 120) hist.shift();
      g.clearRect(0, 0, cv.width, cv.height);
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      [0, 1].forEach((k) => {
        g.strokeStyle = k === 0 ? ink : muted;
        g.beginPath();
        hist.forEach(([p, r], i) => {
          const v = k === 0 ? p : r;
          const y = 35 - (v / 90) * 33;
          i ? g.lineTo(i * 3, y) : g.moveTo(0, y);
        });
        g.stroke();
      });
    });
    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `<span style="margin-right:12px">${ok ? "●" : "○"} ${k}</span>`).join("");
    });
    return offT;
  },
};
