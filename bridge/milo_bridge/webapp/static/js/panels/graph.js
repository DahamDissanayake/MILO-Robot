// Memory Graph panel: shows the whole knowledge graph by default (Obsidian-
// style force layout, growing live via polling since there's no WS push for
// graph mutations), with search highlighting matches instead of replacing
// the view. Nodes can be dragged (pinned while held), the whole view can be
// panned by dragging empty space, and the force sim is tuned to settle into
// a compact circular cluster.
const POLL_MS = 5000;

export default {
  id: "graph", title: "Memory Graph",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="graph-search">
        <input id="gq" placeholder="Search memory… (name, type, anything)">
        <button class="btn" id="gsearch">Search</button>
        <button class="btn ghost" id="gclear">Clear</button>
      </div>
      <canvas id="graph-canvas"></canvas>
      <div id="graph-detail" class="muted"></div>`;
    const cv = el.querySelector("#graph-canvas"), g = cv.getContext("2d");
    const detail = el.querySelector("#graph-detail");
    let nodes = [], edges = [], selected = null, highlighted = null, raf = null;
    let offsetX = 0, offsetY = 0, dragNode = null, panning = false, lastPX = 0, lastPY = 0, downX = 0, downY = 0, moved = false;

    function resize() { cv.width = cv.clientWidth; cv.height = cv.clientHeight; draw(); }
    resize();
    window.addEventListener("resize", resize);

    function merge(data) {
      const W = cv.width, H = cv.height;
      const byId = new Map(nodes.map((n) => [n.id, n]));
      let changed = false;
      for (const n of data.nodes) {
        if (byId.has(n.id)) { Object.assign(byId.get(n.id), n); continue; }
        nodes.push({
          ...n, x: W / 2 + (Math.random() - 0.5) * 40, y: H / 2 + (Math.random() - 0.5) * 40,
          vx: 0, vy: 0, born: performance.now(),
        });
        changed = true;
      }
      const edgeKey = (e) => `${e.src}:${e.dst}:${e.type}`;
      const existing = new Set(edges.map(edgeKey));
      for (const e of data.edges) if (!existing.has(edgeKey(e))) { edges.push(e); changed = true; }
      if (changed && !raf) tick();
    }

    async function loadAll() {
      const data = await fetch("/api/graph/search?limit=200")
        .then((r) => r.json()).catch(() => ({ nodes: [], edges: [] }));
      merge(data);
      if (!highlighted) {
        detail.textContent = nodes.length ? `${nodes.length} nodes, ${edges.length} edges` : "memory is empty";
      }
    }

    function tick() {
      const W = cv.width, H = cv.height;
      for (const a of nodes) {
        a.vx += (W / 2 - a.x) * 0.02; a.vy += (H / 2 - a.y) * 0.02;
        for (const b of nodes) {
          if (a === b) continue;
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = Math.max(64, dx * dx + dy * dy);
          a.vx += (dx / d2) * 120; a.vy += (dy / d2) * 120;
        }
      }
      for (const e of edges) {
        const a = nodes.find((n) => n.id === e.src), b = nodes.find((n) => n.id === e.dst);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        a.vx += dx * 0.01; a.vy += dy * 0.01;
        b.vx -= dx * 0.01; b.vy -= dy * 0.01;
      }
      let settled = true;
      for (const n of nodes) {
        if (n === dragNode) { n.vx = 0; n.vy = 0; continue; }
        n.vx *= 0.85; n.vy *= 0.85; n.x += n.vx; n.y += n.vy;
        if (Math.abs(n.vx) > 0.05 || Math.abs(n.vy) > 0.05) settled = false;
      }
      draw();
      raf = settled ? null : requestAnimationFrame(tick);
    }

    function draw() {
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      const ok = getComputedStyle(document.documentElement).getPropertyValue("--ok");
      g.clearRect(0, 0, cv.width, cv.height);
      g.save();
      g.translate(offsetX, offsetY);
      g.strokeStyle = muted;
      for (const e of edges) {
        const a = nodes.find((n) => n.id === e.src), b = nodes.find((n) => n.id === e.dst);
        if (!a || !b) continue;
        g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
      }
      const now = performance.now();
      for (const n of nodes) {
        const grown = Math.min(1, (now - (n.born || 0)) / 400);
        const radius = (n === selected ? 8 : 6) * (0.4 + 0.6 * grown);
        const isMatch = highlighted && highlighted.has(n.id);
        g.globalAlpha = highlighted ? (isMatch ? 1 : 0.25) : 1;
        g.fillStyle = n === selected ? ok : (isMatch ? ok : ink);
        g.beginPath(); g.arc(n.x, n.y, radius, 0, Math.PI * 2); g.fill();
        g.fillStyle = muted; g.font = "10px sans-serif";
        g.fillText(`${n.props?.name || n.type}#${n.id}`, n.x + 9, n.y + 3);
      }
      g.restore();
      g.globalAlpha = 1;
    }

    function hitTest(ev) {
      const r = cv.getBoundingClientRect();
      const x = ev.clientX - r.left - offsetX, y = ev.clientY - r.top - offsetY;
      return nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < 120) || null;
    }

    cv.onpointerdown = (ev) => {
      const hit = hitTest(ev);
      downX = ev.clientX; downY = ev.clientY;
      lastPX = ev.clientX; lastPY = ev.clientY;
      moved = false;
      if (hit) { dragNode = hit; cv.setPointerCapture(ev.pointerId); }
      else { panning = true; }
    };

    cv.onpointermove = (ev) => {
      if (dragNode) {
        const r = cv.getBoundingClientRect();
        dragNode.x = ev.clientX - r.left - offsetX;
        dragNode.y = ev.clientY - r.top - offsetY;
        dragNode.vx = 0; dragNode.vy = 0;
        moved = true;
        if (!raf) tick();
      } else if (panning) {
        offsetX += ev.clientX - lastPX; offsetY += ev.clientY - lastPY;
        lastPX = ev.clientX; lastPY = ev.clientY;
        moved = true;
        draw();
      }
    };

    cv.onpointerup = (ev) => {
      if (!moved) {
        selected = hitTest(ev);
        detail.textContent = selected
          ? `#${selected.id} [${selected.type}] ${JSON.stringify(selected.props)}`
          : "";
      }
      dragNode = null; panning = false;
      draw();
    };

    async function search() {
      const q = el.querySelector("#gq").value.trim();
      if (!q) { highlighted = null; draw(); return; }
      const data = await fetch(`/api/graph/search?q=${encodeURIComponent(q)}`)
        .then((r) => r.json()).catch(() => ({ nodes: [], edges: [] }));
      merge(data);
      highlighted = new Set(data.nodes.map((n) => n.id));
      detail.textContent = data.nodes.length ? `${data.nodes.length} matches highlighted` : "no matches";
      draw();
    }
    el.querySelector("#gsearch").onclick = search;
    el.querySelector("#gq").onkeydown = (e) => { if (e.key === "Enter") search(); };
    el.querySelector("#gclear").onclick = () => {
      el.querySelector("#gq").value = ""; highlighted = null; draw();
    };

    loadAll();
    const pollId = setInterval(loadAll, POLL_MS);
    return () => { clearInterval(pollId); if (raf) cancelAnimationFrame(raf); window.removeEventListener("resize", resize); };
  },
};
