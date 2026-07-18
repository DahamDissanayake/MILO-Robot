const STYLE_ID = "power-panel-styles";

function ensureStyles() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .slide-track {
      position: relative;
      height: 52px;
      border-radius: 26px;
      background: color-mix(in srgb, var(--danger) 14%, var(--surface));
      border: 1px solid var(--danger);
      overflow: hidden;
      user-select: none;
      touch-action: none;
      margin-bottom: 14px;
    }
    .slide-fill {
      position: absolute; inset: 0; width: 0%;
      background: var(--danger);
      opacity: 0.28;
    }
    .slide-track.dragging .slide-fill { transition: none; }
    .slide-track:not(.dragging) .slide-fill { transition: width 0.2s cubic-bezier(.2,.8,.2,1); }
    .slide-label {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      color: var(--danger); font-weight: 600; font-size: 13px;
      letter-spacing: 0.02em;
      pointer-events: none;
    }
    .slide-thumb {
      position: absolute; top: 3px; left: 3px;
      width: 46px; height: 46px;
      border-radius: 50%;
      background: var(--danger);
      color: #fff;
      display: flex; align-items: center; justify-content: center;
      font-size: 20px; line-height: 1;
      cursor: grab;
      box-shadow: 0 1px 4px rgba(0,0,0,0.35);
      touch-action: none;
    }
    .slide-track:not(.dragging) .slide-thumb { transition: left 0.2s cubic-bezier(.2,.8,.2,1); }
    .slide-thumb:active { cursor: grabbing; }
    .slide-track.confirmed .slide-thumb { background: var(--ok); cursor: default; }
    .slide-track.confirmed .slide-label { color: var(--ok); font-weight: 700; }
  `;
  document.head.appendChild(style);
}

const THUMB_SIZE = 46;
const EDGE = 3;
const CONFIRM_THRESHOLD = 0.85; // iOS-style forgiving "close enough to the end" fraction

function slideConfirm(el, { label, onConfirm }) {
  ensureStyles();
  el.innerHTML = `
    <div class="slide-track">
      <div class="slide-fill"></div>
      <div class="slide-label">${label}</div>
      <div class="slide-thumb" role="slider" aria-label="${label}">›</div>
    </div>`;
  const track = el.querySelector(".slide-track");
  const fill = el.querySelector(".slide-fill");
  const thumb = el.querySelector(".slide-thumb");
  const labelEl = el.querySelector(".slide-label");

  let fired = false;
  let dragging = false;
  let startX = 0;
  let thumbStartPx = 0;

  const ctl = { setStatus: (text) => { labelEl.textContent = text; } };

  function maxPx() {
    return Math.max(0, track.clientWidth - THUMB_SIZE - EDGE * 2);
  }

  function place(px) {
    const clamped = Math.min(maxPx(), Math.max(0, px));
    thumb.style.left = `${EDGE + clamped}px`;
    const m = maxPx();
    const pct = m > 0 ? (clamped / m) * 100 : 0;
    fill.style.width = `${pct}%`;
    return clamped;
  }

  function reset() {
    track.classList.remove("dragging");
    place(0);
  }

  function confirm() {
    fired = true;
    track.classList.remove("dragging");
    track.classList.add("confirmed");
    place(maxPx());
    ctl.setStatus("Confirmed — sending…");
    onConfirm(ctl);
  }

  thumb.addEventListener("pointerdown", (e) => {
    if (fired) return;
    dragging = true;
    track.classList.add("dragging");
    startX = e.clientX;
    thumbStartPx = parseFloat(thumb.style.left || `${EDGE}`) - EDGE;
    thumb.setPointerCapture(e.pointerId);
  });

  thumb.addEventListener("pointermove", (e) => {
    if (!dragging || fired) return;
    place(thumbStartPx + (e.clientX - startX));
  });

  function endDrag(e) {
    if (!dragging || fired) return;
    dragging = false;
    const finalPx = place(thumbStartPx + (e.clientX - startX));
    if (maxPx() > 0 && finalPx >= maxPx() * CONFIRM_THRESHOLD) {
      confirm();
    } else {
      reset();
    }
  }

  thumb.addEventListener("pointerup", endDrag);
  thumb.addEventListener("pointercancel", () => {
    if (fired) return;
    dragging = false;
    reset();
  });

  // Initial thumb position depends on the track's laid-out width, which
  // isn't available until after this element is attached and rendered.
  requestAnimationFrame(() => place(0));

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
      label: "Slide to Full Restart (reboot the Pi)",
      onConfirm: (ctl) => postAction("/api/system/restart", ctl, "Rebooting…"),
    });
    slideConfirm(el.querySelector("#shutdown-slot"), {
      label: "Slide to Shutdown (power off the Pi)",
      onConfirm: (ctl) => postAction("/api/system/shutdown", ctl, "Shutting down…"),
    });
  },
};
