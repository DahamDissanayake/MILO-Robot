// Status bar: connection, link/owner/gait, compact system stats, and
// page-level actions (Take Control, STOP, Tools, Logout, theme).
export function initStatusBar(el, bus, { onToolsToggle } = {}) {
  el.innerHTML = `
    <span class="brand">MILO</span>
    <span id="conn-dot" class="dot" title="connection"></span>
    <span id="owner-label" class="muted">owner: —</span>
    <button id="stat-toggle" class="btn ghost stat-toggle-btn" title="More stats">⋯</button>
    <div class="stat-group secondary" id="stat-secondary">
      <div class="stat"><span class="stat-label">Link</span><span class="stat-value" id="stat-link">—</span></div>
      <div class="stat"><span class="stat-label">Gait</span><span class="stat-value" id="stat-gait">—</span></div>
      <div class="stat"><span class="stat-label">CPU</span><span class="stat-value" id="stat-cpu">—</span></div>
      <div class="stat"><span class="stat-label">Temp</span><span class="stat-value" id="stat-temp">—</span></div>
      <div class="stat"><span class="stat-label">RAM</span><span class="stat-value" id="stat-mem">—</span></div>
      <div class="stat"><span class="stat-label">Up</span><span class="stat-value" id="stat-uptime">—</span></div>
    </div>
    <span class="spacer"></span>
    <button id="btn-control" class="btn">Take Control</button>
    <button id="btn-stop" class="btn danger">STOP</button>
    <button id="btn-tools" class="btn ghost">Tools</button>
    <button id="btn-logout" class="btn ghost">Logout</button>
    <button id="btn-theme" class="btn ghost" title="Toggle theme">◐</button>`;

  const dot = el.querySelector("#conn-dot");
  const owner = el.querySelector("#owner-label");
  const btnControl = el.querySelector("#btn-control");
  bus.on("_open", () => dot.classList.add("live"));
  bus.on("_close", () => { dot.classList.remove("live"); owner.textContent = "owner: —"; });
  bus.on("control", (m) => {
    owner.textContent = `owner: ${m.owner}`;
    btnControl.textContent = m.you ? "Release Control" : "Take Control";
    btnControl.classList.toggle("active", m.you);
  });
  bus.on("telemetry", (m) => {
    el.querySelector("#stat-link").textContent = m.link ?? "—";
    el.querySelector("#stat-gait").textContent = m.gait_backend ?? "—";
    el.querySelector("#stat-cpu").textContent = m.cpu_percent == null ? "—" : `${m.cpu_percent}%`;
    el.querySelector("#stat-temp").textContent = m.temp_c == null ? "—" : `${m.temp_c.toFixed(1)}°C`;
    el.querySelector("#stat-mem").textContent = m.mem_percent == null ? "—" : `${m.mem_percent}%`;
    el.querySelector("#stat-uptime").textContent = m.uptime_s == null ? "—" : `${Math.round(m.uptime_s)}s`;
  });

  btnControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
  el.querySelector("#btn-stop").onclick = () => bus.send({ t: "stop" });
  el.querySelector("#btn-logout").onclick = async () => {
    await fetch("/api/logout", { method: "POST" });
    location.href = "/login";
  };
  el.querySelector("#btn-theme").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("milo.theme", next);
  };
  el.querySelector("#stat-toggle").onclick = () => {
    el.querySelector("#stat-secondary").classList.toggle("expanded");
  };
  el.querySelector("#btn-tools").onclick = () => onToolsToggle?.();
}
