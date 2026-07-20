# Memory graph rebuild + GraphRAG recall

Date: 2026-07-20

## Problem

Milo's on-board memory graph (`bridge/milo_bridge/graph/store.py`, a SQLite
property graph) doesn't work as real memory:

1. **Flat schema.** `NODE_TYPES` is only `{person, place, object, event,
   fact}`. There's no way to categorize animals Milo meets or stories people
   tell, and no typed relationship between people (e.g. "she is my
   supervisor") — every edge is an untyped free-text string chosen ad hoc by
   whichever LLM call happens to write it.
2. **No real retrieval.** `CognitionAgent._build_context()`
   (`brain/milo_brain/llm/agent.py`) is the entire "recall" step, and it only
   ever looks at two things: the speaker's 10 most-recent neighbor edges, and
   the 5 globally most-recent `event` nodes. It never searches the graph for
   anything related to what's actually being said this turn.
   `GraphStore.search_text` / the `search_text` op in `graph/api.py` already
   exist and are wired to the web dashboard's search box, but nothing in the
   brain ever calls them. This is why Milo "can't recall things properly" —
   most of the graph is simply never consulted.
3. **Conversation memory isn't categorized.** Every extracted "fact" becomes
   a generic `fact` node tied to whoever was speaking via a `said` edge,
   whether or not the fact was actually about them, about someone else, or
   just incidental small talk.
4. **Web dashboard graph panel is browsing-only.** `graph.js` already shows a
   force-directed view with search and a node/edge count
   (`bridge/milo_bridge/webapp/static/js/panels/graph.js`), but clicking a
   node dumps raw JSON props, and there's no breakdown of what's actually in
   the graph (how many people vs. facts vs. stories, etc.).

## Goals

- A richer, validated node/edge taxonomy: `person`, `animal`, `place`,
  `object`, `event`, `fact`, `story`, `topic`, plus a constrained
  relationship vocabulary for edges between people/animals (supervisor,
  parent, owner, etc.), so relationships between people are queryable, not
  free text.
- Real retrieval: before every reply, the brain searches the graph for
  content relevant to the current utterance (not just the speaker's most
  recent neighbors), making this a genuine graph-RAG loop — search, then
  generate, then write back.
- Conversation memory is categorized on write: facts/relations about a known
  person go under that person; a recounted narrative becomes a `story`
  linked to its teller; anything not clearly about a specific person becomes
  a standalone `topic` node instead of being force-attached to whoever was
  talking.
- The web dashboard's memory graph panel gets a stats breakdown (counts by
  node type) and a readable, auto-composed description for the selected
  node instead of a raw JSON dump.
- A clean start: the current graph is wiped (no migration) so it's populated
  from scratch under the new taxonomy.

## Non-goals

- No text-embedding model / semantic vector search. Retrieval is
  keyword/entity search over existing node text (`search_text`) plus graph
  traversal — no new ML dependency on the Pi.
- No change to face-embedding storage/matching (`add_face_embedding`,
  `match_face`) — unrelated to this rebuild.
- No two-pass/background extraction. Extraction stays in the same LLM call
  that generates the spoken reply (one round trip per utterance, as today).
- No migration path from the current graph contents — this is an explicit
  wipe-and-rebuild per the stated goal.
- No changes to the TTS voice (tracked separately in
  `2026-07-20-tts-female-voice-design.md`).

## Design

### A. Schema (`bridge/milo_bridge/graph/store.py`)

```python
NODE_TYPES = frozenset({
    "person", "animal", "place", "object", "event", "fact", "story", "topic",
})

RELATION_TYPES = frozenset({
    "supervisor_of", "reports_to",
    "parent_of", "child_of",
    "sibling_of", "spouse_of", "friend_of", "knows",
    "owns", "belongs_to",
})

STRUCTURAL_EDGE_TYPES = frozenset({"said", "told", "mentions", "met"})

EDGE_TYPES = RELATION_TYPES | STRUCTURAL_EDGE_TYPES
```

- `animal` props: `{"name": ..., "species": <optional>}`.
- `story` props: `{"text": ...}` — a narrative a person recounted, linked
  from the teller via a `told` edge (`person --told--> story`).
- `topic` props: `{"text": ...}` — a standalone note for conversation
  content not clearly about a specific person; no person edge is created.
- `fact` keeps its current shape (`{"text": ...}`) for short attributes,
  linked via `said` (`person --said--> fact`), as today.

Direction convention for `RELATION_TYPES`, fixed and documented in the
module docstring: `src --relation--> dst` reads "src is `relation` of dst"
(e.g. `Jane --supervisor_of--> Daham` means Jane supervises Daham). Because
`neighbors()` matches `WHERE src=? OR dst=?`, a single directional edge is
findable from either node — no inverse edges are stored. Presentation code
(agent context builder, web UI) is responsible for phrasing the relation
correctly depending on which side is being described.

`upsert_edge` gains the same validation `upsert_node` already has:

```python
def upsert_edge(self, src, dst, type, props=None):
    if type not in EDGE_TYPES:
        raise ValueError(f"unknown edge type {type!r}")
    ...
```

This is the key reliability change: today `upsert_edge` accepts any string,
so a small local model's inconsistent labels ("boss" vs "supervisor" vs
"manager") would each become a distinct, unqueryable edge type. Constraining
to `EDGE_TYPES` forces consistency at the store layer regardless of what the
LLM emits (invalid types are caught and dropped by the caller, not surfaced
as a crash — see Error handling).

### B. Extraction (`brain/milo_brain/llm/agent.py`)

`SYSTEM_PROMPT`'s output JSON schema grows from `{reply, facts}` to:

```json
{
  "reply": "what you say out loud",
  "facts": ["short new facts about the speaker, empty if none"],
  "entities": [
    {"name": "Jane", "kind": "person", "relation": "supervisor_of", "with": "speaker"}
  ],
  "story": "a longer narrative the speaker just recounted, or null",
  "topic": "a general note if this exchange wasn't really about the speaker, or null"
}
```

- `entities[].kind` is `"person"` or `"animal"`.
- `entities[].relation` must be one of `RELATION_TYPES`; the prompt lists the
  allowed values explicitly (a fixed enum is small enough for a 3B model to
  select from reliably, versus generating free text).
- `entities[].with` is `"speaker"` or the name of another entity mentioned
  this turn — resolved to a node id by the same find-or-create-by-name logic
  used for the speaker.
- All new fields are optional; `sanitize()` treats missing/malformed fields
  as empty, exactly like it already does for `facts`.

`AgentResult` gains `entities: list[dict]`, `story: str | None`,
`topic: str | None` (all `field(default_factory=...)`/`None` defaults).
`sanitize()` caps list length and string length for the new fields the same
way it already does for `facts` (max 5 items, max ~300 chars each), and
drops any `entities[]` item whose `relation` isn't in `RELATION_TYPES`
(logged, not raised).

`_write_facts` is renamed `_write_memory` and extended:

```python
async def _write_memory(self, person, result: AgentResult) -> None:
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

`_find_or_create_entity(name, kind)` searches existing nodes of that `kind`
by case-insensitive name match (`store.query(type=kind)` then filter in
Python, mirroring how `_maybe_learn_name` already creates person nodes) and
creates one if none matches.

### C. Retrieval — GraphRAG (`_build_context`)

Retrieval happens *before* the reply-generating call (extraction happens
*inside* that same call's output, so retrieval can't depend on it) and uses
keyword search against the raw transcript, per the graph's existing
`search_text`:

```python
STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "to", "of", ...}

def extract_keywords(transcript: str, max_keywords: int = 5) -> list[str]:
    words = re.findall(r"[A-Za-z][\w'-]*", transcript)
    scored = [w for w in words if w.lower() not in STOPWORDS and len(w) >= 4]
    # Capitalized words (likely names/proper nouns) ranked first.
    scored.sort(key=lambda w: (not w[0].isupper(), -len(w)))
    seen, out = set(), []
    for w in scored:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
        if len(out) >= max_keywords:
            break
    return out
```

```python
async def _build_context(self, person, transcript: str) -> str:
    lines = []
    if person is None:
        lines.append(
            "You are talking to someone you have not identified yet. Chat "
            "naturally; you may ask their name once if it feels right, but "
            "don't insist."
        )
    else:
        lines.append(f"Speaker: {person.get('props', {}).get('name', 'unknown')}")
        node_id = person.get("id")
        if node_id is not None:
            neighbors = await self._graph.call("neighbors", node_id=node_id, limit=10)
            for item in neighbors.get("neighbors", []):
                lines.append(self._describe_neighbor(node_id, item))

    seen_ids = {person.get("id")} if person else set()
    for kw in extract_keywords(transcript):
        result = await self._graph.call("search_text", q=kw, limit=5)
        for node in result.get("nodes", []):
            if node["id"] in seen_ids:
                continue
            seen_ids.add(node["id"])
            lines.append(f"- recalled ({node['type']}): {self._summarize_node(node)}")

    recent = await self._graph.call("recent_events", limit=5)
    for node in recent.get("nodes", []):
        text = node.get("props", {}).get("text")
        if text:
            lines.append(f"- recent event: {text}")
    return "\n".join(lines)
```

`_describe_neighbor(node_id, item)` replaces the current raw
`f"- {edge.get('type', 'related')}: {summary}"` line with direction-aware
phrasing for `RELATION_TYPES` edges (e.g. an edge `Jane --supervisor_of-->
Daham` viewed from Daham's side reads `"- reports to: Jane"`; viewed from
Jane's side it reads `"- supervisor of: Daham"`), and falls back to the
existing generic phrasing for structural edges (`said`, `told`, `mentions`,
`met`).

`_summarize_node(node)` returns `node.props.text` for
fact/story/topic/event, or `node.props.name` for person/animal/place/object
— the same fallback chain the current code already uses inline.

This is what makes recall actually work: previously the only way a fact
reached the context was being in the speaker's most recent 10 edges: now
anything in the graph whose text matches a keyword from what's currently
being said gets pulled in, regardless of when it was written or who it was
originally attached to.

### D. Web dashboard (`bridge/milo_bridge/graph/store.py`,
`bridge/milo_bridge/webapp/api/graph.py`, `graph.js`)

`GraphStore.stats()`:

```python
def stats(self) -> dict:
    by_type = dict(self._db.execute(
        "SELECT type, COUNT(*) FROM nodes GROUP BY type"
    ).fetchall())
    total_edges = self._db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    return {"by_type": by_type, "total_nodes": sum(by_type.values()), "total_edges": total_edges}
```

New route:

```python
async def get_stats(request: web.Request) -> web.Response:
    return web.json_response(request.app["deps"].graph_store.stats())

def register(app):
    ...
    app.router.add_get("/api/graph/stats", get_stats)
```

`graph.js` adds a stats bar above the existing search box (fetched on
`loadAll()` and refreshed on the existing `POLL_MS` interval):

```js
async function loadStats() {
  const s = await fetch("/api/graph/stats").then((r) => r.json()).catch(() => null);
  if (!s) return;
  const parts = Object.entries(s.by_type)
    .filter(([, n]) => n > 0)
    .map(([type, n]) => `${n} ${type}${n === 1 ? "" : "s"}`);
  statsEl.textContent = parts.join(" · ");
}
```

The node-click detail panel (`cv.onpointerup`) replaces
`` `#${selected.id} [${selected.type}] ${JSON.stringify(selected.props)}` ``
with an auto-composed description built from the node plus a fresh
`neighbors` fetch for that node (reusing the existing `POST /api/graph`
`{op: "neighbors", node_id}`), formatted with the same direction-aware
relation phrasing described in section C (implemented independently in JS,
since the web panel and the brain are different runtimes, but following the
same convention so a person reads the same relationships the same way in
both places) — e.g. `"Daham — person. 3 facts, 2 stories. supervisor: Jane
Doe."`.

### E. Wipe and cutover

No `ALTER TABLE` is needed (`type`/`props` are already free-form columns);
this is a data wipe, not a schema migration. Rollout: stop the bridge
service, delete `graph.db` under `cfg.data_dir` (`BridgeConfig.graph_db_path`,
`bridge/milo_bridge/config.py:58`) — the existing nightly backup mechanism in
`store.py` means a pre-wipe snapshot exists under `data_dir/backups` if ever
needed — then restart so the graph rebuilds from scratch under the new
taxonomy and validation.

## Error handling

- `upsert_edge`'s new type validation raises `ValueError` for an unknown
  type, exactly like `upsert_node` already does for `NODE_TYPES` — callers
  (the `GraphApi` wire handler) already convert any exception into
  `{"id":..., "error": "..."}` rather than crashing the session, so an
  invalid `relation` from a bad LLM extraction surfaces as a dropped write,
  never a broken reply.
- `sanitize()` continues to guarantee `AgentResult` is always well-formed
  even when the LLM's JSON is malformed or missing the new fields entirely —
  extraction failure degrades to "nothing new written," never blocks the
  spoken reply.
- `_find_or_create_entity` and `_write_memory` wrap each graph call the same
  way `_maybe_learn_name` already does: a failed `upsert_node`/`upsert_edge`
  call is logged and skipped, not propagated, since losing one memory write
  must never break the conversation.
- Retrieval (`_build_context`) failures (e.g. a `search_text` call erroring)
  degrade to fewer context lines, not an exception — the method already
  builds `lines` incrementally, so a failed keyword lookup just contributes
  nothing for that keyword.

## Testing

- `store.py`: `upsert_edge` rejects an edge type outside `EDGE_TYPES`;
  accepts every member of `RELATION_TYPES` and `STRUCTURAL_EDGE_TYPES`;
  `upsert_node` accepts the three new node types; `stats()` returns correct
  per-type counts and totals against a seeded store.
- `agent.py`: `extract_keywords` on representative transcripts (proper
  nouns ranked first, stopwords excluded, capped at `max_keywords`);
  `sanitize()` drops an `entities[]` item with an invalid `relation` but
  keeps valid ones; `_write_memory` end-to-end against a fake graph client —
  facts get `said` edges, entities get the correct typed relation edge with
  correct `src`/`dst` per the direction convention, a story gets a `told`
  edge, a topic gets a standalone node with no person edge.
- `_build_context`: given a fake graph client, asserts the speaker's direct
  neighbors are included, keyword-matched `search_text` results not in the
  neighbor set are appended, and duplicates (a node that's both a neighbor
  and a keyword match) appear once.
- `_describe_neighbor`: direction-aware phrasing for both sides of a
  `supervisor_of` edge and for an `owns` edge (person/animal), plus the
  fallback phrasing for structural edge types.
- `webapp/test_graph_api.py`: `GET /api/graph/stats` returns expected counts
  against a seeded `graph_store`.
- `graph.js`: manual verification in a browser per this repo's existing
  practice for webapp panels — load the stats bar against a seeded graph,
  click a node and confirm the composed description (not a raw JSON dump)
  renders with correct relation phrasing.
