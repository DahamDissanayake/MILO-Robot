export default {
  id: "status", title: "Status", w: 4, h: 3,
  mount(el, { bus }) {
    el.innerHTML = `<table style="width:100%;border-collapse:collapse" id="st"></table>`;
    const rows = [
      ["link", "Brain link"], ["owner", "Control owner"], ["gait_backend", "Gait backend"],
      ["cpu_percent", "CPU %"], ["temp_c", "SoC temp °C"], ["mem_percent", "RAM %"],
      ["uptime_s", "Web uptime s"],
    ];
    const table = el.querySelector("#st");
    table.innerHTML = rows.map(([k, label]) =>
      `<tr><td class="muted" style="padding:2px 8px 2px 0">${label}</td>
       <td id="st-${k}" style="text-align:right">—</td></tr>`).join("");
    const off = bus.on("telemetry", (m) => {
      for (const [k] of rows) {
        const cell = el.querySelector(`#st-${k}`);
        if (cell) cell.textContent = m[k] == null ? "n/a" : m[k];
      }
    });
    return off;
  },
};
