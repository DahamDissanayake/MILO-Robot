from .client_helpers import authed_client
from .fakes import make_deps


_client = authed_client


def _seed(store):
    alice = store.upsert_node("person", {"name": "Alice", "likes": "tennis"})
    bob = store.upsert_node("person", {"name": "Bob"})
    ball = store.upsert_node("object", {"name": "red ball"})
    store.upsert_edge(alice.id, ball.id, "owns")
    store.upsert_edge(alice.id, bob.id, "knows")
    return alice, bob, ball


async def test_search_matches_props_and_includes_edges():
    deps = make_deps()
    alice, bob, ball = _seed(deps.graph_store)
    res = deps.graph_store.search_text("alice")
    ids = {n["id"] for n in res["nodes"]}
    assert alice.id in ids and bob.id not in ids
    res2 = deps.graph_store.search_text("person")
    ids2 = {n["id"] for n in res2["nodes"]}
    assert {alice.id, bob.id} <= ids2
    edge_pairs = {(e["src"], e["dst"]) for e in res2["edges"]}
    assert (alice.id, bob.id) in edge_pairs          # both endpoints matched
    assert (alice.id, ball.id) not in edge_pairs     # ball didn't match


async def test_graph_http_passthrough_and_search():
    deps = make_deps()
    _seed(deps.graph_store)
    client = await _client(deps)
    try:
        resp = await client.post("/api/graph", json={"op": "query", "id": 1})
        data = await resp.json()
        assert "error" not in data
        resp = await client.get("/api/graph/search", params={"q": "tennis"})
        data = await resp.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["props"]["name"] == "Alice"
    finally:
        await client.close()


async def test_poses_and_faces_endpoints():
    client = await _client(make_deps())
    try:
        poses = await (await client.get("/api/poses")).json()
        assert "walk_forward" in poses["poses"] or len(poses["poses"]) > 0
        faces = await (await client.get("/api/faces")).json()
        assert "happy" in faces["faces"]
    finally:
        await client.close()


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


async def test_search_rejects_non_integer_limit():
    client = await _client(make_deps())
    try:
        resp = await client.get("/api/graph/search", params={"limit": "not-a-number"})
        assert resp.status == 400
        data = await resp.json()
        assert "limit" in data["error"]
    finally:
        await client.close()


async def test_search_rejects_negative_limit():
    deps = make_deps()
    _seed(deps.graph_store)
    client = await _client(deps)
    try:
        resp = await client.get("/api/graph/search", params={"limit": "-1"})
        assert resp.status == 400
        data = await resp.json()
        assert "limit" in data["error"]
    finally:
        await client.close()


async def test_search_rejects_limit_above_cap():
    client = await _client(make_deps())
    try:
        resp = await client.get("/api/graph/search", params={"limit": "100000"})
        assert resp.status == 400
    finally:
        await client.close()


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
