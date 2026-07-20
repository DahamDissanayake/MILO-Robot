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
