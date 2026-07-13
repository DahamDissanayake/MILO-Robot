// Sensors panel: live tiles for everything the robot actually reports
// (IMU attitude/gyro as a 3D plate, SoC temp, CPU%, RAM%, hardware
// presence), plus a Details toggle with a rolling system-history sparkline.
const HISTORY_LEN = 120;
const GYRO_HOT_DPS = 90; // deg/s magnitude before the Gyro plate glows

export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="sensor-tiles">
        <div class="sensor-tile">
          <div class="label">Pitch / Roll</div>
          <div class="imu-plate-wrap"><div class="imu-plate" id="plate-attitude">
            <div class="imu-face top"></div>
            <div class="imu-face front"></div>
            <div class="imu-face back"></div>
            <div class="imu-face left"></div>
            <div class="imu-face right"></div>
          </div></div>
        </div>
        <div class="sensor-tile">
          <div class="label">Gyro</div>
          <div class="imu-plate-wrap"><div class="imu-plate" id="plate-gyro">
            <div class="imu-face top"></div>
            <div class="imu-face front"></div>
            <div class="imu-face back"></div>
            <div class="imu-face left"></div>
            <div class="imu-face right"></div>
          </div></div>
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

    const plateA = el.querySelector("#plate-attitude");
    const plateG = el.querySelector("#plate-gyro");
    const attitude = { pitch: 0, roll: 0, ax: 0, ay: 0 };
    const gyroRate = { x: 0, y: 0, z: 0 };
    const gyroAngle = { x: 0, y: 0, z: 0 };

    function applyAttitude() {
      plateA.style.setProperty("--pitch", attitude.pitch.toFixed(2));
      plateA.style.setProperty("--roll", attitude.roll.toFixed(2));
      plateA.style.setProperty("--ax", attitude.ax.toFixed(3));
      plateA.style.setProperty("--ay", attitude.ay.toFixed(3));
    }
    applyAttitude(); // starts flat at 0 until the first telemetry message

    let lastFrame = null;
    let raf = requestAnimationFrame(spinGyro);
    function spinGyro(now) {
      if (lastFrame != null) {
        const dt = Math.min(0.1, (now - lastFrame) / 1000);
        gyroAngle.x += gyroRate.x * dt;
        gyroAngle.y += gyroRate.y * dt;
        gyroAngle.z += gyroRate.z * dt;
        plateG.style.setProperty("--pitch", gyroAngle.x.toFixed(2));
        plateG.style.setProperty("--roll", gyroAngle.y.toFixed(2));
        plateG.style.setProperty("--yaw", gyroAngle.z.toFixed(2));
        const mag = Math.hypot(gyroRate.x, gyroRate.y, gyroRate.z);
        plateG.classList.toggle("hot", mag >= GYRO_HOT_DPS);
      }
      lastFrame = now;
      raf = requestAnimationFrame(spinGyro);
    }

    const offT = bus.on("telemetry", (m) => {
      if (m.imu) {
        attitude.pitch = m.imu.pitch ?? attitude.pitch;
        attitude.roll = m.imu.roll ?? attitude.roll;
        attitude.ax = m.imu.accel?.[0] ?? attitude.ax;
        attitude.ay = m.imu.accel?.[1] ?? attitude.ay;
        applyAttitude();
        const gyro = m.imu.gyro ?? [];
        gyroRate.x = gyro[0] ?? gyroRate.x;
        gyroRate.y = gyro[1] ?? gyroRate.y;
        gyroRate.z = gyro[2] ?? gyroRate.z;
      }
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

    const details = el.querySelector("#sensor-details");
    const detailsBtn = el.querySelector("#sensor-details-btn");
    detailsBtn.onclick = () => {
      const nowHidden = details.classList.toggle("hidden");
      detailsBtn.textContent = nowHidden ? "Details ▾" : "Details ▴";
    };

    return () => {
      cancelAnimationFrame(raf);
      offT();
    };
  },
};
