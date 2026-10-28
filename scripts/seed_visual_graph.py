"""Add an isolated, archived visual fixture through the existing stores."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

DOMAINS = [
    "星海旅行",
    "家庭日常",
    "摄影创作",
    "阅读计划",
    "软件项目",
    "音乐回忆",
    "城市生活",
    "运动时光",
    "料理实验",
    "朋友相聚",
    "语言学习",
    "自然观察",
]
WEIGHTS = [18, 16, 14, 12, 10, 8, 6.5, 5, 4, 3, 2.5, 1]
NAMES = ["林夏", "周野", "陈舟", "苏晴", "许星", "江月", "沈言", "顾川", "叶青", "陆遥"]
TOPICS = [
    "共同计划",
    "难忘瞬间",
    "日常偏好",
    "灵感笔记",
    "周末约定",
    "成长记录",
    "伙伴故事",
    "收藏清单",
]


async def seed_visual_graph(
    data_dir: Path,
    *,
    nodes: int = 10000,
    edges: int = 200000,
    memories: int = 1200,
    communities: int = 12,
    session_id: str = "livingmemory:visual:galaxy",
) -> dict[str, Any]:
    """Append synthetic graph data without indexing it for normal recall.

    Args:
        data_dir: Existing plugin data directory containing livingmemory.db.
        nodes: Number of graph nodes, including a small isolated population.
        edges: Number of distinct directed relations.
        memories: Number of archived, readable memory documents.
        communities: Number of thematic groups, from one to twelve.
        session_id: Scope used to select the fixture in the dashboard.

    Returns:
        Inserted sizes, fixture namespace, session and memory ID range.
    """
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root.parent), str(root.parents[2])]
    from astrbot_plugin_livingmemory.core.models.graph_models import (
        GraphEdge,
        GraphEntry,
        GraphNode,
    )
    from astrbot_plugin_livingmemory.storage.graph_store import GraphStore

    from astrbot.core.db.vec_db.faiss_impl.document_storage import DocumentStorage

    path = (data_dir / "livingmemory.db").resolve(strict=True)
    if not 1 <= communities <= len(DOMAINS) or min(nodes, edges, memories) <= 0:
        raise ValueError("Use positive sizes and one to twelve communities")
    rng = random.Random(42)
    run = uuid4().hex
    now = time.time()
    stamp = datetime.now(timezone.utc).isoformat()
    isolated = min(40, max(1, nodes // 250))
    connected = nodes - isolated
    weights = WEIGHTS[:communities]
    total_weight = sum(weights)
    graph_nodes, graph_edges, groups = [], [], []
    texts, metadatas, owners = [], [], []
    associations: list[set[str]] = [set() for _ in range(memories)]
    cumulative = 0.0
    assigned_nodes = assigned_memories = 0

    for group, weight in enumerate(weights):
        cumulative += weight
        end_node = round(connected * cumulative / total_weight)
        end_memory = round(memories * cumulative / total_weight)
        size, memory_count = end_node - assigned_nodes, end_memory - assigned_memories
        people, topics = max(2, size // 40), max(4, size // 14)
        if memory_count < 1 or size <= people + topics + memory_count:
            raise ValueError("Increase nodes or reduce memories/communities")
        domain = DOMAINS[group]
        start = len(graph_nodes)
        for local in range(size):
            if local < people:
                kind, label = (
                    "person",
                    f"{NAMES[local % len(NAMES)]}·{domain}{local + 1:02}",
                )
            elif local < people + topics:
                kind, label = (
                    "topic",
                    f"{domain}·{TOPICS[local % len(TOPICS)]}{local - people + 1:02}",
                )
            elif local < people + topics + memory_count:
                kind, label = (
                    "summary",
                    f"{domain}·记忆{local - people - topics + 1:03}",
                )
            else:
                kind, label = (
                    "fact",
                    f"{domain}·{TOPICS[local % len(TOPICS)]}片段{local:04}",
                )
            owner = assigned_memories + ((local - people - topics) % memory_count)
            atom_type = (
                "preference"
                if local % 7 == 0
                else "relational"
                if local % 11 == 0
                else "factual"
            )
            node = GraphNode(
                kind,
                label,
                f"{label} [visual/{run}/{len(graph_nodes)}]",
                {"visual_fixture": run, "atom_type": atom_type},
            )
            graph_nodes.append(node)
            owners.append(owner)
            associations[owner].add(node.node_key)
        for local in range(memory_count):
            person = graph_nodes[start + local % people].value
            topic = graph_nodes[start + people + local % topics].value
            text = f"【视觉测试】{domain}第{local + 1}段：{person}围绕{topic}分享了近况、偏好与计划，留下了一段值得再次回顾的共同记忆。"
            importance = (
                0.95
                if local % 19 == 0
                else 0.84
                if local % 11 == 0
                else 0.35 + (local % 9) * 0.05
            )
            texts.append(text)
            metadatas.append(
                {
                    "session_id": session_id,
                    "persona_id": "visual-test",
                    "status": "archived",
                    "archived_at": stamp,
                    "importance": importance,
                    "create_time": now,
                    "last_access_time": now,
                    "memory_type": "GENERAL",
                    "visual_fixture": run,
                    "canonical_summary": text,
                    "topics": [domain, topic],
                    "key_facts": [
                        f"{person}参与了{domain}的交流。",
                        f"大家约定继续整理{topic}的相关记录。",
                    ],
                }
            )
        groups.append((start, size, people, topics, assigned_memories, memory_count))
        assigned_nodes, assigned_memories = end_node, end_memory

    for local in range(isolated):
        node = GraphNode(
            "fact",
            f"独立片段 {local + 1:02}",
            f"独立片段 [visual/{run}/orphan/{local}]",
            {"visual_fixture": run, "atom_type": "factual"},
        )
        graph_nodes.append(node)
        owners.append(memories - 1)
        associations[-1].add(node.node_key)

    def relation(
        source: int, target: int, kind: str, owner: int, weight: float
    ) -> None:
        a, b = graph_nodes[source].node_key, graph_nodes[target].node_key
        graph_edges.append(
            GraphEdge(
                a,
                b,
                kind,
                owner,
                weight=weight,
                status="archived",
                metadata={"visual_fixture": run},
            )
        )
        associations[owner].update((a, b))

    membership = []
    for group, (start, size, people, topics, memory_start, memory_count) in enumerate(
        groups
    ):
        membership.extend([group] * size)
        for local in range(1, size):
            source = start + local
            if local < people:
                target, kind = start, "knows"
            elif local < people + topics:
                target, kind = start + local % people, "about"
            elif local < people + topics + memory_count:
                target, kind = (
                    start + people + (local - people - topics) % topics,
                    "summarizes",
                )
            elif graph_nodes[source].metadata["atom_type"] in {
                "preference",
                "relational",
            }:
                target, kind = start + local % people, "prefers"
            else:
                target, kind = (
                    start
                    + people
                    + topics
                    + (local - people - topics - memory_count) % memory_count,
                    "records",
                )
            relation(source, target, kind, owners[source], 3.5)

    cross = edges // 100 if communities > 1 else 0
    remaining = edges - len(graph_edges) - cross
    if remaining < 0:
        raise ValueError("Edges must cover the semantic branch structure")
    degree, extra = divmod(remaining, connected)
    for start, size, _, _, _, _ in groups:
        for local in range(size):
            source = start + local
            targets = rng.sample(range(size - 1), degree + int(source < extra))
            for target in targets:
                relation(
                    source,
                    start + target + int(target >= local),
                    "related",
                    owners[source],
                    0.6 + rng.random() * 0.4,
                )
    for index in range(cross):
        source = index * 37 % connected
        target_group = (
            membership[source] + 1 + index % (communities - 1)
        ) % communities
        start, size, _, _, _, _ = groups[target_group]
        relation(
            source, start + (index * 53 + 17) % size, "related", owners[source], 0.12
        )

    documents = DocumentStorage(str(path))
    store = GraphStore(str(path))
    await documents.connect()
    try:
        memory_ids = await documents.insert_documents_batch(
            [f"visual/{run}/{i}" for i in range(memories)], texts, metadatas
        )
        print(
            json.dumps(
                {
                    "stage": "documents",
                    "inserted": len(memory_ids),
                    "session_id": session_id,
                }
            ),
            flush=True,
        )
        await store.initialize()
        node_ids = {}
        for start in range(0, len(graph_nodes), 1000):
            node_ids.update(await store.upsert_nodes(graph_nodes[start : start + 1000]))
        print(json.dumps({"stage": "nodes", "inserted": len(node_ids)}), flush=True)
        edge_ids = {}
        for edge in graph_edges:
            edge.source_memory_id = memory_ids[edge.source_memory_id]
        for start in range(0, len(graph_edges), 5000):
            edge_ids.update(
                await store.add_edges(graph_edges[start : start + 5000], node_ids)
            )
            if start % 25000 == 0:
                print(
                    json.dumps(
                        {
                            "stage": "edges",
                            "written": min(start + 5000, len(graph_edges)),
                            "total": len(graph_edges),
                        }
                    ),
                    flush=True,
                )
        entries = [
            GraphEntry(
                f"visual/{run}/{i}",
                memory_ids[i],
                session_id,
                "visual-test",
                "summary",
                texts[i],
                metadatas[i],
                sorted(associations[i]),
            )
            for i in range(memories)
        ]
        await store.add_entries(entries, node_ids, edge_ids)
    finally:
        await documents.engine.dispose()
        await store.close()
    return {
        "nodes": len(graph_nodes),
        "edges": len(graph_edges),
        "memories": len(memory_ids),
        "session_id": session_id,
        "run_id": run,
        "memory_ids": [min(memory_ids), max(memory_ids)],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--nodes", type=int, default=10000)
    parser.add_argument("--edges", type=int, default=200000)
    parser.add_argument("--memories", type=int, default=1200)
    parser.add_argument("--communities", type=int, default=12)
    parser.add_argument("--session-id", default="livingmemory:visual:galaxy")
    args = parser.parse_args()
    result = asyncio.run(
        seed_visual_graph(
            args.data_dir,
            nodes=args.nodes,
            edges=args.edges,
            memories=args.memories,
            communities=args.communities,
            session_id=args.session_id,
        )
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
