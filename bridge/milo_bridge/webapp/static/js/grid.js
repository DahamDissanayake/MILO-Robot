// CSS-grid card dashboard: drag to reorder, corner-resize, persistence.
const KEY = "milo.layout.v1";

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function saveLayout(layout) { localStorage.setItem(KEY, JSON.stringify(layout)); }

export function initGrid(container, cards, bus) {
  const layout = loadLayout();
  layout.order = (layout.order || []).filter((id) => cards.some((c) => c.id === id));
  for (const c of cards) if (!layout.order.includes(c.id)) layout.order.push(c.id);
  layout.sizes = layout.sizes || {};
  layout.hidden = layout.hidden || [];

  const shells = new Map();

  function render() {
    container.innerHTML = "";
    for (const id of layout.order) {
      if (layout.hidden.includes(id)) continue;
      const card = cards.find((c) => c.id === id);
      const el = buildShell(card);
      shells.set(id, el);
      container.appendChild(el);
    }
    updateLocks();
  }

  function buildShell(card) {
    const size = layout.sizes[card.id] || { w: card.w, h: card.h };
    const el = document.createElement("section");
    el.className = "card";
    el.dataset.id = card.id;
    el.style.gridColumn = `span ${size.w}`;
    el.style.gridRow = `span ${size.h}`;
    el.innerHTML = `<div class="card-head"><span>${card.title}</span>
      <button class="close" title="Hide card">✕</button></div>
      <div class="card-body"></div><div class="resize"></div>`;
    el.querySelector(".close").onclick = () => {
      layout.hidden.push(card.id); saveLayout(layout); render();
    };
    wireDrag(el);
    wireResize(el, card);
    card.mount(el.querySelector(".card-body"), { bus });
    return el;
  }

  // -- drag to reorder ------------------------------------------------------
  let dragId = null;
  function wireDrag(el) {
    const head = el.querySelector(".card-head");
    head.addEventListener("pointerdown", (e) => {
      if (e.target.classList.contains("close")) return;
      dragId = el.dataset.id; el.classList.add("dragging");
      const move = (ev) => {
        const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".card");
        document.querySelectorAll(".card.drop-target").forEach((c) => c.classList.remove("drop-target"));
        if (over && over.dataset.id !== dragId) over.classList.add("drop-target");
      };
      const up = (ev) => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        el.classList.remove("dragging");
        const over = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".card");
        document.querySelectorAll(".card.drop-target").forEach((c) => c.classList.remove("drop-target"));
        if (over && over.dataset.id !== dragId) {
          const from = layout.order.indexOf(dragId);
          const to = layout.order.indexOf(over.dataset.id);
          layout.order.splice(from, 1);
          layout.order.splice(to, 0, dragId);
          saveLayout(layout); render();
        }
        dragId = null;
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  // -- corner resize --------------------------------------------------------
  function wireResize(el, card) {
    const handle = el.querySelector(".resize");
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const start = { x: e.clientX, y: e.clientY };
      const cell = container.getBoundingClientRect().width / 12;
      const size = layout.sizes[card.id] || { w: card.w, h: card.h };
      const move = (ev) => {
        const w = Math.max(2, Math.min(12, size.w + Math.round((ev.clientX - start.x) / cell)));
        const h = Math.max(2, Math.min(10, size.h + Math.round((ev.clientY - start.y) / 80)));
        el.style.gridColumn = `span ${w}`;
        el.style.gridRow = `span ${h}`;
        layout.sizes[card.id] = { w, h };
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        saveLayout(layout);
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  // -- control locking ------------------------------------------------------
  function updateLocks() {
    for (const card of cards) {
      const el = shells.get(card.id);
      if (el && card.needsControl) el.classList.toggle("locked", !bus.controlled);
    }
  }
  bus.on("control", updateLocks);
  bus.on("_close", updateLocks);

  // -- header helpers -------------------------------------------------------
  const menu = document.getElementById("add-menu");
  document.getElementById("btn-add").onclick = () => {
    menu.classList.toggle("hidden");
    menu.innerHTML = "";
    const hidden = layout.hidden;
    if (!hidden.length) menu.innerHTML = "<button disabled>all cards shown</button>";
    for (const id of [...hidden]) {
      const card = cards.find((c) => c.id === id);
      const b = document.createElement("button");
      b.textContent = card.title;
      b.onclick = () => {
        layout.hidden = layout.hidden.filter((x) => x !== id);
        saveLayout(layout); menu.classList.add("hidden"); render();
      };
      menu.appendChild(b);
    }
  };
  document.getElementById("btn-reset").onclick = () => {
    localStorage.removeItem(KEY); location.reload();
  };

  render();
}
