"""Extract graph-memory structures from a stored memory document."""

from __future__ import annotations

import hashlib
from typing import Any

from ..models.graph_models import ExtractedGraph, GraphEdge, GraphEntry, GraphNode
from .entity_resolver import EntityResolver


class GraphExtractor:
    """Turn memory summaries into nodes, edges, and searchable graph entries."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.max_topics = int(self.config.get("graph_max_topics", 6))
        self.max_participants = int(self.config.get("graph_max_participants", 8))
        self.max_facts = int(self.config.get("graph_max_facts", 8))

    def extract(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> ExtractedGraph:
        """Build a graph snapshot from one memory document."""
        metadata = metadata or {}
        graph = ExtractedGraph()

        session_id = metadata.get("session_id")
        persona_id = metadata.get("persona_id")
        summary = metadata.get("canonical_summary") or content

        topics = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("topics", []) if item]
        )[: self.max_topics]
        participants = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("participants", []) if item]
        )[: self.max_participants]
        key_facts = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("key_facts", []) if item]
        )[: self.max_facts]

        if not key_facts and summary:
            key_facts = [summary]

        node_map: dict[str, GraphNode] = {}

        def add_node(
            node_type: str, value: str, extra: dict[str, Any] | None = None
        ) -> str:
            canonical_value = EntityResolver.canonicalize(value)
            if not canonical_value:
                return ""
            node = GraphNode(
                node_type=node_type,
                value=value.strip(),
                canonical_value=canonical_value,
                metadata=extra or {},
            )
            node_map[node.node_key] = node
            return node.node_key

        topic_keys = [add_node("topic", topic) for topic in topics]
        participant_keys = [
            add_node("person", participant) for participant in participants
        ]
        fact_keys = [add_node("fact", fact, {"summary": summary}) for fact in key_facts]

        topic_keys = [item for item in topic_keys if item]
        participant_keys = [item for item in participant_keys if item]
        fact_keys = [item for item in fact_keys if item]

        graph.nodes.extend(node_map.values())

        def add_entry(
            entry_type: str,
            content_text: str,
            node_keys: list[str],
            relation_type: str | None = None,
            confidence: float = 0.8,
        ) -> None:
            payload = (
                f"{entry_type}|{source_memory_id}|{relation_type or ''}|"
                f"{'|'.join(node_keys)}|{content_text}"
            )
            entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            entry_metadata = {
                "source_memory_id": source_memory_id,
                "session_id": session_id,
                "persona_id": persona_id,
                "importance": metadata.get("importance", 0.5),
                "create_time": metadata.get("create_time"),
                "last_access_time": metadata.get("last_access_time"),
                "canonical_summary": summary,
                "summary_schema_version": metadata.get("summary_schema_version"),
                "graph_confidence": confidence,
                "source_window": metadata.get("source_window"),
            }
            graph.entries.append(
                GraphEntry(
                    entry_key=entry_key,
                    source_memory_id=source_memory_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    entry_type=entry_type,
                    content=content_text,
                    metadata=entry_metadata,
                    node_keys=node_keys,
                    relation_type=relation_type,
                )
            )

        for fact_key in fact_keys:
            fact_value = node_map[fact_key].value
            add_entry(
                "fact",
                f"Fact: {fact_value}. Summary: {summary}",
                [fact_key],
                relation_type="fact",
                confidence=0.9,
            )

        for topic_key in topic_keys:
            topic_value = node_map[topic_key].value
            add_entry(
                "topic",
                f"Topic: {topic_value}. Summary: {summary}",
                [topic_key],
                relation_type="topic",
                confidence=0.75,
            )

        for person_key in participant_keys:
            person_value = node_map[person_key].value
            add_entry(
                "participant",
                f"Participant: {person_value}. Summary: {summary}",
                [person_key],
                relation_type="participant",
                confidence=0.7,
            )

        for topic_key in topic_keys:
            for fact_key in fact_keys:
                graph.edges.append(
                    GraphEdge(
                        source_key=topic_key,
                        target_key=fact_key,
                        relation_type="describes",
                        source_memory_id=source_memory_id,
                        confidence=0.82,
                        metadata={"summary": summary},
                    )
                )
                add_entry(
                    "edge",
                    (
                        f"Topic {node_map[topic_key].value} describes "
                        f"fact {node_map[fact_key].value}. Summary: {summary}"
                    ),
                    [topic_key, fact_key],
                    relation_type="describes",
                    confidence=0.82,
                )

        for person_key in participant_keys:
            for fact_key in fact_keys:
                graph.edges.append(
                    GraphEdge(
                        source_key=person_key,
                        target_key=fact_key,
                        relation_type="mentioned_in",
                        source_memory_id=source_memory_id,
                        confidence=0.88,
                        metadata={"summary": summary},
                    )
                )
                add_entry(
                    "edge",
                    (
                        f"Participant {node_map[person_key].value} is linked to "
                        f"fact {node_map[fact_key].value}. Summary: {summary}"
                    ),
                    [person_key, fact_key],
                    relation_type="mentioned_in",
                    confidence=0.88,
                )

        for index, first_key in enumerate(participant_keys):
            for second_key in participant_keys[index + 1 :]:
                graph.edges.append(
                    GraphEdge(
                        source_key=first_key,
                        target_key=second_key,
                        relation_type="co_occurs_with",
                        source_memory_id=source_memory_id,
                        confidence=0.7,
                        metadata={"summary": summary},
                    )
                )
                add_entry(
                    "edge",
                    (
                        f"Participant {node_map[first_key].value} co-occurs with "
                        f"participant {node_map[second_key].value}. Summary: {summary}"
                    ),
                    [first_key, second_key],
                    relation_type="co_occurs_with",
                    confidence=0.7,
                )

        if not graph.entries and summary:
            summary_key = add_node("summary", summary)
            if summary_key:
                graph.nodes = list(node_map.values())
                add_entry(
                    "summary",
                    f"Summary: {summary}",
                    [summary_key],
                    relation_type="summary",
                    confidence=0.6,
                )

        return graph


__all__ = ["GraphExtractor"]
