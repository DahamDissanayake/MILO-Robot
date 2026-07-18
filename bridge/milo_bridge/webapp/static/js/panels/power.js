function slideConfirm(el, { label, onConfirm }) {
  el.innerHTML = `
    <div style="margin-bottom:10px">
      <div style="margin-bottom:4px">${label}</div>
      <input type="range" min="0" max="100" value="0" class="slide-confirm" style="width:100%;accent-color:var(--danger)">
      <div class="slide-status" style="font-size:11px;color:var(--muted)">Slide to confirm</div>
    </div>`;
  const slider = el.querySelector(".slide-confirm");
  const status = el.querySelector(".slide-status");
  let fired = false;
  const ctl = { setStatus: (text) => { status.textContent = text; } };
  slider.oninput = () => {
    if (fired) return;
    if (Number(slider.value) >= 100) {
      fired = true;
      slider.disabled = true;
      ctl.setStatus("Confirmed — sending…");
      onConfirm(ctl);
    }
  };
  slider.onchange = () => {
    if (!fired) {
      slider.value = 0;
      ctl.setStatus("Slide to confirm");
    }
  };
  return ctl;
}

async function postAction(path, ctl, pendingText) {
  try {
    const r = await fetch(path, { method: "POST" });
    const data = await r.json();
    ctl.setStatus(data.ok ? pendingText : `Failed: ${data.error || "unknown error"}`);
  } catch {
    ctl.setStatus("Failed: request error");
  }
}

export default {
  id: "power", title: "Power",
  mount(el) {
    el.innerHTML = `<div id="restart-slot"></div><div id="shutdown-slot"></div>`;
    slideConfirm(el.querySelector("#restart-slot"), {
      label: "Full Restart (reboot the Pi)",
      onConfirm: (ctl) => postAction("/api/system/restart", ctl, "Rebooting…"),
    });
    slideConfirm(el.querySelector("#shutdown-slot"), {
      label: "Shutdown (power off the Pi)",
      onConfirm: (ctl) => postAction("/api/system/shutdown", ctl, "Shutting down…"),
    });
  },
};
