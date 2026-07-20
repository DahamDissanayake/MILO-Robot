# Memory Graph Rebuild + GraphRAG Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Milo's on-board memory graph with a richer, validated node/edge taxonomy (people, animals, stories, topics, typed relationships) and make the brain actually search the whole graph for relevant memories before every reply, instead of only looking at the speaker's 10 most recent edges.

**Architecture:** A shared vocabulary module in `milo-common` (both `milo-bridge` and `milo-brain` already depend on it) defines the valid node/edge types once, so the store's write-time validation and the brain's extraction prompt can never drift apart. The bridge-side `GraphStore` gains the new node types, edge-type validation, and a `stats()` rollup. The brain-side `CognitionAgent` gains real retrieval (keyword search across the whole graph, not just direct neighbors) and richer extraction (typed relationships, stories, a standalone "topic" bucket for anything not about a specific person). The web dashboard's existing graph panel gains a stats bar and a readable per-node description instead of a raw JSON dump.

**Tech Stack:** Python (SQLite, dataclasses), aiohttp (web API), vanilla JS + Canvas (web panel), pytest.

## Global Constraints

- No new node-type/edge-type vocabulary duplicated between `milo-bridge` and `milo-brain` — both read from `milo_common.graph_types`.
- No `ALTER TABLE` / schema migration — `nodes.type`/`nodes.props` and `edges.type`/`edges.props` are already free-form columns; only in-code validation changes.
- Edge direction convention, fixed everywhere: `src --relation--> dst` reads "src is `relation` of dst".
- Retrieval and extraction stay in the existing single LLM round trip per utterance — no second LLM call, no embedding model.
- Any graph write failure (invalid type, etc.) is logged/dropped, never raised out of the conversation loop.

---

### Task 1: Shared node/edge type vocabulary (`milo-common`)

**Files:**
- Create: `common/milo_common/graph_types.py`
- Test: `common/tests/test_graph_types.py`

**Interfaces:**
- Produces: `NODE_TYPES: frozenset[str]`, `RELATION_TYPES: frozenset[str]`, `STRUCTURAL_EDGE_TYPES: frozenset[str]`, `EDGE_TYPES: frozenset[str]` — imported by `bridge/milo_bridge/graph/store.py` (Task 2) and `brain/milo_brain/llm/agent.py` (Tasks 5-8).

- [ ] **Step 1: Write the failing test**

Create `common/tests/test_graph_types.py`:

```python
from milo_common.graph_types import EDGE_TYPES, NODE_TYPES, RELATION_TYPES, STRUCTURAL_EDGE_TYPES


def test_node_types_include_the_new_categories():
    assert {"person", "animal", "place", "object", "event", "fact", "story", "topic"} == NODE_TYPES


def test_edge_types_is_the_union_of_relation_and_structural():
    assert EDGE_TYPES == RELATION_TYPES | STRUCTURAL_EDGE_TYPES
    assert RELATION_TYPES.isdisjoint(STRUCTURAL_EDGE_TYPES)


def test_relation_types_cover_the_expected_vocabulary():
    expected = {
        "supervisor_of", "reports_to", "parent_of", "child_of",
        "sibling_of", "spouse_of", "friend_of", "knows", "owns", "belongs_to",
    }
    assert RELATION_TYPES == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd common && python -m pytest tests/test_graph_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_common.graph_types'`

- [ ] **Step 3: Write the module**

Create `common/milo_common/graph_types.py`:

```python
"""Shared knowledge-graph node/edge type vocabulary.

Used by milo-bridge's GraphStore (validated at write time, see
bridge/milo_bridge/graph/store.py) and milo-brain's CognitionAgent (built
into the extraction prompt and validated before an edge is sent over the
wire, see brain/milo_brain/llm/agent.py) so the two packages can never
drift into accepting different edge/node type spellings.

Edge direction convention: ``src --relation--> dst`` always reads "src is
`relation` of dst" (e.g. ``Jane --supervisor_of--> Daham`` means Jane
supervises Daham). A single directional edge is enough to be found from
either node, since GraphStore.neighbors() matches on src OR dst -- no
inverse edges are ever stored.
"""

from __future__ import annotations

NODE_TYPES = frozenset({
    "person", "animal", "place", "object", "event", "fact", "story", "topic",
})

# Typed relationships between people/animals. Read "src <relation> dst".
RELATION_TYPES = frozenset({
    "supervisor_of", "reports_to",
    "parent_of", "child_of",
    "sibling_of", "spouse_of", "friend_of", "knows",
    "owns", "belongs_to",
})

# Structural bookkeeping edges (who said/told/mentioned/met what), not
# person-to-person relationships.
STRUCTURAL_EDGE_TYPES = frozenset({"said", "told", "mentions", "met"})

EDGE_TYPES = RELATION_TYPES | STRUCTURAL_EDGE_TYPES
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd common && python -m pytest tests/test_graph_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add common/milo_common/graph_types.py common/tests/test_graph_types.py
git commit -m "feat: shared node/edge type vocabulary for the memory graph"
```

---

### Task 2: Store schema — new node types, edge-type validation, stats

**Files:**
- Modify: `bridge/milo_bridge/graph/store.py`
- Test: `bridge/tests/test_graph.py`

**Interfaces:**
- Consumes: `NODE_TYPES`, `EDGE_TYPES` from `milo_common.graph_types` (Task 1).
- Produces: `GraphStore.upsert_edge` raises `ValueError` for a `type` outside `EDGE_TYPES`; `GraphStore.stats() -> dict` with keys `by_type: dict[str, int]`, `total_nodes: int`, `total_edges: int`.

- [ ] **Step 1: Write the failing tests**

Add to `bridge/tests/test_graph.py`:

```python
def test_new_node_types_are_accepted(store):
    animal = store.upsert_node("animal", {"name": "Rex", "species": "dog"})
    story = store.upsert_node("story", {"text": "told me about Japan"})
    topic = store.upsert_node("topic", {"text": "weather chat"})
    assert animal.type == "animal" and story.type == "story" and topic.type == "topic"


def test_invalid_edge_type_rejected(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    jane = store.upsert_node("person", {"name": "Jane"})
    with pytest.raises(ValueError):
        store.upsert_edge(jane.id, daham.id, "boss_of")  # not in EDGE_TYPES


def test_relation_and_structural_edge_types_are_accepted(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    jane = store.upsert_node("person", {"name": "Jane"})
    edge = store.upsert_edge(jane.id, daham.id, "supervisor_of")
    assert edge.type == "supervisor_of"
    fact = store.upsert_node("fact", {"text": "likes robots"})
    said = store.upsert_edge(daham.id, fact.id, "said")
    assert said.type == "said"


def test_stats_counts_nodes_by_type_and_total_edges(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    jane = store.upsert_node("person", {"name": "Jane"})
    store.upsert_node("animal", {"name": "Rex"})
    store.upsert_edge(jane.id, daham.id, "supervisor_of")
    stats = store.stats()
    assert stats["by_type"] == {"person": 2, "animal": 1}
    assert stats["total_nodes"] == 3
    assert stats["total_edges"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && python -m pytest tests/test_graph.py -v -k "new_node_types or invalid_edge_type or relation_and_structural or stats_counts"`
Expected: FAIL — `test_new_node_types_are_accepted` fails with `ValueError: unknown node type 'animal'`; `test_invalid_edge_type_rejected` fails because `upsert_edge` currently accepts any string (no `pytest.raises` triggered); `test_relation_and_structural_edge_types_are_accepted` currently passes already (no validation yet) but is kept as a regression check; `test_stats_counts_nodes_by_type_and_total_edges` fails with `AttributeError: 'GraphStore' object has no attribute 'stats'`.

- [ ] **Step 3: Implement**

In `bridge/milo_bridge/graph/store.py`, replace the local `NODE_TYPES` constant and import the shared vocabulary, and add edge-type validation + `stats()`:

```python
from milo_common.graph_types import EDGE_TYPES, NODE_TYPES

EMBEDDING_DIM = 512
DEFAULT_MATCH_THRESHOLD = 0.45
```

(Remove the old `NODE_TYPES = frozenset({"person", "place", "object", "event", "fact"})` line — it's now imported.)

In `upsert_edge`, add validation as the first line of the method body:

```python
    def upsert_edge(self, src: int, dst: int, type: str, props: dict | None = None) -> Edge:
        if type not in EDGE_TYPES:
            raise ValueError(f"unknown edge type {type!r}")
        for node_id in (src, dst):
            if self.get_node(node_id) is None:
                raise KeyError(f"node {node_id} does not exist")
        ...
```

Add `stats()` near `all()`:

```python
    def stats(self) -> dict:
        """Node counts by type plus totals, for the web dashboard's stats bar."""
        by_type = dict(self._db.execute(
            "SELECT type, COUNT(*) FROM nodes GROUP BY type"
        ).fetchall())
        total_edges = self._db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        return {
            "by_type": by_type,
            "total_nodes": sum(by_type.values()),
            "total_edges": total_edges,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && python -m pytest tests/test_graph.py -v`
Expected: all PASS, including the pre-existing tests (`test_edges_and_neighbors` uses `"said"` and `"knows"`, both in `EDGE_TYPES`; `test_api_upsert_query_roundtrip` uses `"knows"`; `test_api_errors_are_returned_not_raised` only exercises node-type errors, unaffected).

- [ ] **Step 5: Run the full bridge test suite to confirm nothing else broke**

Run: `cd bridge && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/graph/store.py bridge/tests/test_graph.py
git commit -m "feat: validate edge types and add per-type stats to GraphStore"
```

---

### Task 3: Web API — `GET /api/graph/stats`

**Files:**
- Modify: `bridge/milo_bridge/webapp/api/graph.py`
- Test: `bridge/tests/webapp/test_graph_api.py`

**Interfaces:**
- Consumes: `GraphStore.stats()` (Task 2).
- Produces: `GET /api/graph/stats` → `{"by_type": {...}, "total_nodes": N, "total_edges": M}`.

- [ ] **Step 1: Write the failing test**

Add to `bridge/tests/webapp/test_graph_api.py`:

```python
async def test_stats_endpoint_returns_counts_by_type():
    deps = make_deps()
    _seed(deps.graph_store)
    client = await _client(deps)
    try:
        resp = await client.get("/api/graph/stats")
        data = await resp.json()
        assert data["by_type"] == {"person": 2, "object": 1}
        assert data["total_nodes"] == 3
        assert data["total_edges"] == 2
    finally:
        await client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && python -m pytest tests/webapp/test_graph_api.py::test_stats_endpoint_returns_counts_by_type -v`
Expected: FAIL with a 404 (route not registered).

- [ ] **Step 3: Implement**

In `bridge/milo_bridge/webapp/api/graph.py`:

```python
async def get_stats(request: web.Request) -> web.Response:
    deps = request.app["deps"]
    return web.json_response(deps.graph_store.stats())


def register(app: web.Application) -> None:
    app.router.add_post("/api/graph", post_graph)
    app.router.add_get("/api/graph/search", get_search)
    app.router.add_get("/api/graph/stats", get_stats)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bridge && python -m pytest tests/webapp/test_graph_api.py::test_stats_endpoint_returns_counts_by_type -v`
Expected: PASS

- [ ] **Step 5: Run the full webapp test suite to confirm nothing else broke**

Run: `cd bridge && python -m pytest tests/webapp -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/api/graph.py bridge/tests/webapp/test_graph_api.py
git commit -m "feat: expose GraphStore.stats() as GET /api/graph/stats"
```

---

### Task 4: Web UI — stats bar + readable node description

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/graph.js`
- Modify: `bridge/milo_bridge/webapp/static/css/console.css`

**Interfaces:**
- Consumes: `GET /api/graph/stats` (Task 3); existing `POST /api/graph {op: "neighbors", node_id}` wire op (already implemented, unchanged).

- [ ] **Step 1: Add the stats bar element and CSS**

In `bridge/milo_bridge/webapp/static/js/panels/graph.js`, extend the panel's markup (currently built in `mount()`):

```js
    el.innerHTML = `
      <div class="graph-search">
        <input id="gq" placeholder="Search memory… (name, type, anything)">
        <button class="btn" id="gsearch">Search</button>
        <button class="btn ghost" id="gclear">Clear</button>
      </div>
      <div id="graph-stats" class="muted graph-stats"></div>
      <canvas id="graph-canvas"></canvas>
      <div id="graph-detail" class="muted"></div>`;
    const cv = el.querySelector("#graph-canvas"), g = cv.getContext("2d");
    const detail = el.querySelector("#graph-detail");
    const statsEl = el.querySelector("#graph-stats");
```

In `bridge/milo_bridge/webapp/static/css/console.css`, next to the existing `.graph-search` rule (around line 224):

```css
.graph-stats { margin-bottom: 6px; font-size: 12px; }
```

- [ ] **Step 2: Fetch and render stats**

Add a `loadStats` function and call it alongside the existing `loadAll()`:

```js
    async function loadStats() {
      const s = await fetch("/api/graph/stats").then((r) => r.json()).catch(() => null);
      if (!s) return;
      const parts = Object.entries(s.by_type)
        .filter(([, n]) => n > 0)
        .map(([type, n]) => `${n} ${type}${n === 1 ? "" : "s"}`);
      statsEl.textContent = parts.length ? parts.join(" · ") : "memory is empty";
    }
```

Update the mount/poll wiring so stats load and refresh alongside the graph itself:

```js
    loadAll();
    loadStats();
    const pollId = setInterval(() => { loadAll(); loadStats(); }, POLL_MS);
    return () => { clearInterval(pollId); if (raf) cancelAnimationFrame(raf); window.removeEventListener("resize", resize); };
```

(This replaces the existing `loadAll(); const pollId = setInterval(loadAll, POLL_MS);` block at the bottom of `mount()`.)

- [ ] **Step 3: Replace the raw-JSON node description with a composed one**

Add a description builder and use it in `cv.onpointerup` (currently `` `#${selected.id} [${selected.type}] ${JSON.stringify(selected.props)}` ``):

```js
    const RELATION_PHRASING = {
      supervisor_of: ["supervisor of", "reports to"],
      reports_to: ["reports to", "supervisor of"],
      parent_of: ["parent of", "child of"],
      child_of: ["child of", "parent of"],
      sibling_of: ["sibling of", "sibling of"],
      spouse_of: ["spouse of", "spouse of"],
      friend_of: ["friend of", "friend of"],
      knows: ["knows", "knows"],
      owns: ["owns", "belongs to"],
      belongs_to: ["belongs to", "owns"],
    };

    function describeRelation(edgeType, viewerIsSrc) {
      const phrasing = RELATION_PHRASING[edgeType];
      if (!phrasing) return edgeType;
      return viewerIsSrc ? phrasing[0] : phrasing[1];
    }

    async function describeNode(node) {
      const label = node.props?.name || node.props?.text || `${node.type}#${node.id}`;
      const body = await fetch("/api/graph", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ op: "neighbors", node_id: node.id, limit: 20 }),
      }).then((r) => r.json()).catch(() => ({ neighbors: [] }));
      const rels = (body.neighbors || []).map((item) => {
        const other = item.node?.props?.name || item.node?.props?.text || `#${item.node?.id}`;
        const viewerIsSrc = item.edge?.src === node.id;
        return `${describeRelation(item.edge?.type, viewerIsSrc)}: ${other}`;
      });
      return `${label} — ${node.type}${rels.length ? ". " + rels.join(", ") : ""}`;
    }

    cv.onpointerup = (ev) => {
      if (!moved) {
        selected = hitTest(ev);
        if (selected) {
          detail.textContent = "…";
          describeNode(selected).then((text) => { if (selected) detail.textContent = text; });
        } else {
          detail.textContent = "";
        }
      }
      dragNode = null; panning = false;
      draw();
    };
```

- [ ] **Step 4: Manual verification in a browser**

This panel has no automated JS test suite (none exists in this repo — verification for `graph.js` has always been manual, per the panel's own comments). Start the bridge webapp locally (or against a robot), seed a few nodes/edges via the graph CLI or `/api/graph` `upsert_node`/`upsert_edge` calls including at least one `supervisor_of` relation, open the Memory Graph panel, and confirm:
- The stats bar shows correct counts (e.g. `2 persons · 1 story`).
- Clicking a person node shows a readable line (e.g. `"Daham — person. reports to: Jane"`), not raw JSON.
- The stats bar and canvas both update within one poll interval (5s) after adding a node via another client/tab.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/graph.js bridge/milo_bridge/webapp/static/css/console.css
git commit -m "feat: memory graph panel gets a stats bar and readable node descriptions"
```

---

### Task 5: Retrieval helpers — keyword extraction and relation phrasing

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Produces: `extract_keywords(transcript: str, max_keywords: int = 5) -> list[str]`, `describe_relation(edge_type: str, viewer_is_src: bool) -> str`, `summarize_node(node: dict) -> str | None` — all module-level pure functions, consumed by `_build_context` in Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_agent.py`, near `test_extract_name_variants`:

```python
def test_extract_keywords_prefers_proper_nouns_and_drops_stopwords():
    kws = extract_keywords("Have you seen Jane lately about her new project")
    assert "Jane" in kws
    assert "have" not in [k.lower() for k in kws]
    assert kws[0] == "Jane"  # capitalized proper noun ranked first


def test_extract_keywords_caps_at_max_keywords():
    kws = extract_keywords("apple banana cherry dragon elephant flamingo giraffe", max_keywords=3)
    assert len(kws) == 3


def test_extract_keywords_deduplicates_case_insensitively():
    kws = extract_keywords("Japan japan JAPAN trip")
    assert len(kws) == 2  # "Japan" and "trip", not three separate "japan" entries


def test_describe_relation_is_direction_aware():
    assert describe_relation("supervisor_of", viewer_is_src=True) == "supervisor of"
    assert describe_relation("supervisor_of", viewer_is_src=False) == "reports to"
    assert describe_relation("owns", viewer_is_src=True) == "owns"
    assert describe_relation("owns", viewer_is_src=False) == "belongs to"
    assert describe_relation("friend_of", viewer_is_src=True) == "friend of"
    assert describe_relation("friend_of", viewer_is_src=False) == "friend of"


def test_describe_relation_falls_back_to_the_raw_type_for_structural_edges():
    assert describe_relation("said", viewer_is_src=True) == "said"
    assert describe_relation("told", viewer_is_src=False) == "told"


def test_summarize_node_prefers_text_then_name_then_none():
    assert summarize_node({"props": {"text": "likes robots", "name": "ignored"}}) == "likes robots"
    assert summarize_node({"props": {"name": "Jane"}}) == "Jane"
    assert summarize_node({"props": {}}) is None
```

Update the import block at the top of `brain/tests/test_agent.py`:

```python
from milo_brain.llm.agent import (
    SYSTEM_PROMPT,
    VALID_FACES,
    CognitionAgent,
    OllamaClient,
    describe_relation,
    extract_keywords,
    extract_name,
    parse_llm_json,
    repair_tool_args,
    sanitize,
    summarize_node,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "extract_keywords or describe_relation or summarize_node"`
Expected: FAIL with `ImportError: cannot import name 'extract_keywords' from 'milo_brain.llm.agent'` (and similarly for the other two).

- [ ] **Step 3: Implement**

In `brain/milo_brain/llm/agent.py`, add near `extract_name` (after its definition):

```python
STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "and", "or", "but", "to",
    "of", "in", "on", "at", "for", "with", "this", "that", "have", "has",
    "had", "you", "your", "i", "me", "my", "we", "our", "they", "them",
    "he", "she", "it", "its", "about", "just", "really", "very", "so",
    "not", "do", "does", "did", "will", "would", "can", "could", "should",
    "from", "as", "be", "been", "was", "were", "there", "here", "what",
    "when", "where", "who", "how", "why",
})


def extract_keywords(transcript: str, max_keywords: int = 5) -> list[str]:
    """Cheap keyword pull for graph-wide recall: capitalized proper nouns
    first, then longer words, stopwords and short words dropped. No LLM
    call -- this runs before the reply-generating call, so it can't lean
    on that turn's own extraction (see CognitionAgent._build_context)."""
    words = re.findall(r"[A-Za-z][\w'-]*", transcript)
    candidates = [w for w in words if w.lower() not in STOPWORDS and len(w) >= 4]
    candidates.sort(key=lambda w: (not w[0].isupper(), -len(w)))
    seen: set[str] = set()
    out: list[str] = []
    for w in candidates:
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= max_keywords:
            break
    return out


# Direction-aware phrasing for RELATION_TYPES edges: (phrase when the node
# being described is the edge's src, phrase when it's the dst). Structural
# edge types (said/told/mentions/met) aren't in this table and read fine
# using the raw edge type as-is.
RELATION_PHRASING: dict[str, tuple[str, str]] = {
    "supervisor_of": ("supervisor of", "reports to"),
    "reports_to": ("reports to", "supervisor of"),
    "parent_of": ("parent of", "child of"),
    "child_of": ("child of", "parent of"),
    "sibling_of": ("sibling of", "sibling of"),
    "spouse_of": ("spouse of", "spouse of"),
    "friend_of": ("friend of", "friend of"),
    "knows": ("knows", "knows"),
    "owns": ("owns", "belongs to"),
    "belongs_to": ("belongs to", "owns"),
}


def describe_relation(edge_type: str, viewer_is_src: bool) -> str:
    phrasing = RELATION_PHRASING.get(edge_type)
    if phrasing is None:
        return edge_type
    return phrasing[0] if viewer_is_src else phrasing[1]


def summarize_node(node: dict) -> str | None:
    props = node.get("props", {})
    return props.get("text") or props.get("name")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "extract_keywords or describe_relation or summarize_node"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat: keyword extraction and direction-aware relation phrasing for graph recall"
```

---

### Task 6: Rewire `_build_context` for whole-graph keyword recall

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Consumes: `extract_keywords`, `describe_relation`, `summarize_node` (Task 5); existing wire ops `neighbors`, `search_text`, `recent_events` (the last two already implemented in `graph/api.py`, `search_text` just never called from the brain until now).
- Produces: `CognitionAgent._build_context(self, person: dict | None, transcript: str) -> str` (signature changes — gains `transcript`).

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_agent.py`, near `FakeGraph`:

```python
class SearchableFakeGraph:
    """A FakeGraph variant with a configurable keyword -> nodes index and a
    configurable neighbor list, for testing _build_context's whole-graph
    keyword recall (search_text) both independently of and overlapping with
    the direct-neighbors path."""

    def __init__(self, search_index=None, neighbors=None):
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 100
        self._search_index = search_index or {}
        self._neighbors = neighbors or []

    async def call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        if op == "upsert_node":
            self._next_id += 1
            return {"node": {"id": self._next_id, "type": kwargs.get("type"),
                             "props": kwargs.get("props", {})}}
        if op == "neighbors":
            return {"neighbors": self._neighbors}
        if op == "recent_events":
            return {"nodes": []}
        if op == "search_text":
            return {"nodes": self._search_index.get(kwargs.get("q", ""), [])}
        return {"nodes": []}
```

And tests using it:

```python
def test_build_context_recalls_keyword_matches_beyond_direct_neighbors():
    story_node = {"id": 55, "type": "story", "props": {"text": "trip to Japan last year"}}
    graph = SearchableFakeGraph(search_index={"Japan": [story_node]})
    agent = CognitionAgent(FakeLlm(), graph, FakeMcp())

    context = asyncio.run(agent._build_context(DAHAM, "tell me about Japan"))
    assert "trip to Japan last year" in context
    search_calls = [kw for op, kw in graph.calls if op == "search_text"]
    assert any(kw.get("q") == "Japan" for kw in search_calls)


def test_build_context_does_not_duplicate_a_node_that_is_both_neighbor_and_keyword_match():
    fact_node = {"id": 1, "type": "fact", "props": {"text": "likes robots"}}
    graph = SearchableFakeGraph(
        neighbors=[{"edge": {"type": "said", "src": DAHAM["id"], "dst": 1}, "node": fact_node}],
        search_index={"robots": [fact_node]},
    )
    context = asyncio.run(CognitionAgent(FakeLlm(), graph, FakeMcp())._build_context(DAHAM, "tell me about robots"))
    assert context.count("likes robots") == 1  # once from neighbors, not again from the keyword match


def test_build_context_deduplicates_a_node_matched_by_multiple_keywords():
    fact_node = {"id": 7, "type": "fact", "props": {"text": "likes robots"}}
    graph = SearchableFakeGraph(search_index={"robots": [fact_node], "likes": [fact_node]})
    context = asyncio.run(CognitionAgent(FakeLlm(), graph, FakeMcp())._build_context(DAHAM, "robots and likes robots"))
    assert context.count("likes robots") == 1  # matched by two keywords, appears once
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "build_context_recalls or build_context_does_not_duplicate or build_context_deduplicates"`
Expected: FAIL — `TypeError: _build_context() missing 1 required positional argument: 'transcript'` (current signature is `_build_context(self, person)`).

- [ ] **Step 3: Implement**

In `brain/milo_brain/llm/agent.py`, replace `_build_context`:

```python
    async def _build_context(self, person: dict | None, transcript: str) -> str:
        lines: list[str] = []
        person_id = person.get("id") if person else None
        if person is None:
            lines.append(
                "You are talking to someone you have not identified yet. Chat "
                "naturally; you may ask their name once if it feels right, but "
                "don't insist."
            )
        else:
            lines.append(f"Speaker: {person.get('props', {}).get('name', 'unknown')}")
            if person_id is not None:
                neighbors = await self._graph.call("neighbors", node_id=person_id, limit=10)
                for item in neighbors.get("neighbors", []):
                    node = item.get("node") or {}
                    edge = item.get("edge") or {}
                    summary = summarize_node(node)
                    if not summary:
                        continue
                    label = describe_relation(edge.get("type", "related"), edge.get("src") == person_id)
                    lines.append(f"- {label}: {summary}")

        seen_ids = {person_id} if person_id is not None else set()
        for kw in extract_keywords(transcript):
            result = await self._graph.call("search_text", q=kw, limit=5)
            for node in result.get("nodes", []):
                if node.get("id") in seen_ids:
                    continue
                seen_ids.add(node.get("id"))
                summary = summarize_node(node)
                if summary:
                    lines.append(f"- recalled ({node.get('type')}): {summary}")

        recent = await self._graph.call("recent_events", limit=5)
        for node in recent.get("nodes", []):
            text = node.get("props", {}).get("text")
            if text:
                lines.append(f"- recent event: {text}")
        return "\n".join(lines)
```

Update the one call site, in `on_utterance`:

```python
        context = await self._build_context(speaker, transcript)
```

(This replaces `context = await self._build_context(speaker)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "build_context_recalls or build_context_does_not_duplicate or build_context_deduplicates"`
Expected: PASS

- [ ] **Step 5: Run the full brain test suite to confirm nothing else broke**

Run: `cd brain && python -m pytest -q`
Expected: all PASS — `test_known_person_gets_contextual_reply_with_no_tool_calls` still finds `"likes robots"` and `"met Daham yesterday"` in the sent context (the neighbors/recent_events branches are unchanged; `FakeGraph.call` returns `{}` for the now-also-called `"search_text"` op, contributing nothing, since that fake predates this task and was never updated to know about it).

- [ ] **Step 6: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat: _build_context searches the whole graph by keyword, not just direct neighbors"
```

---

### Task 7: Extraction schema — entities, story, topic

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Consumes: `RELATION_TYPES` from `milo_common.graph_types` (Task 1).
- Produces: `AgentResult` gains `entities: list[dict]`, `story: str | None`, `topic: str | None`; `sanitize(data: dict) -> AgentResult` populates and validates them.

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_agent.py`:

```python
def test_sanitize_keeps_valid_entities_and_drops_invalid_relation():
    data = {
        "reply": "ok", "facts": [],
        "entities": [
            {"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"},
            {"name": "Rex", "kind": "animal", "relation": "made_up_relation", "with": "speaker"},
        ],
    }
    result = sanitize(data)
    assert len(result.entities) == 1
    assert result.entities[0] == {"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}


def test_sanitize_drops_entity_with_unknown_kind():
    data = {"reply": "ok", "facts": [], "entities": [
        {"name": "Rex", "kind": "robot", "relation": "owns", "with": "speaker"},
    ]}
    assert sanitize(data).entities == []


def test_sanitize_caps_story_and_topic_length_and_treats_null_as_none():
    data = {"reply": "ok", "facts": [], "story": "x" * 600, "topic": None}
    result = sanitize(data)
    assert len(result.story) == 500
    assert result.topic is None


def test_sanitize_handles_missing_entities_story_topic_fields():
    result = sanitize({"reply": "ok", "facts": []})
    assert result.entities == [] and result.story is None and result.topic is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "sanitize_keeps_valid_entities or sanitize_drops_entity or sanitize_caps_story or sanitize_handles_missing"`
Expected: FAIL — `AttributeError: 'AgentResult' object has no attribute 'entities'`.

- [ ] **Step 3: Implement**

In `brain/milo_brain/llm/agent.py`, add the import near the top:

```python
from milo_common.graph_types import RELATION_TYPES
```

Extend `AgentResult`:

```python
@dataclass(frozen=True)
class AgentResult:
    reply: str
    facts: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    story: str | None = None
    topic: str | None = None
    new_person_name: str | None = None
```

Update `SYSTEM_PROMPT`'s trailing schema block. Add above the `SYSTEM_PROMPT` definition:

```python
_RELATIONS = ", ".join(sorted(RELATION_TYPES))
```

And change the final paragraph of `SYSTEM_PROMPT` from:

```python
You know things from your on-board memory graph; context about the speaker
follows. Once you're done (with or without using any tools), reply ONLY with
JSON matching this schema:
{{
  "reply": "what you say out loud",
  "facts": ["short new facts about the speaker worth remembering, empty if none"]
}}"""
```

to:

```python
You know things from your on-board memory graph; context about the speaker
follows. Once you're done (with or without using any tools), reply ONLY with
JSON matching this schema:
{{
  "reply": "what you say out loud",
  "facts": ["short new facts about the speaker worth remembering, empty if none"],
  "entities": [
    {{"name": "their name", "kind": "person or animal",
      "relation": "one of: {_RELATIONS}", "with": "speaker or another name mentioned this turn"}}
  ],
  "story": "a longer narrative the speaker just recounted, or null",
  "topic": "a general note if this exchange wasn't really about the speaker, or null"
}}
Only include "entities" when the speaker described a relationship (e.g.
"she is my supervisor", "this is my dog Rex") -- leave it empty otherwise.
Only set "story" when the speaker recounted something that happened to
them, not for ordinary chat. Only set "topic" for exchanges that aren't
really about the speaker personally."""
```

Replace `sanitize`:

```python
def sanitize(data: dict) -> AgentResult:
    facts = [str(f)[:300] for f in data.get("facts", []) if str(f).strip()][:5]

    entities = []
    raw_entities = data.get("entities")
    for e in raw_entities if isinstance(raw_entities, list) else []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()[:100]
        kind = str(e.get("kind", "")).strip().lower()
        relation = str(e.get("relation", "")).strip().lower()
        with_name = str(e.get("with", "")).strip()[:100] or None
        if not name or kind not in {"person", "animal"} or relation not in RELATION_TYPES:
            continue
        entities.append({"name": name, "kind": kind, "relation": relation, "with": with_name})
    entities = entities[:5]

    def _clean_text(value, max_len=500):
        if not value:
            return None
        cleaned = str(value)[:max_len].strip()
        return cleaned or None

    story = _clean_text(data.get("story"))
    topic = _clean_text(data.get("topic"))

    return AgentResult(
        reply=str(data.get("reply", ""))[:600], facts=facts,
        entities=entities, story=story, topic=topic,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "sanitize"`
Expected: PASS, including the pre-existing `test_sanitize_drops_face_and_move_keeps_reply_and_facts`.

- [ ] **Step 5: Run the full brain test suite to confirm nothing else broke**

Run: `cd brain && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat: extraction schema gains typed entities, stories, and topics"
```

---

### Task 8: Write path — `_find_or_create_entity` + `_write_memory`

**Files:**
- Modify: `brain/milo_brain/llm/agent.py`
- Test: `brain/tests/test_agent.py`

**Interfaces:**
- Consumes: `AgentResult.entities/story/topic` (Task 7); wire ops `query`, `upsert_node`, `upsert_edge` (all pre-existing in `graph/api.py`).
- Produces: `CognitionAgent._find_or_create_entity(name: str, kind: str) -> dict | None`; `CognitionAgent._write_memory(person: dict | None, result: AgentResult) -> None` (replaces `_write_facts`).

- [ ] **Step 1: Write the failing tests**

Add to `brain/tests/test_agent.py`:

```python
def test_on_utterance_writes_entity_relation_story_and_topic():
    llm = FakeLlm([{"role": "assistant", "content": json.dumps({
        "reply": "Got it!",
        "facts": [],
        "entities": [{"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}],
        "story": "told me about her trip to Japan last year",
        "topic": "the weather has been nice lately",
    })}])
    graph = FakeGraph()
    agent = CognitionAgent(llm, graph, FakeMcp())

    result = asyncio.run(agent.on_utterance(
        "Jane is my supervisor, she just got back from Japan", DAHAM, None))
    assert result.reply == "Got it!"

    person_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "person"]
    assert person_creates and person_creates[0]["props"]["name"] == "Jane"

    edge_calls = [kw for op, kw in graph.calls if op == "upsert_edge"]
    assert len(edge_calls) == 2  # supervisor_of + told, topic gets no edge

    relation_edge = next(kw for kw in edge_calls if kw["type"] == "supervisor_of")
    assert relation_edge["dst"] == DAHAM["id"]  # Jane --supervisor_of--> Daham
    assert isinstance(relation_edge["src"], int)

    story_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "story"]
    assert story_creates and "Japan" in story_creates[0]["props"]["text"]
    told_edge = next(kw for kw in edge_calls if kw["type"] == "told")
    assert told_edge["src"] == DAHAM["id"]

    topic_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "topic"]
    assert topic_creates and "weather" in topic_creates[0]["props"]["text"]


class GraphWithExistingJane(FakeGraph):
    async def call(self, op, **kwargs):
        if op == "query" and kwargs.get("type") == "person":
            self.calls.append((op, kwargs))
            return {"nodes": [{"id": 42, "type": "person", "props": {"name": "Jane"}}]}
        return await super().call(op, **kwargs)


def test_entity_relation_reuses_existing_person_by_case_insensitive_name():
    llm = FakeLlm([{"role": "assistant", "content": json.dumps({
        "reply": "ok", "facts": [],
        "entities": [{"name": "jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}],
    })}])
    graph = GraphWithExistingJane()
    agent = CognitionAgent(llm, graph, FakeMcp())
    asyncio.run(agent.on_utterance("jane is my supervisor", DAHAM, None))

    person_creates = [kw for op, kw in graph.calls if op == "upsert_node" and kw.get("type") == "person"]
    assert person_creates == []  # reused id 42, not created again
    edge_calls = [kw for op, kw in graph.calls if op == "upsert_edge"]
    assert edge_calls[0]["src"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "writes_entity_relation or reuses_existing_person"`
Expected: FAIL — the current `on_utterance` only ever writes `facts` via `_write_facts`; no `entities`/`story`/`topic` handling exists yet, so `person_creates`/`story_creates`/`topic_creates` are all empty and the assertions fail.

- [ ] **Step 3: Implement**

In `brain/milo_brain/llm/agent.py`, replace `_write_facts` with:

```python
    async def _find_or_create_entity(self, name: str, kind: str) -> dict | None:
        existing = await self._graph.call("query", type=kind, limit=200)
        for node in existing.get("nodes", []):
            if node.get("props", {}).get("name", "").lower() == name.lower():
                return node
        created = await self._graph.call("upsert_node", type=kind, props={"name": name})
        return created.get("node")

    async def _write_memory(self, person: dict | None, result: AgentResult) -> None:
        node_id = person.get("id") if person else None

        for fact in result.facts:
            created = await self._graph.call("upsert_node", type="fact", props={"text": fact})
            fact_id = created.get("node", {}).get("id")
            if node_id is not None and fact_id is not None:
                await self._graph.call("upsert_edge", src=node_id, dst=fact_id, type="said")

        for entity in result.entities:
            target = await self._find_or_create_entity(entity["name"], entity["kind"])
            with_name = entity.get("with")
            if with_name == "speaker":
                subject_id = node_id
            elif with_name:
                subject = await self._find_or_create_entity(with_name, "person")
                subject_id = subject["id"] if subject else None
            else:
                subject_id = None
            if subject_id is not None and target is not None:
                await self._graph.call(
                    "upsert_edge", src=target["id"], dst=subject_id, type=entity["relation"]
                )

        if result.story and node_id is not None:
            created = await self._graph.call("upsert_node", type="story", props={"text": result.story})
            story_id = created.get("node", {}).get("id")
            if story_id is not None:
                await self._graph.call("upsert_edge", src=node_id, dst=story_id, type="told")

        if result.topic:
            await self._graph.call("upsert_node", type="topic", props={"text": result.topic})
```

Update the call site in `on_utterance`:

```python
        await self._write_memory(self._session_person, result)
```

(This replaces `await self._write_facts(self._session_person, result.facts)`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd brain && python -m pytest tests/test_agent.py -v -k "writes_entity_relation or reuses_existing_person"`
Expected: PASS

- [ ] **Step 5: Run the full brain test suite to confirm nothing else broke**

Run: `cd brain && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add brain/milo_brain/llm/agent.py brain/tests/test_agent.py
git commit -m "feat: write typed entity relations, stories, and standalone topics to the graph"
```

---

### Task 9: Wipe and cutover documentation

**Files:**
- Modify: `bridge/milo_bridge/README.md`

**Interfaces:** none (operational documentation only).

- [ ] **Step 1: Add a "Resetting the memory graph" section**

In `bridge/milo_bridge/README.md`, insert after the `## Verifying it's running` section (before `## Package layout`):

```markdown
## Resetting the memory graph

The memory graph (`graph.db` under the configured `data_dir`, default
`~/.milo/graph.db`) uses a schema that's validated in code, not migrated in
the database — upgrading to a new node/edge taxonomy means wiping and
starting fresh rather than an in-place migration:

```bash
sudo systemctl stop milo-bridge
rm ~/.milo/graph.db          # a nightly backup exists under ~/.milo/backups/ if needed
sudo systemctl start milo-bridge
```

The graph rebuilds from scratch as Milo has new conversations.
```

- [ ] **Step 2: Commit**

```bash
git add bridge/milo_bridge/README.md
git commit -m "docs: document how to reset the memory graph for a schema cutover"
```

## Self-Review Notes

- **Spec coverage:** Task 1-2 cover spec section A (schema + validation); Task 3-4 cover section D (web dashboard stats + description); Task 5-6 cover section C (retrieval/GraphRAG); Task 7-8 cover section B (extraction: entities/story/topic + write path); Task 9 covers section E (wipe/cutover). The spec's direction convention (`src --relation--> dst`) is implemented identically in both the write path (Task 8: `src=target["id"], dst=subject_id`) and the two independent read-side phrasing tables (Task 5's `RELATION_PHRASING` in Python, Task 4's `RELATION_PHRASING` in JS) — verified the two tables list the same 10 relation types with matching phrasing.
- **No placeholders:** every step has complete, runnable code, exact test code, and exact commands.
- **Type/name consistency:** `extract_keywords`, `describe_relation`, `summarize_node` (Task 5) are used with identical signatures in `_build_context` (Task 6); `AgentResult.entities/story/topic` (Task 7) match exactly what `_write_memory` (Task 8) reads; `GraphStore.stats()`'s return shape (`by_type`/`total_nodes`/`total_edges`, Task 2) matches what the `/api/graph/stats` route returns verbatim (Task 3) and what `graph.js`'s `loadStats()` consumes (Task 4); `RELATION_TYPES`/`EDGE_TYPES`/`NODE_TYPES` (Task 1) are the single source both `store.py` (Task 2) and `agent.py` (Task 7) import, never redefined locally.
- **Cross-task ordering:** Task 1 (shared vocabulary) precedes both Task 2 (store validation) and Task 7 (prompt/sanitize), since both import from it. Task 5's pure helpers precede Task 6's `_build_context` rewrite, which uses them. Nothing in Tasks 2-4 (bridge-side) depends on Tasks 5-8 (brain-side) or vice versa, so the two halves could also be executed in parallel by different workers if desired.
