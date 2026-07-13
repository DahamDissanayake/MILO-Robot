// Sensors panel: live tiles for everything the robot actually reports
// (IMU attitude/gyro, SoC temp, CPU%, RAM%, hardware presence), plus a
// Details toggle with rolling history sparklines.
const HISTORY_LEN = 120;

export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="sensor-tiles">
        <div class="sensor-tile"><div class="label">Pitch / Roll</div><div class="value" id="tile-attitude">—</div></div>
        <div class="sensor-tile"><div class="label">Gyro</div><div class="value" id="tile-gyro">—</div></div>
        <div class="sensor-tile"><div class="label">SoC Temp</div><div class="value" id="tile-temp">—</div></div>
        <div class="sensor-tile"><div class="label">CPU</div><div class="value" id="tile-cpu">—</div></div>
        <div class="sensor-tile"><div class="label">RAM</div><div class="value" id="tile-ram">—</div></div>
        <div class="sensor-tile"><div class="label">Hardware</div><div class="value" id="tile-hw">—</div></div>
      </div>
      <button class="btn ghost" id="sensor-details-btn" style="margin-top:10px">Details ▾</button>
      <div class="sensor-details hidden" id="sensor-details">
        <div class="spark-label">Attitude — pitch / roll (°)</div>
        <canvas id="spark-attitude" width="360" height="50"></canvas>
        <div class="spark-label">System — CPU % / RAM % / Temp °C</div>
        <canvas id="spark-system" width="360" height="50"></canvas>
      </div>`;

    const attitudeHist = [], systemHist = [];
    const cvA = el.querySelector("#spark-attitude"), gA = cvA.getContext("2d");
    const cvS = el.querySelector("#spark-system"), gS = cvS.getContext("2d");

    function drawTraces(ctx, canvas, hist, range) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (!hist.length) return;
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      const ok = getComputedStyle(document.documentElement).getPropertyValue("--ok");
      const colors = [ink, muted, ok];
      const series = hist[0].length;
      for (let k = 0; k < series; k++) {
        ctx.strokeStyle = colors[k % colors.length];
        ctx.beginPath();
        hist.forEach((row, i) => {
          const y = canvas.height - 5 - ((row[k] - range[0]) / (range[1] - range[0])) * (canvas.height - 10);
          i ? ctx.lineTo(i * 3, y) : ctx.moveTo(0, y);
        });
        ctx.stroke();
      }
    }

    const offT = bus.on("telemetry", (m) => {
      const pitch = m.imu?.pitch, roll = m.imu?.roll;
      const gyroZ = m.imu ? (m.imu.gyro_z ?? m.imu.gyro?.[2] ?? null) : null;
      el.querySelector("#tile-attitude").textContent =
        m.imu ? `${pitch?.toFixed(1) ?? "—"}° / ${roll?.toFixed(1) ?? "—"}°` : "n/a";
      el.querySelector("#tile-gyro").textContent = gyroZ == null ? "n/a" : `${gyroZ.toFixed(1)}°/s`;
      el.querySelector("#tile-temp").textContent = m.temp_c == null ? "n/a" : `${m.temp_c.toFixed(1)}°C`;
      el.querySelector("#tile-cpu").textContent = m.cpu_percent == null ? "n/a" : `${m.cpu_percent}%`;
      el.querySelector("#tile-ram").textContent = m.mem_percent == null ? "n/a" : `${m.mem_percent}%`;

      if (m.imu) {
        attitudeHist.push([pitch || 0, roll || 0]);
        if (attitudeHist.length > HISTORY_LEN) attitudeHist.shift();
        drawTraces(gA, cvA, attitudeHist, [-90, 90]);
      }
      systemHist.push([m.cpu_percent || 0, m.mem_percent || 0, m.temp_c || 0]);
      if (systemHist.length > HISTORY_LEN) systemHist.shift();
      drawTraces(gS, cvS, systemHist, [0, 100]);
    });

    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#tile-hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `<span style="margin-right:8px">${ok ? "●" : "○"} ${k}</span>`).join("");
    });

    const details = el.querySelector("#sensor-details");
    const detailsBtn = el.querySelector("#sensor-details-btn");
    detailsBtn.onclick = () => {
      const nowHidden = details.classList.toggle("hidden");
      detailsBtn.textContent = nowHidden ? "Details ▾" : "Details ▴";
    };

    return offT;
  },
};
