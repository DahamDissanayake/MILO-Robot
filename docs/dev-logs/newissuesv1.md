# New Issues v1 — Findings from the graphify knowledge-graph build & architecture trace

**Date:** 2026-07-19
**Source:** `/graphify .` full-corpus build (348 files, 3020 nodes, 5867 edges, 191 communities) plus a guided trace of the two highest-betweenness nodes (`PairedStore`, `ControlBroker`).

> **Scope note / honesty:** These are findings from building and reading the knowledge graph, **not** a code audit. The core code paths I traced (`PairedStore`, `ControlBroker`, and their consumers) were **correct and well-tested** — I did not find functional defects in them. The genuine issues below are mostly about **knowledge-graph build quality, extraction coverage, and things flagged for verification**, plus a couple of low-risk code/tooling observations. One speculative concern was checked and disproven (see §8).

---

## 1. Fragmented `PairedStore` nodes — RESOLVED (graph-wide `_py_` normalization, 2026-07-20)

> **RESOLUTION (2026-07-20):** Fixed via a deterministic, no-token graph normalization pass. Import-site `_py_` artifact nodes were folded into their canonical `.py` class definitions for **9 unambiguous symbols** (PairedStore, MiloSocket, BrainConfig, Peer, Message, ServoDriver, GraphStore, WebDeps, ResultRecorder). Result: **3020→2988 nodes, 5867→5821 edges, 0 dangling edges**, and the God-Node ranking now resolves to real definitions — `PairedStore` is a single node (`common/milo_common/auth.py`, degree 69, now correctly the #2 god node) instead of an `mcp/auth.py` import-site artifact. Ambiguous groups (`__init__.py` ×15) and orphan external symbols (`Path`, `ndarray`, `ComposeResult`, … — no MILO definition) were deliberately left untouched. `TokenRateTracker` was skipped because its only non-`_py_` "code" node is actually a spec-doc concept (no real `.py` definition to merge into). The graph, report, and `graph.html` were all regenerated consistently. Details of the original finding below for the record.



**What (updated after deeper investigation):** `PairedStore` is spread across **7 graph nodes**, not two. Measured edge degrees in `graph.json`:

| node id | source_file | edges | edge character |
|---------|-------------|-------|----------------|
| `bridge_milo_bridge_mcp_auth_py_pairedstore` | bridge/milo_bridge/mcp/auth.py | **56** | 54 INFERRED `uses` + 2 `references` |
| `milo_common_auth_pairedstore` | common/milo_common/auth.py (**real class**) | 17 | clean: `contains`, 9 `method`, `imports`, `rationale_for` |
| `common_milo_common_handshake_py_pairedstore` | common/milo_common/handshake.py | 5 | mostly INFERRED `uses` |
| `brain_tests_test_discovery_py_pairedstore` | brain/tests/test_discovery.py | 3 | INFERRED |
| `common_tests_test_handshake_py_pairedstore` | common/tests/test_handshake.py | 3 | INFERRED |
| `brain_milo_brain_net_discovery_py_pairedstore` | brain/milo_brain/net/discovery.py | 2 | INFERRED |
| `pairedstore` | docs/.../movement-imu-mcp-design.md | 2 | doc concept |

**Root cause (confirmed):** graphify mints a **per-import-site node** for every file that imports a symbol, using an id of the form `{full_path}_py_{symbol}` (note the literal `_py_` from the `.py` extension). These import-site nodes accumulate INFERRED `uses` edges to co-imported symbols. The **actual class definition** node (`milo_common_auth_pairedstore`, no `_py_`) is separate and carries only the clean EXTRACTED structural edges.

**The critical consequence:** the report's **God Node "PairedStore — 57 edges, betweenness 0.111" is the `mcp/auth.py` import-site artifact node, not the real class.** Its centrality comes from 54 INFERRED `uses` edges, several of which are **not real relationships** (e.g. a `bridge/` node "using" `brain/` internals — those run in separate processes and never link directly). The trace narratives in the exploration session were grounded in *source code* (grep + Read), so their conclusions stand; but the **graph's centrality ranking for imported symbols is partly artifact-driven.**

**This is systemic, not local.** The same `_py_` import-site pattern affects every widely-imported symbol (`MiloSocket`, `ControlBroker`, `GaitEngine`, …). Other "god nodes" in the report may likewise be import-site aggregators rather than definitions.

**Why the cheap fix was rejected (2026-07-19):** Merging the 6 reference nodes into the real class node would dump ~70 mostly-INFERRED, partly-spurious edges onto the currently-clean canonical node — **degrading** it. Deleting the artifact nodes fixes PairedStore but leaves every other imported symbol fragmented — a symptom fix. Neither is safe or consistent as a one-off.

**Correct fix:** a **graph-wide normalization** (collapse every unambiguous `{path}_py_{symbol}` import-site node into its single canonical definition node, re-evaluate INFERRED `uses` edges), OR a **full re-extract** once graphify's extractor is configured/updated to not emit standalone import-site nodes. Both are beyond a "targeted, no-token" pass and were deliberately deferred. **No MILO source change required — this is entirely a knowledge-graph build concern.**

---

## 2. Proposal PDF was not extracted (environment gap)

**What:** `Project-Milo-Proposal.pdf` is represented as a single placeholder `paper` node instead of being fully mined for concepts/citations.

**Cause:** The PDF could not be rendered during extraction because **poppler is not installed** on this machine (graphify's PDF path depends on it).

**Effect:** The project proposal's content contributes almost nothing to the graph. Any question that would rely on the proposal's framing/requirements will miss it.

**Fix:** Install poppler (e.g. via conda/`poppler-utils`, or a Windows poppler build on PATH), then re-run `graphify . --update` so the PDF is re-extracted incrementally.

---

## 3. Two large plan docs extracted only partially (coverage gap)

**What:** During semantic extraction, two files exceeded the single-read cap and were read as **leading portions only**:
- `docs/superpowers/plans/2026-07-17-movement-face-speech-imu-mcp-engine.md` (~3119 lines)
- a second large plan/dashboard doc in the same batch

**Cause:** File length exceeded the subagent's single Read window; extraction captured architecture, goals, and named interfaces from the opening sections but not the full body.

**Effect:** Graph coverage of those two (large, architecturally important) plans is partial. Concepts introduced deep in those documents may be missing edges.

**Fix:** On the next build, split those files or run extraction with a deeper read budget for them specifically. Low urgency — the leading sections carried the load-bearing architecture.

---

## 4. Self-referential "import cycles" reported — VERIFIED BENIGN (no fix)

**What:** graphify's report lists two 1-file import cycles:
- `iot-testing/iot_tester/results_log.py -> iot-testing/iot_tester/results_log.py`
- `bridge/milo_bridge/webapp/api/__init__.py -> bridge/milo_bridge/webapp/api/__init__.py`

**Cause:** These are **self-cycles** (a module "importing itself"), the classic signature of the AST extractor recording an internal self-reference or a package `__init__.py` re-exporting its own submodules.

**Verification (2026-07-19, both files inspected):**
- `results_log.py` — contains **no import of itself**; only stdlib imports (`dataclasses`, `datetime`, `pathlib`). No cycle exists.
- `webapp/api/__init__.py` — imports its **sibling** submodules (`from . import auth, brains, graph, ...`) as a normal route-registry aggregator. It does **not** import the `api` package back into itself. No cycle exists.

**Conclusion:** Confirmed graphify extraction artifact — the extractor maps a relative package import back onto the `__init__.py` node and records a self-edge. **Not a real circular import. No code change required.**

**Effect:** Cosmetic noise in the graph report only.

---

## 5. High-INFERRED-edge hub nodes flagged for verification (data quality)

**What:** 20% of all edges are INFERRED (1180 edges, avg confidence 0.62; 0% AMBIGUOUS). The report's suggested questions call out hub nodes carrying many model-reasoned (not source-explicit) edges:
- `ControlBroker` — 52 INFERRED edges
- `PairedStore` — 54 INFERRED edges
- `MotionService` — 35 INFERRED edges
- `make_deps()` — 2 INFERRED edges

**Cause:** Semantic subagents add INFERRED `conceptually_related_to` / `semantically_similar_to` edges around central abstractions. These are plausible but not guaranteed correct.

**Effect:** Any downstream analysis (community shape, betweenness) partly rests on unverified edges concentrated on the busiest nodes.

**Fix:** Spot-check the INFERRED edges on the top-3 hubs against source before treating community boundaries as authoritative. During this session's trace, the *structural* (EXTRACTED) edges on `ControlBroker`/`PairedStore` all matched the code — it's specifically the INFERRED overlay that's unverified.

> **See §1** — deeper investigation found that several of these INFERRED `uses` edges belong to *import-site artifact nodes*, and that the top "god node" for `PairedStore` was one such artifact. The two findings share a root cause: graphify's handling of imported symbols. A graph-wide normalization or re-extract addresses both at once.

---

## 6. Only 40 of 191 communities are meaningfully labeled (report usability)

**What:** Communities 0–39 got plain-language names during the build; communities 40–190 remain generic `Community N`.

**Cause:** 191 communities is a long tail; hand-labeling was applied to the 40 largest/most-connected, which cover the bulk of the graph's mass. The remainder are small, often single-purpose test/util clusters.

**Effect:** Navigation of the smaller communities in `GRAPH_REPORT.md` / `graph.html` is by node name only.

**Fix:** Optional. Re-run Step 5 labeling over the full set if a fully-named map is wanted; low value given the tail is mostly tiny clusters.

---

## 7. 28 "thin" communities omitted from the report/viz

**What:** The report notes "191 total, 28 thin omitted" — 163 communities shown.

**Cause:** graphify drops very low-cohesion / tiny communities from the rendered output by design.

**Effect:** A handful of loosely-connected nodes won't appear in the community listing (they're still in `graph.json`).

**Fix:** None needed; expected behavior. Query `graph.json` directly if a specific omitted node matters.

---

## 8. Checked-and-cleared: `ControlBroker` heartbeat reclaim is sound (NOT an issue)

**What I initially suspected:** that `ControlBroker.expire()` (the 10s auto-reclaim that hands motion control back to the brain when a web pilot goes silent) might only fire when a WS message happens to arrive — which would leave the robot frozen if the controlling browser died with no other traffic.

**What I found:** **False alarm.** `expire()` is driven by a dedicated background task `_expiry_loop` (`bridge/milo_bridge/webapp/ws.py:240`) that runs on a fixed `EXPIRY_S` timer, independent of client messages. Additionally `release_web(client_id)` is called in the WS handler's `finally` block (`ws.py:218`) on any disconnect. The auto-reclaim is guaranteed. Recorded here so the concern isn't re-raised later.

---

## Architecture observations (context, not defects)

These aren't problems — they're the load-bearing structure the graph surfaced, worth capturing:

- **Two-gate permission model.** The graph's two highest-betweenness nodes are exactly the system's two trust gates:
  - `PairedStore` (`common/milo_common/auth.py`) — *who may connect at all* (persistent HMAC/HKDF pairing tokens; spans both the robot and brain processes because it lives in `common`, imported by 18 files across all three packages).
  - `ControlBroker` (`bridge/milo_bridge/webapp/control.py`) — *who may move the robot right now* (ephemeral, in-memory, web-preemptive, self-healing via heartbeat).
- **Brain suspend is real I/O suspension, not just motion denial.** `net/session.py:_brain_active()` = `broker.allow_brain_motion()`, wired as `should_stream` for `pump_video`/`pump_audio` and as the gate on incoming brain audio. When a web pilot takes control the robot stops streaming camera+mic to the brain and ignores its voice — the cognition session goes dark on I/O.
- **Two enforcement points, one per client type:** web motion is gated by `is_web_controller()` in `webapp/motion.py` + `ws.py`; brain motion is gated by `allow_brain_motion()` in `mcp/server.py:50`.

---

## Suggested follow-ups (priority order)

1. ~~**Graph-wide `_py_` import-site normalization**~~ (§1) — **DONE 2026-07-20.** Collapsed artifact nodes into canonical definitions; god-node/centrality rankings now reflect real code structure; artifact half of §5 resolved.
2. **Install poppler and `--update`** so the proposal PDF joins the graph (§2). Needs an elevated shell: `choco install poppler`.
3. **Spot-check INFERRED edges** on the *definition* nodes of `ControlBroker` / `MotionService` before trusting community boundaries (§5).
4. Optionally split the two oversized plan docs for full extraction (§3).

## Status

**2026-07-20 (normalization pass):**
- **§1** (PairedStore fragmentation) — **RESOLVED.** Graph-wide `_py_` normalization applied (9 symbols merged, 32 artifact nodes removed, god-node ranking corrected). Graph/report/HTML regenerated and integrity-verified (0 dangling edges). See §1 resolution note.
- **§6** (community labels) — **DONE as a side effect.** The re-cluster after normalization was hand-labeled: **45 significant communities named** (of 192); the long tail stays `Community N` by design.
- **§5** — the artifact half is now moot (import-site nodes gone). The remaining item is spot-checking genuinely-INFERRED edges on real definition nodes; still advisory, not applied.

**2026-07-19 (initial investigation pass):**
- **§4** (import cycles) — investigated, **verified benign**, doc corrected. No code change (correct outcome).
- **§2, §3** — require poppler + re-extraction; out of scope for a no-token pass. Still open.

**Net:** no MILO **source code** needed changing — none of the findings were code defects. The knowledge-graph build issues (§1, §4, §6) are resolved; §2/§3 remain as documented environment/coverage caveats.
