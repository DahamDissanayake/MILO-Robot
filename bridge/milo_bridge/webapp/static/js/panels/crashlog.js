export default {
  id: "crashlog", title: "Crash Log",
  mount(el) {
    el.innerHTML = `
      <div id="crash-count" style="font-weight:600;margin-bottom:6px">Crashes since last restart: —</div>
      <div id="crash-entries" style="font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere"></div>`;
    const countEl = el.querySelector("#crash-count");
    const entriesEl = el.querySelector("#crash-entries");

    function render(data) {
      countEl.textContent = `Crashes since last restart: ${data.count}`;
      if (data.entries.length === 0) {
        entriesEl.textContent = "No crashes recorded.";
        return;
      }
      entriesEl.innerHTML = data.entries.slice().reverse().map((e) => {
        const t = new Date(e.t * 1000).toLocaleString();
        return `<div style="margin-bottom:6px;border-bottom:1px solid var(--line);padding-bottom:4px">
          <div><b>${e.kind}</b> — ${t}</div>
          <div>${e.error}</div>
        </div>`;
      }).join("");
    }

    fetch("/api/crashes").then((r) => r.json()).then(render).catch(() => {
      countEl.textContent = "Crashes since last restart: (failed to load)";
    });
  },
};
