"""Graph ops over the wire: ``{"t":"graph", "op":..., "id":...}`` -> result dict.

Runs inside the already-authenticated robot session; embeddings travel as
base64 float32 so everything stays in JSON frames.
"""

from __future__ import annotations

import base64
import logging

import numpy as np

from .store import GraphStore

log = logging.getLogger(__name__)


def encode_embedding(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode()


def decode_embedding(data: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(data), dtype=np.float32)


class GraphApi:
    def __init__(self, store: GraphStore):
        self._store = store

    def handle(self, request: dict) -> dict:
        """Never raises: errors come back as ``{"id":..., "error":...}``."""
        req_id = request.get("id")
        op = request.get("op")
        try:
            handler = getattr(self, f"_op_{op}", None)
            if handler is None:
                raise ValueError(f"unknown graph op {op!r}")
            result = handler(request)
        except Exception as exc:
            log.warning("graph op %r failed: %s", op, exc)
            return {"id": req_id, "error": f"{type(exc).__name__}: {exc}"}
        return {"id": req_id, **result}

    def _op_upsert_node(self, req: dict) -> dict:
        node = self._store.upsert_node(req["type"], req.get("props", {}), req.get("node_id"))
        if "embedding" in req:
            self._store.add_face_embedding(node.id, decode_embedding(req["embedding"]))
        return {"node": node.to_dict()}

    def _op_upsert_edge(self, req: dict) -> dict:
        edge = self._store.upsert_edge(req["src"], req["dst"], req["type"], req.get("props"))
        return {"edge": edge.to_dict()}

    def _op_query(self, req: dict) -> dict:
        nodes = self._store.query(
            type=req.get("type"), prop=req.get("prop"), value=req.get("value"),
            text=req.get("text"), limit=req.get("limit", 50),
        )
        return {"nodes": [n.to_dict() for n in nodes]}

    def _op_neighbors(self, req: dict) -> dict:
        return {"neighbors": self._store.neighbors(req["node_id"], limit=req.get("limit", 50))}

    def _op_recent_events(self, req: dict) -> dict:
        return {"nodes": [n.to_dict() for n in self._store.recent_events(req.get("limit", 20))]}

    def _op_add_face(self, req: dict) -> dict:
        self._store.add_face_embedding(req["node_id"], decode_embedding(req["embedding"]))
        return {"ok": True}

    def _op_match_face(self, req: dict) -> dict:
        match = self._store.match_face(
            decode_embedding(req["embedding"]), threshold=req.get("threshold", 0.45)
        )
        if match is None:
            return {"match": None}
        node, similarity = match
        return {"match": node.to_dict(), "similarity": similarity}
