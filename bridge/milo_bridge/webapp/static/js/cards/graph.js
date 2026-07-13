// Force-directed canvas view of the knowledge graph with text search.
export default {
  id: "graph", title: "Memory Graph", w: 8, h: 5,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <input id="gq" placeholder="Search memory… (name, type, anything)" style="flex:1">
        <button class="btn" id="gsearch">Search</button>
      </div>
      <canvas id="gcv" style="width:100%;height:calc(100% - 78px);background:var(--bg);border-radius:4px"></canvas>
      <div id="gdetail" class="muted" style="height:34px;overflow:auto;font-size:12px"></div>`;
    const cv = el.querySelector("#gcv"), g = cv.getContext("2d");
    let nodes = [], edges = [], selected = null, raf = null;

    function resize() { cv.width = cv.clientWidth; cv.height = cv.clientHeight; }
    resize();

    function load(data) {
      const W = cv.width, H = cv.height;
      nodes = data.nodes.map((n, i) => ({
        ...n, x: W / 2 + Math.cos(i) * 80, y: H / 2 + Math.sin(i) * 80, vx: 0, vy: 0,
      }));
      edges = data.edges;
      selected = null;
      if (!raf) tick();
    }

    function tick() {
      // physics: repulsion + springs + centering
      const W = cv.width, H = cv.height;
      for (const a of nodes) {
        a.vx += (W / 2 - a.x) * 0.001; a.vy += (H / 2 - a.y) * 0.001;
        for (const b of nodes) {
          if (a === b) continue;
          const dx = a.x - b.x, dy = a.y - b.y;
          const d2 = Math.max(100, dx * dx + dy * dy);
          a.vx += (dx / d2) * 600; a.vy += (dy / d2) * 600;
        }
      }
      for (const e of edges) {
        const a = nodes.find((n) => n.id === e.src), b = nodes.find((n) => n.id === e.dst);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        a.vx += dx * 0.003; a.vy += dy * 0.003;
        b.vx -= dx * 0.003; b.vy -= dy * 0.003;
      }
      for (const n of nodes) {
        n.vx *= 0.85; n.vy *= 0.85; n.x += n.vx; n.y += n.vy;
      }
      draw();
      raf = requestAnimationFrame(tick);
    }

    function draw() {
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      const ok = getComputedStyle(document.documentElement).getPropertyValue("--ok");
      g.clearRect(0, 0, cv.width, cv.height);
      g.strokeStyle = muted;
      for (const e of edges) {
        const a = nodes.find((n) => n.id === e.src), b = nodes.find((n) => n.id === e.dst);
        if (!a || !b) continue;
        g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
      }
      for (const n of nodes) {
        g.fillStyle = n === selected ? ok : ink;
        g.beginPath(); g.arc(n.x, n.y, 7, 0, 7); g.fill();
        g.fillStyle = muted; g.font = "10px sans-serif";
        g.fillText(`${n.props?.name || n.type}#${n.id}`, n.x + 9, n.y + 3);
      }
    }

    cv.onclick = (ev) => {
      const r = cv.getBoundingClientRect();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      selected = nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < 120) || null;
      el.querySelector("#gdetail").textContent = selected
        ? `#${selected.id} [${selected.type}] ${JSON.stringify(selected.props)}`
        : "";
    };

    async function search() {
      resize();
      const q = el.querySelector("#gq").value.trim();
      if (!q) return;
      const data = await fetch(`/api/graph/search?q=${encodeURIComponent(q)}`)
        .then((r) => r.json()).catch(() => ({ nodes: [], edges: [] }));
      load(data);
      el.querySelector("#gdetail").textContent =
        data.nodes.length ? `${data.nodes.length} nodes, ${data.edges.length} edges` : "no matches";
    }
    el.querySelector("#gsearch").onclick = search;
    el.querySelector("#gq").onkeydown = (e) => { if (e.key === "Enter") search(); };

    return () => { if (raf) cancelAnimationFrame(raf); };
  },
};
