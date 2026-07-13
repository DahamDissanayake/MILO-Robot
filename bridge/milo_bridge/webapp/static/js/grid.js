// Masonry-style auto-packing card dashboard: drag to reorder, corner-resize,
// live compaction, persistence.
const KEY = "milo.layout.v1";
const ROW_PX = 80;
const NARROW_PX = 700;
const FULL_COLUMNS = 12;
const NARROW_COLUMNS = 2;
const MAX_W = 12;
const MAX_H = 10;
const MIN_W = 2;
const MIN_H = 2;

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}
function saveLayout(layout) { localStorage.setItem(KEY, JSON.stringify(layout)); }

// Bin-packs cards into the first legal top-left position, in `order`
// sequence, using `columns` logical columns. Returns Map<id, {x,y,w,h}>.
function compact(order, sizes, cardById, columns) {
  const placed = [];
  const positions = new Map();
  for (const id of order) {
    const card = cardById.get(id);
    if (!card) continue;
    const size = sizes[id] || { w: card.w, h: card.h };
    const w = Math.min(Math.max(size.w, MIN_W), Math.min(MAX_W, columns));
    const h = Math.min(Math.max(size.h, MIN_H), MAX_H);
    let bestX = 0, bestY = 0;
    outer:
    for (let y = 0; ; y++) {
      for (let x = 0; x <= columns - w; x++) {
        const overlaps = placed.some((p) =>
          x < p.x + p.w && x + w > p.x && y < p.y + p.h && y + h > p.y);
        if (!overlaps) { bestX = x; bestY = y; break outer; }
      }
    }
    placed.push({ x: bestX, y: bestY, w, h });
    positions.set(id, { x: bestX, y: bestY, w, h });
  }
  return positions;
}

export function initGrid(container, cards, bus) {
  const layout = loadLayout();
  layout.order = (layout.order || []).filter((id) => cards.some((c) => c.id === id));
  for (const c of cards) if (!layout.order.includes(c.id)) layout.order.push(c.id);
  layout.sizes = layout.sizes || {};
  layout.hidden = layout.hidden || [];

  const cardById = new Map(cards.map((c) => [c.id, c]));
  const shells = new Map();

  function columns() {
    return container.clientWidth < NARROW_PX ? NARROW_COLUMNS : FULL_COLUMNS;
  }

  function applyPositions() {
    const cols = columns();
    const cellPx = container.clientWidth / cols;
    const positions = compact(
      layout.order.filter((id) => !layout.hidden.includes(id)),
      layout.sizes, cardById, cols,
    );
    let maxBottom = 0;
    for (const [id, pos] of positions) {
      const el = shells.get(id);
      if (!el) continue;
      el.style.left = `${pos.x * cellPx}px`;
      el.style.top = `${pos.y * ROW_PX}px`;
      el.style.width = `${pos.w * cellPx}px`;
      el.style.height = `${pos.h * ROW_PX}px`;
      maxBottom = Math.max(maxBottom, (pos.y + pos.h) * ROW_PX);
    }
    container.style.height = `${maxBottom + 24}px`;
  }

  function render() {
    container.innerHTML = "";
    shells.clear();
    for (const id of layout.order) {
      if (layout.hidden.includes(id)) continue;
      const card = cardById.get(id);
      const el = buildShell(card);
      shells.set(id, el);
      container.appendChild(el);
    }
    applyPositions();
    updateLocks();
  }

  function buildShell(card) {
    const el = document.createElement("section");
    el.className = "card";
    el.dataset.id = card.id;
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

  // -- corner resize: live-recompacts on every pointermove ------------------
  function wireResize(el, card) {
    const handle = el.querySelector(".resize");
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.classList.add("resizing");
      const start = { x: e.clientX, y: e.clientY };
      const cellPx = container.clientWidth / columns();
      const startSize = layout.sizes[card.id] || { w: card.w, h: card.h };
      const move = (ev) => {
        const w = Math.max(MIN_W, Math.min(MAX_W, startSize.w + Math.round((ev.clientX - start.x) / cellPx)));
        const h = Math.max(MIN_H, Math.min(MAX_H, startSize.h + Math.round((ev.clientY - start.y) / ROW_PX)));
        layout.sizes[card.id] = { w, h };
        applyPositions();
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        el.classList.remove("resizing");
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

  // -- responsive reflow ------------------------------------------------------
  window.addEventListener("resize", applyPositions);

  // -- header helpers -------------------------------------------------------
  const menu = document.getElementById("add-menu");
  document.getElementById("btn-add").onclick = () => {
    menu.classList.toggle("hidden");
    menu.innerHTML = "";
    const hidden = layout.hidden;
    if (!hidden.length) menu.innerHTML = "<button disabled>all cards shown</button>";
    for (const id of [...hidden]) {
      const card = cardById.get(id);
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
