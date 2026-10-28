"""Weighted community metadata used by the 3D dashboard."""

from astrbot_plugin_livingmemory.core.page_api_modules.graph_handler import GraphHandler


def test_weighted_communities_follow_dense_groups_without_losing_relations():
    snapshot = {
        "nodes": [{"id": i} for i in range(8)],
        "edges": [
            {"source": start + a, "target": start + b, "weight": 5}
            for start in (0, 4)
            for a in range(4)
            for b in range(a + 1, 4)
        ]
        + [{"source": 3, "target": 4, "weight": 0.1}],
    }
    original_edges = list(snapshot["edges"])
    GraphHandler._assign_communities(snapshot)
    groups = [node["community"] for node in snapshot["nodes"]]
    assert len(set(groups[:4])) == len(set(groups[4:])) == 1
    assert groups[0] != groups[4]
    assert snapshot["edges"] == original_edges


def test_parallel_and_reverse_ties_share_their_total_community_weight():
    snapshot = {
        "nodes": [{"id": i} for i in range(4)],
        "edges": [
            {"source": 0, "target": 1, "weight": 5},
            {"source": 2, "target": 3, "weight": 5},
            {"source": 1, "target": 2, "weight": 0.1},
            {"source": 1, "target": 0, "weight": 7},
        ],
    }
    GraphHandler._assign_communities(snapshot)
    assert [node["community"] for node in snapshot["nodes"]] == [0, 0, 1, 1]


def test_isolates_share_one_display_cluster_and_self_loops_do_not_split_it():
    snapshot = {
        "nodes": [{"id": i} for i in range(6)],
        "edges": [
            {"source": 0, "target": 1, "weight": 2},
            {"source": 2, "target": 2, "weight": 100},
        ],
    }
    GraphHandler._assign_communities(snapshot)
    groups = [node["community"] for node in snapshot["nodes"]]
    assert groups[0] == groups[1]
    assert len(set(groups[2:])) == 1
    assert groups[0] != groups[2]


def test_a_graph_without_relations_has_one_compact_display_cluster():
    snapshot = {"nodes": [{"id": 9}, {"id": 2}], "edges": []}
    GraphHandler._assign_communities(snapshot)
    assert [node["community"] for node in snapshot["nodes"]] == [0, 0]


def test_zero_weight_relations_remain_evidence_without_creating_attraction():
    snapshot = {
        "nodes": [{"id": 1}, {"id": 2}],
        "edges": [{"source": 1, "target": 2, "weight": 0}],
    }
    GraphHandler._assign_communities(snapshot)
    assert [node["community"] for node in snapshot["nodes"]] == [0, 0]
    assert len(snapshot["edges"]) == 1
