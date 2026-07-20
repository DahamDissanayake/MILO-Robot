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
