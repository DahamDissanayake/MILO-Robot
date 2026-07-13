"""Milo's memory: a SQLite property graph, living only on the Pi.

Nodes are typed (person/place/object/event/fact) with JSON properties; edges
are typed and timestamped. Person nodes carry InsightFace embeddings for
``match_face``. Brains extract; the Pi stores, indexes, serves.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

NODE_TYPES = frozenset({"person", "place", "object", "event", "fact"})
EMBEDDING_DIM = 512
DEFAULT_MATCH_THRESHOLD = 0.45

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,
  props TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY,
  src INTEGER NOT NULL REFERENCES nodes(id),
  dst INTEGER NOT NULL REFERENCES nodes(id),
  type TEXT NOT NULL,
  props TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS face_embeddings (
  node_id INTEGER NOT NULL REFERENCES nodes(id),
  embedding BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Node:
    id: int
    type: str
    props: dict
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "props": self.props,
                "created_at": self.created_at, "updated_at": self.updated_at}


@dataclass(frozen=True)
class Edge:
    id: int
    src: int
    dst: int
    type: str
    props: dict
    created_at: str

    def to_dict(self) -> dict:
        return {"id": self.id, "src": self.src, "dst": self.dst, "type": self.type,
                "props": self.props, "created_at": self.created_at}


class GraphStore:
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(SCHEMA)
        self._db.commit()

    # -- nodes ---------------------------------------------------------------
    def upsert_node(self, type: str, props: dict, node_id: int | None = None) -> Node:
        if type not in NODE_TYPES:
            raise ValueError(f"unknown node type {type!r}")
        now = _now()
        if node_id is None:
            cur = self._db.execute(
                "INSERT INTO nodes (type, props, created_at, updated_at) VALUES (?,?,?,?)",
                (type, json.dumps(props), now, now),
            )
            self._db.commit()
            return Node(cur.lastrowid, type, props, now, now)
        existing = self.get_node(node_id)
        if existing is None:
            raise KeyError(f"node {node_id} does not exist")
        merged = {**existing.props, **props}
        self._db.execute(
            "UPDATE nodes SET props=?, updated_at=? WHERE id=?",
            (json.dumps(merged), now, node_id),
        )
        self._db.commit()
        return Node(node_id, existing.type, merged, existing.created_at, now)

    def get_node(self, node_id: int) -> Node | None:
        row = self._db.execute(
            "SELECT id, type, props, created_at, updated_at FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        return Node(row[0], row[1], json.loads(row[2]), row[3], row[4]) if row else None

    def query(
        self, type: str | None = None, prop: str | None = None,
        value=None, text: str | None = None, limit: int = 50,
    ) -> list[Node]:
        sql = "SELECT id, type, props, created_at, updated_at FROM nodes"
        clauses, params = [], []
        if type is not None:
            clauses.append("type=?")
            params.append(type)
        if text is not None:
            clauses.append("props LIKE ?")
            params.append(f"%{text}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        nodes = [
            Node(r[0], r[1], json.loads(r[2]), r[3], r[4])
            for r in self._db.execute(sql, params)
        ]
        if prop is not None:
            nodes = [n for n in nodes if n.props.get(prop) == value]
        return nodes

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

    # -- edges ---------------------------------------------------------------
    def upsert_edge(self, src: int, dst: int, type: str, props: dict | None = None) -> Edge:
        for node_id in (src, dst):
            if self.get_node(node_id) is None:
                raise KeyError(f"node {node_id} does not exist")
        now = _now()
        cur = self._db.execute(
            "INSERT INTO edges (src, dst, type, props, created_at) VALUES (?,?,?,?,?)",
            (src, dst, type, json.dumps(props or {}), now),
        )
        self._db.commit()
        return Edge(cur.lastrowid, src, dst, type, props or {}, now)

    def neighbors(self, node_id: int, limit: int = 50) -> list[dict]:
        """Edges touching the node, each with the far node inlined."""
        rows = self._db.execute(
            "SELECT id, src, dst, type, props, created_at FROM edges"
            " WHERE src=? OR dst=? ORDER BY created_at DESC LIMIT ?",
            (node_id, node_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            edge = Edge(r[0], r[1], r[2], r[3], json.loads(r[4]), r[5])
            other = self.get_node(edge.dst if edge.src == node_id else edge.src)
            out.append({"edge": edge.to_dict(), "node": other.to_dict() if other else None})
        return out

    def recent_events(self, limit: int = 20) -> list[Node]:
        return self.query(type="event", limit=limit)

    # -- faces ---------------------------------------------------------------
    def add_face_embedding(self, node_id: int, embedding: np.ndarray) -> None:
        node = self.get_node(node_id)
        if node is None or node.type != "person":
            raise KeyError(f"node {node_id} is not a person")
        vec = np.asarray(embedding, dtype=np.float32).reshape(EMBEDDING_DIM)
        self._db.execute(
            "INSERT INTO face_embeddings (node_id, embedding, created_at) VALUES (?,?,?)",
            (node_id, vec.tobytes(), _now()),
        )
        self._db.commit()

    def match_face(
        self, embedding: np.ndarray, threshold: float = DEFAULT_MATCH_THRESHOLD
    ) -> tuple[Node, float] | None:
        """Brute-force cosine over all stored embeddings — fine for hundreds
        of people on the Pi. Returns (person, similarity) or None."""
        query = np.asarray(embedding, dtype=np.float32).reshape(EMBEDDING_DIM)
        qn = np.linalg.norm(query)
        if qn == 0:
            return None
        best_id, best_sim = None, threshold
        for node_id, blob in self._db.execute("SELECT node_id, embedding FROM face_embeddings"):
            stored = np.frombuffer(blob, dtype=np.float32)
            denom = qn * np.linalg.norm(stored)
            if denom == 0:
                continue
            sim = float(np.dot(query, stored) / denom)
            if sim >= best_sim:
                best_id, best_sim = node_id, sim
        if best_id is None:
            return None
        return self.get_node(best_id), best_sim

    # -- maintenance -----------------------------------------------------------
    def backup(self, dest_dir: Path | str) -> Path:
        """Consistent online backup to a timestamped file (nightly job)."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = dest_dir / f"graph-{stamp}.db"
        target = sqlite3.connect(dest)
        with target:
            self._db.backup(target)
        target.close()
        return dest

    def close(self) -> None:
        self._db.close()
