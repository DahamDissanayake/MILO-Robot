import numpy as np
import pytest

from milo_bridge.graph.api import GraphApi, decode_embedding, encode_embedding
from milo_bridge.graph.store import EMBEDDING_DIM, GraphStore


@pytest.fixture()
def store():
    s = GraphStore(":memory:")
    yield s
    s.close()


def embedding(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=EMBEDDING_DIM).astype(np.float32)


# --- store ------------------------------------------------------------------

def test_node_crud_and_prop_merge(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    assert daham.id and daham.type == "person"
    updated = store.upsert_node("person", {"likes": "robots"}, node_id=daham.id)
    assert updated.props == {"name": "Daham", "likes": "robots"}
    assert store.get_node(daham.id).props["name"] == "Daham"


def test_invalid_node_type_rejected(store):
    with pytest.raises(ValueError):
        store.upsert_node("spaceship", {})
    with pytest.raises(KeyError):
        store.upsert_node("person", {}, node_id=999)


def test_query_by_type_text_and_prop(store):
    store.upsert_node("person", {"name": "Daham"})
    store.upsert_node("person", {"name": "Amma"})
    store.upsert_node("fact", {"text": "Daham studies engineering"})
    assert len(store.query(type="person")) == 2
    assert len(store.query(text="Daham")) == 2
    assert store.query(type="person", prop="name", value="Amma")[0].props["name"] == "Amma"


def test_edges_and_neighbors(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    fact = store.upsert_node("fact", {"text": "likes robots"})
    store.upsert_edge(daham.id, fact.id, "said")
    neighbors = store.neighbors(daham.id)
    assert len(neighbors) == 1
    assert neighbors[0]["edge"]["type"] == "said"
    assert neighbors[0]["node"]["props"]["text"] == "likes robots"
    with pytest.raises(KeyError):
        store.upsert_edge(daham.id, 999, "knows")


def test_recent_events_ordering(store):
    for i in range(3):
        store.upsert_node("event", {"n": i})
    events = store.recent_events(limit=2)
    assert len(events) == 2
    assert events[0].props["n"] == 2  # newest first


def test_match_face_finds_same_person(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    other = store.upsert_node("person", {"name": "Amma"})
    store.add_face_embedding(daham.id, embedding(1))
    store.add_face_embedding(other.id, embedding(2))

    # Same face + small noise -> matches Daham with high similarity.
    noisy = embedding(1) + 0.05 * embedding(99)
    match = store.match_face(noisy)
    assert match is not None
    node, similarity = match
    assert node.props["name"] == "Daham"
    assert similarity > 0.9


def test_match_face_unknown_below_threshold(store):
    daham = store.upsert_node("person", {"name": "Daham"})
    store.add_face_embedding(daham.id, embedding(1))
    assert store.match_face(embedding(42)) is None      # unrelated face
    assert store.match_face(np.zeros(EMBEDDING_DIM)) is None  # degenerate


def test_face_embedding_requires_person(store):
    fact = store.upsert_node("fact", {"text": "x"})
    with pytest.raises(KeyError):
        store.add_face_embedding(fact.id, embedding(1))


def test_backup_creates_readable_copy(store, tmp_path):
    store.upsert_node("person", {"name": "Daham"})
    dest = store.backup(tmp_path)
    assert dest.exists()
    copy = GraphStore(dest)
    assert len(copy.query(type="person")) == 1
    copy.close()


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "graph.db"
    s1 = GraphStore(path)
    s1.upsert_node("person", {"name": "Daham"})
    s1.close()
    s2 = GraphStore(path)  # the portability promise: memory survives restarts
    assert s2.query(type="person")[0].props["name"] == "Daham"
    s2.close()


# --- wire API -----------------------------------------------------------------

def test_api_upsert_query_roundtrip(store):
    api = GraphApi(store)
    created = api.handle({"id": 1, "op": "upsert_node", "type": "person",
                          "props": {"name": "Daham"}, "embedding": encode_embedding(embedding(1))})
    assert created["id"] == 1 and created["node"]["props"]["name"] == "Daham"

    result = api.handle({"id": 2, "op": "match_face", "embedding": encode_embedding(embedding(1))})
    assert result["match"]["id"] == created["node"]["id"]
    assert result["similarity"] > 0.99

    edge = api.handle({"id": 3, "op": "upsert_edge", "src": created["node"]["id"],
                       "dst": created["node"]["id"], "type": "knows"})
    assert edge["edge"]["type"] == "knows"

    nb = api.handle({"id": 4, "op": "neighbors", "node_id": created["node"]["id"]})
    assert len(nb["neighbors"]) == 1


def test_api_errors_are_returned_not_raised(store):
    api = GraphApi(store)
    result = api.handle({"id": 9, "op": "explode"})
    assert result["id"] == 9 and "unknown graph op" in result["error"]
    result = api.handle({"id": 10, "op": "upsert_node", "type": "alien", "props": {}})
    assert "error" in result


def test_embedding_codec_roundtrip():
    vec = embedding(7)
    assert np.allclose(decode_embedding(encode_embedding(vec)), vec)
