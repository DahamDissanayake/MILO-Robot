# Webapp Console Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Milo web dashboard's free-form draggable card grid with a fixed "cockpit" console (status bar, camera+controls+communication center, sensors side panel, always-visible memory graph, tools drawer) that is genuinely responsive on desktop and mobile.

**Architecture:** Same `aiohttp` backend + hand-written ES module frontend, zero build step. The existing WebSocket client (`bus.js`) and panel `mount(el, {bus}) -> cleanup` contract are kept; the drag/resize grid engine (`grid.js`) is replaced by a fixed-zone layout module (`layout.js`), and the flat card registry becomes a zone-grouped panel registry.

**Tech Stack:** Vanilla ES modules, hand-written CSS, `aiohttp` (Python), `pytest` + `pytest-asyncio` for backend tests. No npm, no bundler, no JS test framework — matches the existing codebase exactly.

## Global Constraints

- No build step. Every JS file is a plain ES module served as a static file — no bundler, no transpilation, no `package.json`.
- No change to `ControlBroker` semantics (Take Control exclusivity, brain-vs-web arbitration, heartbeat expiry, STOP's unconditional exemption). Only two backend files change in this plan (`graph/store.py`, `webapp/api/graph.py`), both additive.
- No new sensor hardware. The Sensors panel only surfaces telemetry that already exists: IMU (pitch/roll/gyro), SoC temp, CPU%, RAM%, hardware-presence booleans.
- This codebase has **no JS unit test framework** (confirmed: no `package.json`, no JS test runner anywhere in the repo). The established verification pattern for frontend work here is (a) the Python-side static-integrity test (`bridge/tests/webapp/test_static_integrity.py`) that checks referenced files exist, and (b) a manual smoke checklist run against `bridge/tools/webdev.py` (see `docs/WEB-DASHBOARD.md` §7). Every frontend task in this plan follows that same pattern — do not introduce a JS test framework as part of this work.
- Backend tests run from the repo root: `pytest bridge/tests/webapp/<file>.py -v` (repo's `pyproject.toml` sets `asyncio_mode = "auto"`, so `async def test_...` needs no decorator).
- Dev server for manual verification: `python bridge/tools/webdev.py` from the repo root, then open `http://localhost:8080`. It wires the real `aiohttp` app to fake drivers (`bridge/tests/webapp/fakes.py`) — camera shows a placeholder frame, audio plumbing works without real mic/speaker hardware, motion cards print into fakes instead of moving servos.
- Known pre-existing shape mismatch (not introduced or fixed by this plan): `bridge/tests/webapp/fakes.py`'s `FakeImu.read()` returns `{"pitch": ..., "roll": ..., "gyro_z": ...}` (a plain dict with a scalar `gyro_z`), while the real driver (`bridge/milo_bridge/drivers/imu.py`, `Mpu6050.read()`) returns an `ImuState` dataclass with a `gyro` field that's a 3-tuple. Task 4's Sensors panel code reads gyro defensively (`m.imu.gyro_z ?? m.imu.gyro?.[2] ?? null`) to tolerate either shape without crashing. Fixing the underlying mismatch is out of scope for this plan.

---

## Task 1: Backend — full-graph fetch for the Memory Graph panel

**Files:**
- Modify: `bridge/milo_bridge/graph/store.py:141-160` (add `all()`, factor out `_edges_among()`)
- Modify: `bridge/milo_bridge/webapp/api/graph.py:13-19` (`get_search` calls `.all()` on empty query)
- Test: `bridge/tests/webapp/test_graph_api.py`

**Interfaces:**
- Produces: `GraphStore.all(limit: int = 200) -> dict` returning `{"nodes": [...], "edges": [...]}` for the whole graph, most-recently-updated nodes first, capped at `limit`. Consumed by Task 5's `panels/graph.js` via `GET /api/graph/search?limit=200` (no `q`).
- Produces: `GraphStore._edges_among(ids: set[int]) -> list[Edge]` — private helper shared by `search_text` and `all`.

- [ ] **Step 1: Write the failing tests**

Add to `bridge/tests/webapp/test_graph_api.py` (after the existing `_seed` helper and its imports, which already provide `make_deps`, `_client`, `_seed`):

```python
async def test_all_returns_full_graph_capped():
    deps = make_deps()
    alice, bob, ball = _seed(deps.graph_store)
    result = deps.graph_store.all(limit=200)
    ids = {n["id"] for n in result["nodes"]}
    assert {alice.id, bob.id, ball.id} <= ids
    edge_pairs = {(e["src"], e["dst"]) for e in result["edges"]}
    assert (alice.id, ball.id) in edge_pairs
    assert (alice.id, bob.id) in edge_pairs


async def test_all_respects_limit():
    deps = make_deps()
    for i in range(5):
        deps.graph_store.upsert_node("fact", {"n": i})
    result = deps.graph_store.all(limit=3)
    assert len(result["nodes"]) == 3


async def test_search_with_empty_query_returns_full_graph_via_http():
    deps = make_deps()
    _seed(deps.graph_store)
    client = await _client(deps)
    try:
        resp = await client.get("/api/graph/search", params={"limit": "200"})
        data = await resp.json()
        assert len(data["nodes"]) == 3
    finally:
        await client.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from repo root `D:/Github/MILO-Robot`): `pytest bridge/tests/webapp/test_graph_api.py -v`
Expected: the three new tests FAIL — `test_all_returns_full_graph_capped` and `test_all_respects_limit` with `AttributeError: 'GraphStore' object has no attribute 'all'`; `test_search_with_empty_query_returns_full_graph_via_http` with an assertion failure (`len(data["nodes"]) == 0`, since today's `get_search` short-circuits an empty `q` to `{"nodes": [], "edges": []}`).

- [ ] **Step 3: Implement `_edges_among` and `all` in `GraphStore`**

In `bridge/milo_bridge/graph/store.py`, replace the `search_text` method (currently lines 141-160) with this — it factors the edge-lookup block out into a shared helper and adds `all()` right after it:

```python
    def _edges_among(self, ids: set[int]) -> list[Edge]:
        """Edges whose src and dst are both in `ids`."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        cur = self._db.execute(
            f"SELECT id, src, dst, type, props, created_at FROM edges "
            f"WHERE src IN ({marks}) AND dst IN ({marks})",
            (*ids, *ids),
        )
        return [Edge(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5]) for r in cur.fetchall()]

    def search_text(self, q: str, limit: int = 25) -> dict:
        """Free-text search over node type and props JSON; edges among matches."""
        pat = f"%{q}%"
        cur = self._db.execute(
            "SELECT id, type, props, created_at, updated_at FROM nodes "
            "WHERE type LIKE ? OR props LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (pat, pat, limit),
        )
        nodes = [Node(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in cur.fetchall()]
        edges = self._edges_among({n.id for n in nodes})
        return {"nodes": [n.to_dict() for n in nodes], "edges": [e.to_dict() for e in edges]}

    def all(self, limit: int = 200) -> dict:
        """The whole graph, most-recently-updated nodes first, capped at `limit`."""
        cur = self._db.execute(
            "SELECT id, type, props, created_at, updated_at FROM nodes "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        nodes = [Node(r[0], r[1], json.loads(r[2]), r[3], r[4]) for r in cur.fetchall()]
        edges = self._edges_among({n.id for n in nodes})
        return {"nodes": [n.to_dict() for n in nodes], "edges": [e.to_dict() for e in edges]}
```

- [ ] **Step 4: Update `get_search` to use `all()` on an empty query**

In `bridge/milo_bridge/webapp/api/graph.py`, replace the `get_search` function (currently lines 13-19):

```python
async def get_search(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    q = request.query.get("q", "").strip()
    limit = int(request.query.get("limit", "25"))
    if not q:
        return web.json_response(deps.graph_store.all(limit))
    return web.json_response(deps.graph_store.search_text(q, limit))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest bridge/tests/webapp/test_graph_api.py -v`
Expected: all tests PASS, including the three new ones and the pre-existing `test_search_matches_props_and_includes_edges`, `test_graph_http_passthrough_and_search`, `test_poses_and_faces_endpoints`.

- [ ] **Step 6: Run the full webapp test suite to confirm no regressions**

Run: `pytest bridge/tests/webapp -v`
Expected: all tests PASS (this touches shared code in `GraphStore` and the graph API route, so confirm nothing else broke).

- [ ] **Step 7: Commit**

```bash
git add bridge/milo_bridge/graph/store.py bridge/milo_bridge/webapp/api/graph.py bridge/tests/webapp/test_graph_api.py
git commit -m "feat(graph): add GraphStore.all() and serve it on empty search query"
```

---

## Task 2: Console shell — fixed cockpit layout replacing the drag/resize grid

**Files:**
- Create: `bridge/milo_bridge/webapp/static/css/console.css`
- Create: `bridge/milo_bridge/webapp/static/js/statusbar.js`
- Create: `bridge/milo_bridge/webapp/static/js/layout.js`
- Create: `bridge/milo_bridge/webapp/static/js/panels/camera.js` (moved from `js/cards/camera.js`, `w`/`h` dropped)
- Create: `bridge/milo_bridge/webapp/static/js/panels/move.js` (moved from `js/cards/move.js`, `w`/`h` dropped)
- Create: `bridge/milo_bridge/webapp/static/js/panels/poses.js` (moved from `js/cards/poses.js`, `w`/`h` dropped)
- Create: `bridge/milo_bridge/webapp/static/js/panels/servos.js` (moved from `js/cards/servos.js`, `w`/`h` dropped)
- Create: `bridge/milo_bridge/webapp/static/js/panels/log.js` (moved from `js/cards/log.js`, `w`/`h` dropped)
- Create: `bridge/milo_bridge/webapp/static/js/panels/sensors.js` (moved from `js/cards/sensors.js` unchanged besides `w`/`h`; rewritten in Task 4)
- Create: `bridge/milo_bridge/webapp/static/js/panels/graph.js` (moved from `js/cards/graph.js` unchanged besides `w`/`h`; rewritten in Task 5)
- Modify: `bridge/milo_bridge/webapp/static/index.html` (full rewrite)
- Modify: `bridge/milo_bridge/webapp/static/css/theme.css` (full rewrite: `#topbar` → `#statusbar`, stat readout styles, drop dead `.menu` rules)
- Modify: `bridge/milo_bridge/webapp/static/js/main.js` (full rewrite: bootstrap `statusbar.js` + `layout.js` instead of inline header wiring + `initGrid`)
- Modify: `bridge/milo_bridge/webapp/static/js/registry.js` (full rewrite: zone-grouped registry)
- Modify: `bridge/tests/webapp/test_static_integrity.py` (update hardcoded shell file list)
- Delete: `bridge/milo_bridge/webapp/static/js/grid.js`
- Delete: `bridge/milo_bridge/webapp/static/css/grid.css`
- Delete: `bridge/milo_bridge/webapp/static/js/cards/status.js` (folded into `statusbar.js`)
- Delete: `bridge/milo_bridge/webapp/static/js/cards/ears.js` (replaced by Task 3's `panels/comm.js`)
- Delete: `bridge/milo_bridge/webapp/static/js/cards/voice.js` (replaced by Task 3's `panels/comm.js`)
- Delete: `bridge/milo_bridge/webapp/static/js/cards/camera.js`, `cards/move.js`, `cards/poses.js`, `cards/servos.js`, `cards/log.js`, `cards/sensors.js`, `cards/graph.js` (moved to `panels/`, not left duplicated)

**Interfaces:**
- Consumes: `createBus()` from `js/bus.js` (unchanged — exposes `bus.clientId`, `bus.controlled`, `bus.connected`, `bus.send(obj)`, `bus.sendBytes(u8)`, `bus.on(topic, fn) -> unsubscribe`, `bus.onBinary(fn) -> unsubscribe`).
- Produces: `initStatusBar(el, bus, { onToolsToggle }) -> void`, mounted onto `<header id="statusbar">`.
- Produces: `initLayout(registry, bus) -> { toggleTools(): void }`, mounted using the fixed DOM slots `#cockpit-center`, `#cockpit-side`, `#memory-graph`, `#tools-drawer`, `#drawer-backdrop`.
- Produces: `registry` object shape `{ cockpitCenter: Panel[], cockpitSide: Panel[], graph: Panel[], tools: Panel[] }` where `Panel = { id: string, title: string, needsControl?: boolean, mount(el, {bus}): (() => void) | void }`. Task 3 adds a `comm` panel into `cockpitCenter`; Task 4 rewrites the `sensors` panel's `mount`; Task 5 rewrites the `graph` panel's `mount`. None of those tasks change this shape.

- [ ] **Step 1: Rewrite `theme.css`**

Replace the full contents of `bridge/milo_bridge/webapp/static/css/theme.css`:

```css
:root {
  --bg: #fafafa; --surface: #ffffff; --ink: #111111; --muted: #777777;
  --line: #dddddd; --ok: #1a7f37; --danger: #c0392b;
  --font: system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
:root[data-theme="dark"] {
  --bg: #0d0d0d; --surface: #161616; --ink: #f2f2f2; --muted: #8a8a8a;
  --line: #2c2c2c; --ok: #2ecc71; --danger: #e74c3c;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--bg); color: var(--ink); font: 14px/1.45 var(--font); }
#statusbar {
  position: sticky; top: 0; z-index: 50; display: flex; align-items: center;
  flex-wrap: wrap; gap: 10px 16px; padding: 8px 14px; background: var(--surface);
  border-bottom: 1px solid var(--line);
}
.brand { font-weight: 700; letter-spacing: 0.18em; }
.spacer { flex: 1; }
.muted { color: var(--muted); }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--danger); display: inline-block; }
.dot.live { background: var(--ok); }
.stat-group { display: flex; gap: 14px; align-items: center; }
.stat { display: flex; flex-direction: column; line-height: 1.15; }
.stat .stat-label { font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.stat .stat-value { font-size: 12px; font-weight: 600; }
.stat-toggle-btn { display: none; }
.btn {
  font: inherit; color: var(--ink); background: var(--surface);
  border: 1px solid var(--ink); border-radius: 4px; padding: 5px 12px; cursor: pointer;
}
.btn:hover { background: var(--ink); color: var(--surface); }
.btn.danger { border-color: var(--danger); color: var(--danger); }
.btn.danger:hover { background: var(--danger); color: #fff; }
.btn.ghost { border-color: var(--line); }
.btn.active { background: var(--ink); color: var(--surface); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
input, select, textarea {
  font: inherit; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 4px; padding: 4px 8px;
}
input[type="range"] { padding: 0; }
canvas { display: block; }
```

(This drops the old `.menu`/`.menu.hidden`/`.menu button` rules — dead CSS now that the "+ Card" add-menu is gone — and renames `#topbar` to `#statusbar`, adding the `.stat-group`/`.stat`/`.stat-toggle-btn` chrome used by the new status bar.)

- [ ] **Step 2: Create `console.css`**

Create `bridge/milo_bridge/webapp/static/css/console.css`:

```css
/* Fixed cockpit console layout: zones, panels, communication VU meter,
   sensor tiles, memory graph, tools drawer, and the mobile breakpoint. */

#cockpit {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
  padding: 16px;
  align-items: start;
}
#cockpit-center { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
#cockpit-side { display: flex; flex-direction: column; gap: 16px; }

.panel {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px; position: relative;
}
.panel-title {
  font-weight: 600; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 10px;
}
.panel.locked .panel-body { opacity: 0.45; pointer-events: none; }
.panel.locked::after {
  content: "take control to use"; display: block; margin-top: 8px;
  font-size: 11px; color: var(--muted);
}

/* camera */
#cam { width: 100%; aspect-ratio: 4 / 3; object-fit: contain; background: #000; border-radius: 6px; }

/* communication panel */
.comm-row { display: flex; gap: 14px; align-items: stretch; }
.comm-controls { flex: 1; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.comm-say { display: flex; gap: 6px; }
.comm-say input { flex: 1; }
.vu-vertical {
  width: 22px; height: 100px; border: 1px solid var(--line); border-radius: 4px;
  background: var(--bg); position: relative; overflow: hidden; align-self: center; flex-shrink: 0;
}
.vu-fill {
  position: absolute; left: 0; right: 0; bottom: 0; height: calc(var(--level, 0) * 100%);
  background: var(--ok); transition: height 0.08s linear;
}
.vu-vertical.hot .vu-fill { background: var(--danger); }
.locked-control { opacity: 0.45; pointer-events: none; }

/* sensors panel */
.sensor-tiles { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.sensor-tile { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
.sensor-tile .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.sensor-tile .value { font-size: 18px; font-weight: 600; margin-top: 2px; }
.sensor-details { margin-top: 12px; border-top: 1px solid var(--line); padding-top: 10px; }
.sensor-details.hidden { display: none; }
.sensor-details canvas { width: 100%; height: 50px; margin-bottom: 10px; }
.sensor-details .spark-label { font-size: 11px; color: var(--muted); margin-bottom: 2px; }

/* memory graph */
#memory-graph { margin: 0 16px 16px; }
.graph-search { display: flex; gap: 6px; margin-bottom: 8px; }
.graph-search input { flex: 1; }
#graph-canvas {
  width: 100%; height: 480px; background: var(--bg); border-radius: 8px; border: 1px solid var(--line);
}
#graph-detail { min-height: 34px; max-height: 60px; overflow: auto; font-size: 12px; margin-top: 6px; }

/* tools drawer */
#drawer-backdrop {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.35); z-index: 70;
  opacity: 0; pointer-events: none; transition: opacity 0.2s ease;
}
#drawer-backdrop.open { opacity: 1; pointer-events: auto; }
#tools-drawer {
  position: fixed; top: 0; right: 0; height: 100%; width: 360px; max-width: 90vw;
  background: var(--surface); border-left: 1px solid var(--line); z-index: 80;
  transform: translateX(100%); transition: transform 0.2s ease; overflow-y: auto; padding: 16px;
}
#tools-drawer.open { transform: translateX(0); }
#tools-drawer .panel + .panel { margin-top: 18px; }

/* responsive */
@media (max-width: 900px) {
  #cockpit { grid-template-columns: 1fr; padding: 10px; gap: 10px; }
  #memory-graph { margin: 0 10px 10px; }
  #graph-canvas { height: 320px; }
  #tools-drawer { width: 100%; max-width: 100%; }
  .stat-group.secondary { display: none; }
  .stat-group.secondary.expanded { display: flex; flex-basis: 100%; order: 10; }
  .stat-toggle-btn { display: inline-flex; }
  .btn { min-height: 44px; padding: 10px 16px; }
  input[type="range"] { min-height: 32px; }
  input:not([type="range"]), select, textarea { min-height: 40px; padding: 8px 10px; }
}
```

- [ ] **Step 3: Delete `grid.css`**

```bash
rm bridge/milo_bridge/webapp/static/css/grid.css
```

- [ ] **Step 4: Create the `panels/` directory with moved panel files**

Create `bridge/milo_bridge/webapp/static/js/panels/camera.js`:

```js
export default {
  id: "camera", title: "Camera",
  mount(el) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <img id="cam" src="/stream/camera" alt="camera offline"
             onerror="this.dataset.err=1">
        <div><button class="btn" id="snap">Snapshot</button></div>
      </div>`;
    const img = el.querySelector("#cam");
    el.querySelector("#snap").onclick = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
      c.getContext("2d").drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.href = c.toDataURL("image/jpeg");
      a.download = `milo-${Date.now()}.jpg`;
      a.click();
    };
  },
};
```

(Note: the inline `width/object-fit/background/border-radius` styling on `#cam` moved into `console.css`'s `#cam` rule from Step 2, since it's now a fixed panel rather than a resizable card.)

Create `bridge/milo_bridge/webapp/static/js/panels/move.js`:

```js
const SEND_MS = 100;

export default {
  id: "move", title: "Move", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;gap:14px;height:100%">
        <div id="pad" style="flex:1;max-width:220px;aspect-ratio:1;border:1px solid var(--line);
             border-radius:8px;position:relative;touch-action:none">
          <div id="knob" style="position:absolute;width:26px;height:26px;border-radius:50%;
               background:var(--ink);left:calc(50% - 13px);top:calc(50% - 13px)"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;flex:1">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, Q/E to turn</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const pad = el.querySelector("#pad"), knob = el.querySelector("#knob");
    const speed = el.querySelector("#speed");
    let vec = { vx: 0, vy: 0, yaw: 0 }, timer = null;

    function sending(active) {
      if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
      if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
    }
    const scaled = () => {
      const k = speed.value / 100;
      return { vx: vec.vx * k, vy: vec.vy * k, yaw: vec.yaw * 2 * k };
    };

    pad.addEventListener("pointerdown", (e) => {
      pad.setPointerCapture(e.pointerId);
      const rect = pad.getBoundingClientRect();
      const move = (ev) => {
        const x = Math.max(-1, Math.min(1, ((ev.clientX - rect.left) / rect.width) * 2 - 1));
        const y = Math.max(-1, Math.min(1, ((ev.clientY - rect.top) / rect.height) * 2 - 1));
        knob.style.left = `calc(${(x + 1) * 50}% - 13px)`;
        knob.style.top = `calc(${(y + 1) * 50}% - 13px)`;
        vec = { vx: -y, vy: x, yaw: 0 };
        sending(true);
      };
      const up = () => {
        pad.removeEventListener("pointermove", move);
        knob.style.left = "calc(50% - 13px)"; knob.style.top = "calc(50% - 13px)";
        vec = { vx: 0, vy: 0, yaw: 0 }; sending(false);
      };
      pad.addEventListener("pointermove", move);
      pad.addEventListener("pointerup", up, { once: true });
      move(e);
    });

    const keys = { w: [1,0,0], s: [-1,0,0], a: [0,-1,0], d: [0,1,0], q: [0,0,-1], e: [0,0,1],
      ArrowUp: [1,0,0], ArrowDown: [-1,0,0], ArrowLeft: [0,0,-1], ArrowRight: [0,0,1] };
    const down = new Set();
    const sync = () => {
      let vx = 0, vy = 0, yaw = 0;
      down.forEach((k) => { const [a,b,c] = keys[k]; vx += a; vy += b; yaw += c; });
      vec = { vx: Math.sign(vx), vy: Math.sign(vy), yaw: Math.sign(yaw) };
      sending(down.size > 0);
    };
    const kd = (e) => { if (keys[e.key] && !e.repeat && e.target.tagName !== "INPUT") { down.add(e.key); sync(); } };
    const ku = (e) => { if (keys[e.key]) { down.delete(e.key); sync(); } };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => { sending(false); window.removeEventListener("keydown", kd); window.removeEventListener("keyup", ku); };
  },
};
```

Create `bridge/milo_bridge/webapp/static/js/panels/poses.js`:

```js
export default {
  id: "poses", title: "Poses & Emotes", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `<div class="muted">Poses</div><div id="pose-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px"></div>
      <div class="muted">Faces</div><div id="face-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"></div>`;
    const fill = (sel, names, type) => {
      const box = el.querySelector(sel);
      names.forEach((name) => {
        const b = document.createElement("button");
        b.className = "btn"; b.textContent = name;
        b.onclick = () => bus.send({ t: type, name });
        box.appendChild(b);
      });
    };
    fetch("/api/poses").then((r) => r.json()).then((d) => fill("#pose-btns", d.poses, "pose"));
    fetch("/api/faces").then((r) => r.json()).then((d) => fill("#face-btns", d.faces, "face"));
  },
};
```

Create `bridge/milo_bridge/webapp/static/js/panels/servos.js`:

```js
const SERVOS = ["R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4"];

export default {
  id: "servos", title: "Servo Test", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = SERVOS.map((s) => `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="width:26px;font-weight:600">${s}</span>
        <input type="range" min="0" max="180" value="90" data-servo="${s}" style="flex:1">
        <span data-val="${s}" style="width:34px;text-align:right">90°</span>
      </div>`).join("") +
      `<button class="btn" id="center" style="margin-top:8px">Center All (90°)</button>`;
    el.querySelectorAll("input[type=range]").forEach((sl) => {
      sl.oninput = () => {
        el.querySelector(`[data-val="${sl.dataset.servo}"]`).textContent = `${sl.value}°`;
        bus.send({ t: "servo", servo: sl.dataset.servo, deg: Number(sl.value) });
      };
    });
    el.querySelector("#center").onclick = () => {
      const angles = {};
      SERVOS.forEach((s) => {
        const sl = el.querySelector(`[data-servo="${s}"]`);
        sl.value = 90;
        el.querySelector(`[data-val="${s}"]`).textContent = "90°";
        angles[s] = 90;
      });
      bus.send({ t: "servo_batch", angles });
    };
  },
};
```

Create `bridge/milo_bridge/webapp/static/js/panels/log.js`:

```js
export default {
  id: "log", title: "Bridge Log",
  mount(el, { bus }) {
    el.innerHTML = `<pre id="loglines" style="margin:0;font-size:11px;white-space:pre-wrap"></pre>`;
    const pre = el.querySelector("#loglines");
    const push = (line) => {
      pre.textContent += line + "\n";
      const lines = pre.textContent.split("\n");
      if (lines.length > 300) pre.textContent = lines.slice(-300).join("\n");
      el.scrollTop = el.scrollHeight;
    };
    fetch("/api/logs?n=100").then((r) => r.json())
      .then((d) => d.lines.forEach(push)).catch(() => {});
    return bus.on("log", (m) => push(m.line));
  },
};
```

Create `bridge/milo_bridge/webapp/static/js/panels/sensors.js` (identical logic to today's card; Task 4 rewrites this file):

```js
export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <canvas id="imu-spark" width="360" height="70" style="width:100%"></canvas>
      <div id="imu-now" class="muted" style="margin:4px 0 10px">imu: —</div>
      <div id="hw"></div>`;
    const hist = [];
    const cv = el.querySelector("#imu-spark"), g = cv.getContext("2d");
    const offT = bus.on("telemetry", (m) => {
      const now = el.querySelector("#imu-now");
      if (!m.imu) { now.textContent = "imu: n/a"; return; }
      now.textContent = `pitch ${m.imu.pitch?.toFixed(1)}°  roll ${m.imu.roll?.toFixed(1)}°`;
      hist.push([m.imu.pitch || 0, m.imu.roll || 0]);
      if (hist.length > 120) hist.shift();
      g.clearRect(0, 0, cv.width, cv.height);
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink");
      const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted");
      [0, 1].forEach((k) => {
        g.strokeStyle = k === 0 ? ink : muted;
        g.beginPath();
        hist.forEach(([p, r], i) => {
          const v = k === 0 ? p : r;
          const y = 35 - (v / 90) * 33;
          i ? g.lineTo(i * 3, y) : g.moveTo(0, y);
        });
        g.stroke();
      });
    });
    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `<span style="margin-right:12px">${ok ? "●" : "○"} ${k}</span>`).join("");
    });
    return offT;
  },
};
```

Create `bridge/milo_bridge/webapp/static/js/panels/graph.js` (identical logic to today's card; Task 5 rewrites this file):

```js
export default {
  id: "graph", title: "Memory Graph",
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
```

- [ ] **Step 5: Delete the old `cards/` directory**

```bash
rm bridge/milo_bridge/webapp/static/js/cards/camera.js
rm bridge/milo_bridge/webapp/static/js/cards/move.js
rm bridge/milo_bridge/webapp/static/js/cards/poses.js
rm bridge/milo_bridge/webapp/static/js/cards/servos.js
rm bridge/milo_bridge/webapp/static/js/cards/log.js
rm bridge/milo_bridge/webapp/static/js/cards/sensors.js
rm bridge/milo_bridge/webapp/static/js/cards/graph.js
rm bridge/milo_bridge/webapp/static/js/cards/status.js
rm bridge/milo_bridge/webapp/static/js/cards/ears.js
rm bridge/milo_bridge/webapp/static/js/cards/voice.js
rmdir bridge/milo_bridge/webapp/static/js/cards
rm bridge/milo_bridge/webapp/static/js/grid.js
```

- [ ] **Step 6: Create `statusbar.js`**

Create `bridge/milo_bridge/webapp/static/js/statusbar.js`:

```js
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
```

- [ ] **Step 7: Create `layout.js`**

Create `bridge/milo_bridge/webapp/static/js/layout.js`:

```js
// Fixed cockpit layout: mounts zone-grouped panels into fixed slots and
// manages the Tools drawer. Replaces the old drag/resize grid.js.
export function initLayout(registry, bus) {
  const center = document.getElementById("cockpit-center");
  const side = document.getElementById("cockpit-side");
  const graphZone = document.getElementById("memory-graph");
  const drawer = document.getElementById("tools-drawer");
  const backdrop = document.getElementById("drawer-backdrop");

  function mountInto(container, panels) {
    for (const panel of panels) {
      const section = document.createElement("section");
      section.className = "panel";
      section.dataset.id = panel.id;
      section.innerHTML = `<h2 class="panel-title">${panel.title}</h2><div class="panel-body"></div>`;
      container.appendChild(section);
      panel.mount(section.querySelector(".panel-body"), { bus });
      if (panel.needsControl) {
        const applyLock = () => section.classList.toggle("locked", !bus.controlled);
        applyLock();
        bus.on("control", applyLock);
        bus.on("_close", applyLock);
      }
    }
  }

  mountInto(center, registry.cockpitCenter);
  mountInto(side, registry.cockpitSide);
  mountInto(graphZone, registry.graph);
  mountInto(drawer, registry.tools);

  let open = false;
  function setOpen(next) {
    open = next;
    drawer.classList.toggle("open", open);
    backdrop.classList.toggle("open", open);
  }
  backdrop.onclick = () => setOpen(false);

  return { toggleTools: () => setOpen(!open) };
}
```

- [ ] **Step 8: Rewrite `registry.js`**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/registry.js`:

```js
// Adding a panel = create js/panels/<name>.js + add it to the right zone below.
import camera from "./panels/camera.js";
import move from "./panels/move.js";
import sensors from "./panels/sensors.js";
import graph from "./panels/graph.js";
import poses from "./panels/poses.js";
import servos from "./panels/servos.js";
import log from "./panels/log.js";

export const registry = {
  cockpitCenter: [camera, move],
  cockpitSide: [sensors],
  graph: [graph],
  tools: [poses, servos, log],
};
```

- [ ] **Step 9: Rewrite `main.js`**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/main.js`:

```js
import { createBus } from "./bus.js";
import { initStatusBar } from "./statusbar.js";
import { initLayout } from "./layout.js";
import { registry } from "./registry.js";

// theme (set before first paint to avoid a flash of the wrong theme)
const saved = localStorage.getItem("milo.theme");
if (saved) document.documentElement.dataset.theme = saved;
else if (matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.dataset.theme = "dark";

const bus = createBus();
const layout = initLayout(registry, bus);
initStatusBar(document.getElementById("statusbar"), bus, {
  onToolsToggle: () => layout.toggleTools(),
});
```

- [ ] **Step 10: Rewrite `index.html`**

Replace the full contents of `bridge/milo_bridge/webapp/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MILO</title>
<link rel="stylesheet" href="/static/css/theme.css">
<link rel="stylesheet" href="/static/css/console.css">
</head>
<body>
<header id="statusbar"></header>
<main id="cockpit">
  <section id="cockpit-center"></section>
  <aside id="cockpit-side"></aside>
</main>
<section id="memory-graph"></section>
<div id="drawer-backdrop"></div>
<aside id="tools-drawer"></aside>
<script type="module" src="/static/js/main.js"></script>
</body>
</html>
```

- [ ] **Step 11: Update `test_static_integrity.py`'s hardcoded shell file list**

In `bridge/tests/webapp/test_static_integrity.py`, replace `test_shell_files_exist` (currently the last function in the file):

```python
def test_shell_files_exist():
    for f in ["index.html", "css/theme.css", "css/console.css", "js/main.js",
              "js/registry.js", "js/bus.js", "js/layout.js", "js/statusbar.js",
              "js/panels/log.js"]:
        assert (STATIC / f).exists(), f"missing {f}"
```

- [ ] **Step 12: Run the static integrity tests**

Run: `pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all four tests PASS — `test_index_references_exist` and `test_registry_imports_exist` now scan the new `index.html`/`registry.js`, `test_login_page_references_exist` is unaffected (login page untouched), `test_shell_files_exist` checks the new file list from Step 11.

- [ ] **Step 13: Run the full webapp test suite**

Run: `pytest bridge/tests/webapp -v`
Expected: all tests PASS. `test_status.py::test_index_served` in particular still passes since it only checks that `"MILO"` appears in the served `index.html`, which it still does (the `<title>` and `.brand` text).

- [ ] **Step 14: Manual verification with the dev server**

Run: `python bridge/tools/webdev.py` (from repo root), then open `http://localhost:8080` in a browser.

Verify, logging in with `tester` / `test-pw-12345` (the dev fakes' seeded credentials):
- The page shows a sticky status bar at the top (brand, connection dot goes green, owner label, Take Control/STOP/Tools/Logout/theme buttons — no more +Card or ⟲ reset buttons).
- Below it, a two-column area: left/wide column has the Camera panel (showing the placeholder stream) and the Move panel (joystick + speed slider + STOP) stacked vertically; right/narrow column has the Sensors panel.
- Below that, a full-width Memory Graph section with its search bar and canvas.
- Clicking **Tools** in the status bar slides in a drawer from the right containing Poses & Emotes, Servo Test, and Bridge Log; clicking the dark backdrop closes it.
- Clicking **Take Control** unlocks the Move panel (border/dim state clears) and the Poses/Servo Test panels in the drawer.
- Toggling the theme button switches light/dark and both look correct (no unstyled/broken regions).
- No JS errors in the browser console.

- [ ] **Step 15: Commit**

```bash
git add bridge/milo_bridge/webapp/static
git add bridge/tests/webapp/test_static_integrity.py
git commit -m "feat(webapp): replace drag/resize card grid with a fixed cockpit console"
```

---

## Task 3: Communication panel — merge Ears + Voice into one gated panel

**Files:**
- Create: `bridge/milo_bridge/webapp/static/js/panels/comm.js`
- Modify: `bridge/milo_bridge/webapp/static/js/registry.js:1-14` (import `comm`, add to `cockpitCenter`)

**Interfaces:**
- Consumes: `bus.onBinary(fn)`, `bus.send(obj)`, `bus.sendBytes(u8)`, `bus.controlled`, `bus.on("control"|"_close", fn)`, `bus.clientId` from `bus.js` (all pre-existing, unchanged).
- Produces: a `comm` panel object (`{ id: "comm", title: "Communication", mount(el, {bus}) }`) added to `registry.cockpitCenter`, no `needsControl` flag at the panel level (it self-gates its push-to-talk/Say controls internally instead — see Step 1).

- [ ] **Step 1: Create `panels/comm.js`**

Create `bridge/milo_bridge/webapp/static/js/panels/comm.js`:

```js
// Communication panel: merges the old Ears (listen) and Voice (speak) cards.
// Listening (headphones + VU meter) needs no control; push-to-talk and Say
// are individually locked until this tab holds control.
const SAMPLE_RATE = 16000;   // must match the robot's capture/playback rate
const CHANNELS = 2;
const HOT_THRESHOLD = 0.5;   // level (0-1) above which the VU bar turns red

export default {
  id: "comm", title: "Communication",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="comm-row">
        <div class="comm-controls">
          <button class="btn" id="headphones">🎧 Listen</button>
          <button class="btn" id="ptt">🎙 Hold to Talk</button>
          <div class="comm-say">
            <input id="say" placeholder="Type something to say…">
            <button class="btn" id="speak">Say</button>
          </div>
          <div class="muted" id="comm-note"></div>
        </div>
        <div class="vu-vertical" id="vu"><div class="vu-fill"></div></div>
      </div>`;

    // -- listening (headphones + VU meter): no control required -------------
    const headphones = el.querySelector("#headphones");
    const vu = el.querySelector("#vu");
    let playCtx = null, playHead = 0, listening = false;

    function setLevel(level) {
      vu.style.setProperty("--level", Math.min(1, level).toFixed(3));
      vu.classList.toggle("hot", level >= HOT_THRESHOLD);
    }

    const offBin = bus.onBinary((u8) => {
      if (!listening || u8[0] !== 0x01) return;
      const pcm = new Int16Array(u8.buffer, u8.byteOffset + 1, (u8.byteLength - 1) >> 1);
      const frames = pcm.length / CHANNELS;
      const buf = playCtx.createBuffer(CHANNELS, frames, SAMPLE_RATE);
      let sumSq = 0;
      for (let ch = 0; ch < CHANNELS; ch++) {
        const out = buf.getChannelData(ch);
        for (let i = 0; i < frames; i++) {
          const v = pcm[i * CHANNELS + ch] / 32768;
          out[i] = v; sumSq += v * v;
        }
      }
      setLevel(Math.sqrt(sumSq / (frames * CHANNELS)) * 4);
      const src = playCtx.createBufferSource();
      src.buffer = buf; src.connect(playCtx.destination);
      playHead = Math.max(playHead, playCtx.currentTime + 0.05);
      src.start(playHead);
      playHead += buf.duration;
    });

    headphones.onclick = () => {
      listening = !listening;
      headphones.textContent = listening ? "🎧 Mute" : "🎧 Listen";
      headphones.classList.toggle("active", listening);
      if (listening && !playCtx) playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      if (listening) playHead = 0; else setLevel(0);
      bus.send({ t: "audio", on: listening });
    };

    // -- push-to-talk + Say: need control -------------------------------------
    const note = el.querySelector("#comm-note");
    const ptt = el.querySelector("#ptt");
    const say = el.querySelector("#say");
    const speak = el.querySelector("#speak");
    let recCtx = null, stream = null, node = null;

    function applyGate() {
      const locked = !bus.controlled;
      [ptt, say, speak].forEach((elm) => elm.classList.toggle("locked-control", locked));
      ptt.disabled = say.disabled = speak.disabled = locked;
    }
    applyGate();
    const offControl = bus.on("control", applyGate);
    const offClose = bus.on("_close", applyGate);

    async function startTalk() {
      if (!bus.controlled) return;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch { note.textContent = "microphone permission denied"; return; }
      recCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      const src = recCtx.createMediaStreamSource(stream);
      node = recCtx.createScriptProcessor(2048, 1, 1);
      node.onaudioprocess = (ev) => {
        const f32 = ev.inputBuffer.getChannelData(0);
        const out = new Uint8Array(1 + f32.length * 2);
        out[0] = 0x02;
        const view = new DataView(out.buffer);
        for (let i = 0; i < f32.length; i++)
          view.setInt16(1 + i * 2, Math.max(-1, Math.min(1, f32[i])) * 32767, true);
        bus.sendBytes(out);
      };
      src.connect(node); node.connect(recCtx.destination);
    }
    function stopTalk() {
      if (node) node.disconnect();
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (recCtx) recCtx.close();
      recCtx = stream = node = null;
    }
    ptt.onpointerdown = startTalk;
    ptt.onpointerup = ptt.onpointerleave = stopTalk;

    speak.onclick = async () => {
      if (!bus.controlled) return;
      const text = say.value.trim();
      if (!text) return;
      const r = await fetch("/api/speak", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, client: bus.clientId }),
      }).then((r) => r.json()).catch(() => ({ error: "network" }));
      note.textContent = r.error ? `✗ ${r.error}` : "✓ spoke";
    };

    return () => {
      offBin(); offControl(); offClose();
      if (playCtx) playCtx.close();
      bus.send({ t: "audio", on: false });
      stopTalk();
    };
  },
};
```

- [ ] **Step 2: Wire `comm` into the registry**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/registry.js`:

```js
// Adding a panel = create js/panels/<name>.js + add it to the right zone below.
import camera from "./panels/camera.js";
import move from "./panels/move.js";
import comm from "./panels/comm.js";
import sensors from "./panels/sensors.js";
import graph from "./panels/graph.js";
import poses from "./panels/poses.js";
import servos from "./panels/servos.js";
import log from "./panels/log.js";

export const registry = {
  cockpitCenter: [camera, move, comm],
  cockpitSide: [sensors],
  graph: [graph],
  tools: [poses, servos, log],
};
```

- [ ] **Step 3: Run the static integrity and webapp test suites**

Run: `pytest bridge/tests/webapp -v`
Expected: all tests PASS (`test_registry_imports_exist` now also validates `panels/comm.js` exists, since it scans every `from "./..."` import in `registry.js`).

- [ ] **Step 4: Manual verification with the dev server**

Run: `python bridge/tools/webdev.py`, open `http://localhost:8080`, log in.

Verify:
- A single "Communication" panel now appears in the center column below Move (Ears/Voice cards are gone).
- Without holding control: clicking **🎧 Listen** turns it into "🎧 Mute" and the vertical VU bar reacts (the dev fakes stream silent/near-silent PCM, so the bar may sit near the bottom — confirm it isn't frozen/erroring, and that its CSS var updates by inspecting the element). The push-to-talk button and the Say input/button are visibly dimmed and unclickable (cursor shows not-allowed via `pointer-events: none`).
- Click **Take Control** — push-to-talk and Say controls un-dim and become clickable; typing text and clicking **Say** shows either "✓ spoke" or a `tts-unavailable` error inline (both are acceptable off-Pi, since `espeak-ng` may not be installed on the dev machine — the point is the request round-trips and the note updates).
- Holding the push-to-talk button (mouse down) prompts for microphone permission in the browser; releasing it stops capture without errors.
- Release control — push-to-talk/Say lock again; Listen/VU meter remain usable throughout.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/comm.js bridge/milo_bridge/webapp/static/js/registry.js
git commit -m "feat(webapp): merge Ears and Voice into one Communication panel"
```

---

## Task 4: Sensors panel — live tiles + details history view

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/sensors.js` (full rewrite)

**Interfaces:**
- Consumes: `bus.on("telemetry", fn)` payload shape `{ imu: {pitch, roll, gyro_z?} | null, temp_c, cpu_percent, mem_percent, ... }` (from `bridge/milo_bridge/webapp/telemetry.py`, unchanged), and `GET /api/status` → `{ hardware: {camera, audio, imu, display} }` (unchanged).
- No change to this panel's registration — it stays the sole entry in `registry.cockpitSide`.

- [ ] **Step 1: Rewrite `panels/sensors.js`**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/panels/sensors.js`:

```js
// Sensors panel: live tiles for everything the robot actually reports
// (IMU attitude/gyro, SoC temp, CPU%, RAM%, hardware presence), plus a
// Details toggle with rolling history sparklines.
const HISTORY_LEN = 120;

export default {
  id: "sensors", title: "Sensors",
  mount(el, { bus }) {
    el.innerHTML = `
      <div class="sensor-tiles">
        <div class="sensor-tile"><div class="label">Pitch / Roll</div><div class="value" id="tile-attitude">—</div></div>
        <div class="sensor-tile"><div class="label">Gyro</div><div class="value" id="tile-gyro">—</div></div>
        <div class="sensor-tile"><div class="label">SoC Temp</div><div class="value" id="tile-temp">—</div></div>
        <div class="sensor-tile"><div class="label">CPU</div><div class="value" id="tile-cpu">—</div></div>
        <div class="sensor-tile"><div class="label">RAM</div><div class="value" id="tile-ram">—</div></div>
        <div class="sensor-tile"><div class="label">Hardware</div><div class="value" id="tile-hw">—</div></div>
      </div>
      <button class="btn ghost" id="sensor-details-btn" style="margin-top:10px">Details ▾</button>
      <div class="sensor-details hidden" id="sensor-details">
        <div class="spark-label">Attitude — pitch / roll (°)</div>
        <canvas id="spark-attitude" width="360" height="50"></canvas>
        <div class="spark-label">System — CPU % / RAM % / Temp °C</div>
        <canvas id="spark-system" width="360" height="50"></canvas>
      </div>`;

    const attitudeHist = [], systemHist = [];
    const cvA = el.querySelector("#spark-attitude"), gA = cvA.getContext("2d");
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

    const offT = bus.on("telemetry", (m) => {
      const pitch = m.imu?.pitch, roll = m.imu?.roll;
      const gyroZ = m.imu ? (m.imu.gyro_z ?? m.imu.gyro?.[2] ?? null) : null;
      el.querySelector("#tile-attitude").textContent =
        m.imu ? `${pitch.toFixed(1)}° / ${roll.toFixed(1)}°` : "n/a";
      el.querySelector("#tile-gyro").textContent = gyroZ == null ? "n/a" : `${gyroZ.toFixed(1)}°/s`;
      el.querySelector("#tile-temp").textContent = m.temp_c == null ? "n/a" : `${m.temp_c.toFixed(1)}°C`;
      el.querySelector("#tile-cpu").textContent = m.cpu_percent == null ? "n/a" : `${m.cpu_percent}%`;
      el.querySelector("#tile-ram").textContent = m.mem_percent == null ? "n/a" : `${m.mem_percent}%`;

      if (m.imu) {
        attitudeHist.push([pitch || 0, roll || 0]);
        if (attitudeHist.length > HISTORY_LEN) attitudeHist.shift();
        drawTraces(gA, cvA, attitudeHist, [-90, 90]);
      }
      systemHist.push([m.cpu_percent || 0, m.mem_percent || 0, m.temp_c || 0]);
      if (systemHist.length > HISTORY_LEN) systemHist.shift();
      drawTraces(gS, cvS, systemHist, [0, 100]);
    });

    fetch("/api/status").then((r) => r.json()).then((d) => {
      el.querySelector("#tile-hw").innerHTML = Object.entries(d.hardware)
        .map(([k, ok]) => `<span style="margin-right:8px">${ok ? "●" : "○"} ${k}</span>`).join("");
    });

    const details = el.querySelector("#sensor-details");
    const detailsBtn = el.querySelector("#sensor-details-btn");
    detailsBtn.onclick = () => {
      const nowHidden = details.classList.toggle("hidden");
      detailsBtn.textContent = nowHidden ? "Details ▾" : "Details ▴";
    };

    return offT;
  },
};
```

- [ ] **Step 2: Run the webapp test suite**

Run: `pytest bridge/tests/webapp -v`
Expected: all tests PASS (no backend changes in this task; `test_registry_imports_exist` still passes since the file path didn't change).

- [ ] **Step 3: Manual verification with the dev server**

Run: `python bridge/tools/webdev.py`, open `http://localhost:8080`, log in.

Verify:
- The Sensors panel (right column) shows six tiles with live-updating values: Pitch/Roll, Gyro, SoC Temp, CPU, RAM, Hardware (dots for camera/audio/imu/display).
- Clicking **Details ▾** reveals two sparkline canvases ("Attitude" and "System") that start drawing traces as telemetry arrives, and the button label flips to **Details ▴**; clicking again hides them and flips back.
- No console errors (in particular, no `Cannot read properties of null` from the gyro fallback logic — the dev fakes' `FakeImu.read()` returns `{"pitch": 1.0, "roll": -2.0, "gyro_z": 0.5}`, so the Gyro tile should show `0.5°/s`).

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/sensors.js
git commit -m "feat(webapp): redesign Sensors panel with live tiles and a details history view"
```

---

## Task 5: Memory Graph panel — always-visible, growing, Obsidian-style

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/graph.js` (full rewrite)

**Interfaces:**
- Consumes: `GET /api/graph/search?limit=200` (empty `q`) → full graph via Task 1's `GraphStore.all()`; `GET /api/graph/search?q=...` (non-empty `q`) → matching subset via existing `search_text()`. Both already return `{nodes, edges}` in the same shape.
- No change to this panel's registration — it stays the sole entry in `registry.graph`.

- [ ] **Step 1: Rewrite `panels/graph.js`**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/panels/graph.js`:

```js
// Memory Graph panel: shows the whole knowledge graph by default (Obsidian-
// style force layout, growing live via polling since there's no WS push for
// graph mutations), with search highlighting matches instead of replacing
// the view.
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

    function resize() { cv.width = cv.clientWidth; cv.height = cv.clientHeight; }
    resize();
    window.addEventListener("resize", resize);

    function merge(data) {
      const W = cv.width, H = cv.height;
      const byId = new Map(nodes.map((n) => [n.id, n]));
      for (const n of data.nodes) {
        if (byId.has(n.id)) { Object.assign(byId.get(n.id), n); continue; }
        nodes.push({
          ...n, x: W / 2 + (Math.random() - 0.5) * 40, y: H / 2 + (Math.random() - 0.5) * 40,
          vx: 0, vy: 0, born: performance.now(),
        });
      }
      const edgeKey = (e) => `${e.src}:${e.dst}:${e.type}`;
      const existing = new Set(edges.map(edgeKey));
      for (const e of data.edges) if (!existing.has(edgeKey(e))) edges.push(e);
      if (!raf) tick();
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
      let settled = true;
      for (const n of nodes) {
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
      g.globalAlpha = 1;
    }

    cv.onclick = (ev) => {
      const r = cv.getBoundingClientRect();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      selected = nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 < 120) || null;
      detail.textContent = selected
        ? `#${selected.id} [${selected.type}] ${JSON.stringify(selected.props)}`
        : "";
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
```

- [ ] **Step 2: Run the webapp test suite**

Run: `pytest bridge/tests/webapp -v`
Expected: all tests PASS.

- [ ] **Step 3: Manual verification with the dev server**

Run: `python bridge/tools/webdev.py`, open `http://localhost:8080`, log in. The dev fakes start with an empty graph, so seed it first:

```bash
curl -X POST http://localhost:8080/api/graph -H "Content-Type: application/json" -d "{\"op\":\"upsert_node\",\"type\":\"person\",\"props\":{\"name\":\"Ada\"}}"
curl -X POST http://localhost:8080/api/graph -H "Content-Type: application/json" -d "{\"op\":\"upsert_node\",\"type\":\"object\",\"props\":{\"name\":\"red ball\"}}"
```

Verify:
- Reloading (or waiting up to 5s for the poll) shows both seeded nodes appearing in the graph section **without typing anything into the search bar** — this is the key behavior change from today (previously the section stayed empty until a search).
- New nodes visibly grow in size over their first ~400ms after appearing (the "grows" behavior) rather than popping in at full size instantly.
- Typing `ada` into the search bar and clicking **Search** dims the non-matching node (`red ball`) instead of removing it, and highlights/enlarges the Ada node in the "ok" (green) color; the detail line reads "1 matches highlighted".
- Clicking **Clear** removes the dimming and both nodes return to normal.
- Clicking a node shows its `#id [type] {props}` in the detail line below the canvas.

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/graph.js
git commit -m "feat(webapp): Memory Graph shows the full graph by default with search-highlighting"
```

---

## Task 6: Mobile responsive verification

**Files:** none expected to change; this task verifies the responsive CSS already written in Task 2 (Steps 1–2) and fixes any concrete issue found. If a fix is needed, it lands in `bridge/milo_bridge/webapp/static/css/console.css` or `css/theme.css`.

**Interfaces:** none — this task doesn't add new consumable interfaces.

- [ ] **Step 1: Load the console at a mobile viewport**

Run: `python bridge/tools/webdev.py`, open `http://localhost:8080` in a desktop browser, log in, then open devtools' responsive/device mode and set the viewport to 390×844 (an iPhone-class width — anything ≤900px triggers the breakpoint from Task 2).

- [ ] **Step 2: Walk the checklist**

Verify each of the following at the mobile viewport; note and fix any failure before checking it off:
- The status bar shows brand, connection dot, owner label, and a **⋯** button; the CPU/temp/RAM/link/gait/uptime stat group is hidden by default. Tapping **⋯** reveals that stat group on its own full-width row; tapping again hides it.
- The Take Control / STOP / Tools / Logout / theme buttons are all present, wrap onto a second row if needed (via the status bar's `flex-wrap: wrap`), and are each at least 44px tall (touch-friendly, per Task 2's `.btn { min-height: 44px }` mobile rule).
- The cockpit is a single column: Camera panel full-width, Move panel directly below it, Communication panel below that, Sensors panel below that (not side-by-side with anything).
- The joystick pad in the Move panel is reachable and draggable with touch/mouse; the speed slider and STOP button are easily tappable.
- The Communication panel's headphones/push-to-talk/Say controls remain individually gated as in Task 3; the vertical VU meter stays vertical (not flipped to horizontal).
- The Sensors panel's tiles stay in a 2-column grid and remain readable; the Details toggle still expands/collapses the sparklines.
- The Memory Graph section is full-width with a shorter canvas (320px tall per Task 2's media rule) so it doesn't dominate the screen.
- Tapping **Tools** opens the drawer as a full-width overlay (not a 360px sidebar) with a visible backdrop; tapping the backdrop or a close affordance returns to the console.
- No horizontal scrollbar appears on the page at this viewport width (nothing overflows).

- [ ] **Step 3: If any check failed, fix it and re-verify**

Apply the minimal CSS fix in `console.css` (or `theme.css` for status-bar-specific issues) for whatever concretely failed in Step 2, then repeat Step 2's specific failing check to confirm the fix. (No fix is prescribed here in advance — Task 2 already ships the breakpoint, stat-collapse, touch-target sizing, and drawer full-width rules; this step only exists to correct what real rendering reveals, e.g. an unexpected overflow from a fixed pixel width somewhere.)

- [ ] **Step 4: Re-run the desktop manual smoke pass**

Resize back to a desktop width (e.g. 1400px) or exit responsive mode, reload, and re-check Task 2 Step 14's desktop verification list still holds (nothing in this task should have regressed the desktop layout).

- [ ] **Step 5: Commit (only if Step 3 produced changes)**

```bash
git add bridge/milo_bridge/webapp/static/css
git commit -m "fix(webapp): mobile layout corrections found during responsive verification"
```

If Step 3 found nothing to fix, skip this commit — there's nothing to record.

---

## Task 7: Documentation — update the dashboard guide

**Files:**
- Modify: `docs/WEB-DASHBOARD.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Rewrite §3 "Feature tour" and §5 "Writing a new card"**

Update `docs/WEB-DASHBOARD.md`:

- In §1 "What it is": replace "a responsive drag-and-resize card grid — arrange it once, on one device, and it stays arranged; open the same URL from a second phone and get a different, independent layout" with a description of the fixed cockpit layout — status bar on top, camera/move/communication in the center, sensors on the side, memory graph full-width below, secondary tools in a drawer — and that the layout is now the same for every device (no per-browser persisted arrangement), with a real mobile breakpoint that reflows to a single column.
- In §3 "Feature tour": replace the ten-card list with the new panel list — Status bar (merged header + system stats), Camera, Move, **Communication** (replacing the separate Ears/Voice entries — describe the headphones toggle + vertical VU meter + gated push-to-talk/Say), Sensors (tiles + Details history view), Memory Graph (always shows the full graph, search highlights instead of filtering), and the Tools drawer (Poses & Emotes, Servo Test, Bridge Log). Remove the "Cards can be dragged... hidden... reset... persist per-browser in localStorage" paragraph — replace it with one line noting the layout is fixed and the Tools drawer replaces per-card hide/show.
- In §5 "Writing a new card": update the file path from `js/cards/hello.js` to `js/panels/hello.js`, update the registration example to show adding to the appropriate zone array in `registry.js` (`cockpitCenter`/`cockpitSide`/`graph`/`tools`) instead of a flat `cards` array, and note that a panel opts into whole-panel control locking via `needsControl: true` (handled by `layout.js`), or can self-gate individual controls internally (as the Communication panel does) when only part of the panel should lock.

- [ ] **Step 2: Rewrite the manual smoke checklist in §7**

Replace the smoke-checklist bullet list with:

```markdown
### Manual smoke checklist

Run through this in a browser (both themes, and at both a desktop and a
mobile viewport width) after any change that touches the webapp, before
considering it done:

- [ ] Page loads at `http://localhost:8080`; toggle the theme button and
      confirm both light and dark look correct.
- [ ] Click **Take Control** — the Move / Communication (push-to-talk +
      Say) / Poses / Servo Test controls unlock. Open a second tab and try
      **Take Control** there too — it must be denied.
- [ ] With the *first* tab controlling, click **STOP** from the *second*,
      non-controlling tab — it must still work; STOP is never gated by
      control.
- [ ] The Camera panel streams frames continuously and the Bridge Log
      panel (in the Tools drawer) shows new lines arriving live.
- [ ] In the Communication panel, toggle **Listen** without holding
      control — it works, and the vertical VU meter reacts. Confirm
      push-to-talk and Say stay visibly locked until Take Control is held.
- [ ] Seed a couple of graph nodes (see `docs/WEB-DASHBOARD.md`'s `curl`
      example) and confirm they appear in the Memory Graph section
      automatically, without needing to search first; confirm searching
      highlights matches rather than hiding non-matches.
- [ ] Click **Tools** in the status bar — the drawer opens with Poses &
      Emotes, Servo Test, and Bridge Log; clicking the backdrop closes it.
- [ ] At a narrow (≤900px) viewport: the status bar's secondary stats
      collapse behind a **⋯** toggle, the cockpit becomes a single column
      in priority order (camera, move, communication, sensors), and the
      Tools drawer becomes a full-width overlay.
- [ ] Logged-out and login-error flows (`/login`) are unchanged from
      before this redesign — confirm they still work.
```

- [ ] **Step 3: Read the whole file back and check for stale references**

Search the file for any remaining mentions of the removed features (drag, resize, `+ Card`, reset-layout, `grid.js`, `js/cards/`) and correct them.

- [ ] **Step 4: Commit**

```bash
git add docs/WEB-DASHBOARD.md
git commit -m "docs: update dashboard guide for the cockpit console redesign"
```

---

## Self-Review Notes

- **Spec coverage:** Fixed cockpit layout (Task 2), responsive desktop/mobile (Tasks 2 & 6), Sensors tiles + details (Task 4), unified Communication panel with split gating + vertical green/red VU meter (Task 3), Memory Graph always-visible/growing/search-highlighting (Task 5 + Task 1 backend), status bar merging header + system stats (Task 2), Tools drawer for secondary panels (Task 2), documentation update (Task 7). All spec sections (§4–§9) map to a task above.
- **Placeholder scan:** no `TBD`/`TODO`/"add appropriate X" phrasing; every code step shows complete file contents; Task 6's Step 3 intentionally has no pre-written fix because it depends on what Step 2 finds — this is a verification/fix step, not a code-with-placeholder step, consistent with how this codebase already verifies frontend work (manual checklist, not unit tests).
- **Type/name consistency check:** `registry` shape (`cockpitCenter`/`cockpitSide`/`graph`/`tools`) is identical across Tasks 2, 3, 4, 5. `initLayout(registry, bus) -> { toggleTools }` matches its one call site in Task 2's `main.js`. `initStatusBar(el, bus, { onToolsToggle })` matches its one call site. `GraphStore.all(limit)` (Task 1) matches its one call site (`get_search`, same task) and is exercised end-to-end by Task 5's `fetch("/api/graph/search?limit=200")`. Panel `mount(el, {bus})` signature is identical across every panel file in Tasks 2–5.
