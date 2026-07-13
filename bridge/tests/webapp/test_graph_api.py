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
