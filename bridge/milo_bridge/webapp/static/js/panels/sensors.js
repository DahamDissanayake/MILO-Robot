// Sensors panel: live tiles for everything the robot actually reports
// (a single fused IMU 3D plate — accel+gyro, pitch/roll absolute, yaw
// relative — plus SoC temp, CPU%, RAM%, hardware presence), plus a
// Details toggle with a rolling system-history sparkline.
const HISTORY_LEN = 120;
const GYRO_HOT_DPS = 90; // deg/s magnitude before the IMU plate glows

export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="sensor-tiles">
        <div class="sensor-tile imu-tile">
          <div class="imu-tile-head">
            <div class="label">IMU</div>
            <button class="btn ghost imu-reset-btn" id="imu-reset-btn">Reset to Flat</button>
          </div>
          <div class="imu-plate-wrap"><div class="imu-plate" id="plate-imu">
            <div class="imu-face top"></div>
            <div class="imu-face front"></div>
            <div class="imu-face back"></div>
            <div class="imu-face left"></div>
            <div class="imu-face right"></div>
          </div></div>
          <div class="muted imu-reset-note" id="imu-reset-note"></div>
        </div>
        <div class="sensor-tile"><div class="label">SoC Temp</div><div class="value" id="tile-temp">—</div></div>
        <div class="sensor-tile"><div class="label">CPU</div><div class="value" id="tile-cpu">—</div></div>
        <div class="sensor-tile"><div class="label">RAM</div><div class="value" id="tile-ram">—</div></div>
        <div class="sensor-tile hw-tile"><div class="label">Hardware</div><div class="value" id="tile-hw">—</div></div>
      </div>
      <button class="btn ghost" id="sensor-details-btn" style="margin-top:10px">Details ▾</button>
      <div class="sensor-details hidden" id="sensor-details">
        <div class="spark-label">System — CPU % / RAM % / Temp °C</div>
        <canvas id="spark-system" width="360" height="50"></canvas>
      </div>`;

    const systemHist = [];
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

    const plate = el.querySelector("#plate-imu");
    const offImu = bus.on("imu", (m) => {
      plate.style.setProperty("--pitch", (m.pitch ?? 0).toFixed(2));
      plate.style.setProperty("--roll", (m.roll ?? 0).toFixed(2));
      plate.style.setProperty("--yaw", (m.yaw ?? 0).toFixed(2));
      plate.style.setProperty("--ax", (m.accel?.[0] ?? 0).toFixed(3));
      plate.style.setProperty("--ay", (m.accel?.[1] ?? 0).toFixed(3));
      const mag = Math.hypot(...(m.gyro ?? [0, 0, 0]));
      plate.classList.toggle("hot", mag >= GYRO_HOT_DPS);
    });

    const offT = bus.on("telemetry", (m) => {
      el.querySelector("#tile-temp").textContent = m.temp_c == null ? "n/a" : `${m.temp_c.toFixed(1)}°C`;
      el.querySelector("#tile-cpu").textContent = m.cpu_percent == null ? "n/a" : `${m.cpu_percent}%`;
      el.querySelector("#tile-ram").textContent = m.mem_percent == null ? "n/a" : `${m.mem_percent}%`;

      systemHist.push([m.cpu_percent || 0, m.mem_percent || 0, m.temp_c || 0]);
      if (systemHist.length > HISTORY_LEN) systemHist.shift();
      drawTraces(gS, cvS, systemHist, [0, 100]);
    });

    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#tile-hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `
          <div class="hw-row">
            <span class="hw-name">${k}</span>
            <span class="hw-state" style="color:${ok ? "var(--ok)" : "var(--danger)"}">${ok ? "Connected" : "Not connected"}</span>
          </div>`).join("");
    });

    const resetBtn = el.querySelector("#imu-reset-btn");
    const resetNote = el.querySelector("#imu-reset-note");
    resetBtn.onclick = async () => {
      resetBtn.disabled = true;
      const r = await fetch("/api/imu/zero", { method: "POST" })
        .then((r) => r.json()).catch(() => ({ error: "network" }));
      resetBtn.disabled = false;
      resetNote.textContent = r.error ? `✗ ${r.error}` : "✓ zeroed";
      setTimeout(() => { resetNote.textContent = ""; }, 1500);
    };

    const details = el.querySelector("#sensor-details");
    const detailsBtn = el.querySelector("#sensor-details-btn");
    detailsBtn.onclick = () => {
      const nowHidden = details.classList.toggle("hidden");
      detailsBtn.textContent = nowHidden ? "Details ▾" : "Details ▴";
    };

    return () => {
      offImu();
      offT();
    };
  },
};
