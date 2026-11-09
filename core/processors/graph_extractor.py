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
        atoms: list | None = None,
    ) -> ExtractedGraph:
        """Build a graph snapshot from one memory document.

        When atoms are provided, each atom independently contributes nodes
        and edges with per-atom confidence scores instead of hardcoded values.
        """
        graph = (
            self._extract_from_atoms(source_memory_id, atoms, metadata)
            if atoms
            else self._extract_legacy(source_memory_id, content, metadata)
        )
        for entry in graph.entries:
            entry.metadata["adapter_id"] = (metadata or {}).get("adapter_id")
        return graph

    def _participant_nodes(
        self, metadata: dict[str, Any]
    ) -> list[tuple[str, str, dict[str, Any]]]:
        """Return display name, canonical identity, and metadata for people."""
        resolved: list[tuple[str, str, dict[str, Any]]] = []
        identities = metadata.get("participant_identities")
        if isinstance(identities, list):
            seen: set[str] = set()
            for item in identities:
                if not isinstance(item, dict):
                    continue
                identity_key = EntityResolver.canonicalize(
                    str(item.get("identity_key") or "")
                )
                display_name = str(
                    item.get("display_name") or item.get("sender_id") or ""
                ).strip()
                if not identity_key or not display_name or identity_key in seen:
                    continue
                seen.add(identity_key)
                aliases = EntityResolver.dedupe_preserve_order(
                    [str(alias) for alias in item.get("aliases", []) if alias]
                )
                resolved.append(
                    (
                        display_name,
                        f"account:{identity_key}",
                        {
                            "identity_key": identity_key,
                            "sender_id": str(item.get("sender_id") or ""),
                            "platform": str(item.get("platform") or "unknown"),
                            "aliases": aliases,
                            "is_bot": bool(item.get("is_bot", False)),
                        },
                    )
                )
                if len(resolved) >= self.max_participants:
                    break
        if resolved:
            return resolved

        participants = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("participants", []) if item]
        )[: self.max_participants]
        return [
            (participant, EntityResolver.canonicalize(participant), {})
            for participant in participants
        ]

    def _extract_legacy(
        self,
        source_memory_id: int,
        content: str,
        metadata: dict[str, Any] | None,
    ) -> ExtractedGraph:
        """Original graph extraction from metadata (backward-compatible path)."""
        metadata = metadata or {}
        graph = ExtractedGraph()
        summary = metadata.get("canonical_summary") or content

        node_map, topic_keys, participant_keys, fact_keys = self._build_legacy_nodes(
            metadata, content
        )
        graph.nodes.extend(node_map.values())

        self._add_legacy_node_entries(
            graph,
            source_memory_id,
            metadata,
            summary,
            node_map,
            topic_keys,
            participant_keys,
            fact_keys,
        )
        self._add_legacy_edges(
            graph,
            source_memory_id,
            metadata,
            summary,
            node_map,
            topic_keys,
            participant_keys,
            fact_keys,
        )

        if not graph.entries and summary:
            summary_key = self._legacy_node(node_map, "summary", summary)
            if summary_key:
                graph.nodes = list(node_map.values())
                self._add_legacy_entry(
                    graph,
                    source_memory_id,
                    metadata,
                    summary,
                    "summary",
                    f"Summary: {summary}",
                    [summary_key],
                    relation_type="summary",
                    confidence=0.6,
                )

        return graph

    def _build_legacy_nodes(
        self, metadata: dict[str, Any], content: str
    ) -> tuple[
        dict[str, GraphNode],
        list[str],
        list[str],
        list[str],
    ]:
        """创建 legacy 抽取的节点，返回节点表与各类节点 key。"""
        summary = metadata.get("canonical_summary") or content
        topics = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("topics", []) if item]
        )[: self.max_topics]
        participants = self._participant_nodes(metadata)
        key_facts = EntityResolver.dedupe_preserve_order(
            [str(item) for item in metadata.get("key_facts", []) if item]
        )[: self.max_facts]

        if not key_facts and summary:
            key_facts = [summary]

        node_map: dict[str, GraphNode] = {}
        topic_keys = [
            key
            for key in (self._legacy_node(node_map, "topic", topic) for topic in topics)
            if key
        ]
        participant_keys = []
        for participant, canonical_value, identity_metadata in participants:
            node = GraphNode(
                node_type="person",
                value=participant,
                canonical_value=canonical_value,
                metadata=identity_metadata,
            )
            node_map[node.node_key] = node
            participant_keys.append(node.node_key)
        fact_keys = [
            key
            for key in (
                self._legacy_node(node_map, "fact", fact, {"summary": summary})
                for fact in key_facts
            )
            if key
        ]
        return node_map, topic_keys, participant_keys, fact_keys

    def _add_legacy_node_entries(
        self,
        graph: ExtractedGraph,
        source_memory_id: int,
        metadata: dict[str, Any],
        summary: str,
        node_map: dict[str, GraphNode],
        topic_keys: list[str],
        participant_keys: list[str],
        fact_keys: list[str],
    ) -> None:
        """为 fact/topic/participant 节点各写一条检索条目。"""
        for fact_key in fact_keys:
            fact_value = node_map[fact_key].value
            self._add_legacy_entry(
                graph,
                source_memory_id,
                metadata,
                summary,
                "fact",
                f"Fact: {fact_value}. Summary: {summary}",
                [fact_key],
                relation_type="fact",
                confidence=0.9,
            )

        for topic_key in topic_keys:
            topic_value = node_map[topic_key].value
            self._add_legacy_entry(
                graph,
                source_memory_id,
                metadata,
                summary,
                "topic",
                f"Topic: {topic_value}. Summary: {summary}",
                [topic_key],
                relation_type="topic",
                confidence=0.75,
            )

        for person_key in participant_keys:
            person_value = node_map[person_key].value
            self._add_legacy_entry(
                graph,
                source_memory_id,
                metadata,
                summary,
                "participant",
                f"Participant: {person_value}. Summary: {summary}",
                [person_key],
                relation_type="participant",
                confidence=0.7,
            )

    def _add_legacy_edges(
        self,
        graph: ExtractedGraph,
        source_memory_id: int,
        metadata: dict[str, Any],
        summary: str,
        node_map: dict[str, GraphNode],
        topic_keys: list[str],
        participant_keys: list[str],
        fact_keys: list[str],
    ) -> None:
        """建立 topic/participant 与 fact 及彼此之间的边与边条目。"""
        for topic_key in topic_keys:
            for fact_key in fact_keys:
                self._add_legacy_edge(
                    graph,
                    source_memory_id,
                    metadata,
                    summary,
                    topic_key,
                    fact_key,
                    f"Topic {node_map[topic_key].value} describes "
                    f"fact {node_map[fact_key].value}",
                    "describes",
                    0.82,
                )

        for person_key in participant_keys:
            for fact_key in fact_keys:
                self._add_legacy_edge(
                    graph,
                    source_memory_id,
                    metadata,
                    summary,
                    person_key,
                    fact_key,
                    f"Participant {node_map[person_key].value} is linked to "
                    f"fact {node_map[fact_key].value}",
                    "mentioned_in",
                    0.88,
                )

        for index, first_key in enumerate(participant_keys):
            for second_key in participant_keys[index + 1 :]:
                self._add_legacy_edge(
                    graph,
                    source_memory_id,
                    metadata,
                    summary,
                    first_key,
                    second_key,
                    f"Participant {node_map[first_key].value} co-occurs with "
                    f"participant {node_map[second_key].value}",
                    "co_occurs_with",
                    0.7,
                )

    def _add_legacy_edge(
        self,
        graph: ExtractedGraph,
        source_memory_id: int,
        metadata: dict[str, Any],
        summary: str,
        source_key: str,
        target_key: str,
        description: str,
        relation_type: str,
        confidence: float,
    ) -> None:
        """向图中追加一条边及其检索条目。"""
        graph.edges.append(
            GraphEdge(
                source_key=source_key,
                target_key=target_key,
                relation_type=relation_type,
                source_memory_id=source_memory_id,
                confidence=confidence,
                metadata={"summary": summary},
            )
        )
        self._add_legacy_entry(
            graph,
            source_memory_id,
            metadata,
            summary,
            "edge",
            f"{description}. Summary: {summary}",
            [source_key, target_key],
            relation_type=relation_type,
            confidence=confidence,
        )

    @staticmethod
    def _legacy_node(
        node_map: dict[str, GraphNode],
        node_type: str,
        value: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """向节点表写入一个节点并返回其 key。"""
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

    @staticmethod
    def _add_legacy_entry(
        graph: ExtractedGraph,
        source_memory_id: int,
        metadata: dict[str, Any],
        summary: str,
        entry_type: str,
        content_text: str,
        node_keys: list[str],
        relation_type: str | None = None,
        confidence: float = 0.8,
    ) -> None:
        """向图中追加一条带稳定 key 的检索条目。"""
        payload = (
            f"{entry_type}|{source_memory_id}|{relation_type or ''}|"
            f"{'|'.join(node_keys)}|{content_text}"
        )
        entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        entry_metadata = {
            "source_memory_id": source_memory_id,
            "session_id": metadata.get("session_id"),
            "persona_id": metadata.get("persona_id"),
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
                session_id=metadata.get("session_id"),
                persona_id=metadata.get("persona_id"),
                entry_type=entry_type,
                content=content_text,
                metadata=entry_metadata,
                node_keys=node_keys,
                relation_type=relation_type,
            )
        )

    def _extract_from_atoms(
        self,
        source_memory_id: int,
        atoms: list,
        metadata: dict[str, Any] | None = None,
    ) -> ExtractedGraph:
        """Build graph from individual memory atoms with per-atom confidence."""
        metadata = metadata or {}
        graph = ExtractedGraph()
        node_map: dict[str, GraphNode] = {}
        participant_nodes = self._participant_nodes(metadata)
        participant_values = {
            EntityResolver.canonicalize(value)
            for value in metadata.get("participants", [])
            if value
        }
        for display_name, _, identity_metadata in participant_nodes:
            participant_values.add(EntityResolver.canonicalize(display_name))
            participant_values.update(
                EntityResolver.canonicalize(alias)
                for alias in identity_metadata.get("aliases", [])
                if alias
            )

        def _add_node(
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

        for atom in atoms:
            atom_confidence = float(getattr(atom, "confidence", 0.7))
            session_id = getattr(atom, "session_id", None)
            persona_id = getattr(atom, "persona_id", None)
            entities = getattr(atom, "entities", []) or []

            # Atom entities mix topics and LLM participant labels. Stable sender
            # identities replace the participant labels when they are available.
            entity_keys: list[str] = []
            for entity in entities:
                if (
                    participant_nodes
                    and EntityResolver.canonicalize(str(entity)) in participant_values
                ):
                    continue
                entity_key = _add_node("topic", entity)
                if entity_key:
                    entity_keys.append(entity_key)
            for display_name, canonical_value, identity_metadata in participant_nodes:
                node = GraphNode(
                    node_type="person",
                    value=display_name,
                    canonical_value=canonical_value,
                    metadata=identity_metadata,
                )
                node_map[node.node_key] = node
                entity_keys.append(node.node_key)

            # Create a fact node for the atom content
            atom_type = getattr(atom, "atom_type", "unknown")
            atom_type_str = str(getattr(atom_type, "value", atom_type))
            fact_key = _add_node("fact", atom.content, {"atom_type": atom_type_str})
            if not fact_key:
                continue

            # Fact entry with atom's own confidence
            payload = f"fact|{source_memory_id}||{fact_key}|{atom.content}"
            entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
            entry_metadata = {
                "source_memory_id": source_memory_id,
                "session_id": session_id,
                "persona_id": persona_id,
                "importance": float(getattr(atom, "importance", 0.5)),
                "graph_confidence": atom_confidence,
                "atom_type": atom_type_str,
                "ttl_days": float(getattr(atom, "ttl_days", 30.0)),
                "atom_id": int(getattr(atom, "atom_id", 0)),
                "create_time": float(atom.created_at),
                "last_access_time": float(atom.last_accessed_at),
                "expires_at": float(atom.expires_at),
                "decay_type": str(getattr(atom.decay_type, "value", atom.decay_type)),
                "status": str(getattr(atom.status, "value", atom.status)),
            }
            graph.entries.append(
                GraphEntry(
                    entry_key=entry_key,
                    source_memory_id=source_memory_id,
                    session_id=session_id,
                    persona_id=persona_id,
                    entry_type="fact",
                    content=f"Atom: {atom.content}",
                    metadata=entry_metadata,
                    node_keys=[fact_key],
                    relation_type="fact",
                )
            )

            # Link entities to the fact with atom confidence
            for entity_key in dict.fromkeys(entity_keys):
                edge_confidence = atom_confidence * 0.9
                is_person = node_map[entity_key].node_type == "person"
                relation_type = "mentioned_in" if is_person else "describes"
                graph.edges.append(
                    GraphEdge(
                        source_key=entity_key,
                        target_key=fact_key,
                        relation_type=relation_type,
                        source_memory_id=source_memory_id,
                        confidence=edge_confidence,
                        metadata={"atom_content": atom.content},
                    )
                )
                edge_payload = f"edge|{source_memory_id}|{relation_type}|{entity_key}|{fact_key}|{atom.content}"
                edge_entry_key = hashlib.sha1(edge_payload.encode("utf-8")).hexdigest()
                graph.entries.append(
                    GraphEntry(
                        entry_key=edge_entry_key,
                        source_memory_id=source_memory_id,
                        session_id=session_id,
                        persona_id=persona_id,
                        entry_type="edge",
                        content=(
                            f"{'Participant' if is_person else 'Topic'} {node_map[entity_key].value} "
                            f"relates to fact: {atom.content}"
                        ),
                        metadata={
                            **entry_metadata,
                            "graph_confidence": edge_confidence,
                        },
                        node_keys=[entity_key, fact_key],
                        relation_type=relation_type,
                    )
                )

        graph.nodes = list(node_map.values())

        # Fallback: if atoms produced no entries, create a summary entry
        if not graph.entries:
            for atom in atoms:
                summary_key = _add_node("summary", atom.content)
                if summary_key:
                    graph.nodes = list(node_map.values())
                    payload = (
                        f"summary|{source_memory_id}||{summary_key}|{atom.content}"
                    )
                    s_entry_key = hashlib.sha1(payload.encode("utf-8")).hexdigest()
                    graph.entries.append(
                        GraphEntry(
                            entry_key=s_entry_key,
                            source_memory_id=source_memory_id,
                            session_id=getattr(atom, "session_id", None),
                            persona_id=getattr(atom, "persona_id", None),
                            entry_type="summary",
                            content=f"Atom: {atom.content}",
                            metadata={
                                "graph_confidence": float(
                                    getattr(atom, "confidence", 0.6)
                                )
                            },
                            node_keys=[summary_key],
                            relation_type="summary",
                        )
                    )

        return graph


__all__ = ["GraphExtractor"]
