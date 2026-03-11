"""Graph-memory data models used by the plugin."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNode:
    """A canonical node inside the graph-memory layer."""

    node_type: str
    value: str
    canonical_value: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        return f"{self.node_type}:{self.canonical_value}"


@dataclass(slots=True)
class GraphEdge:
    """A graph edge extracted from one memory document."""

    source_key: str
    target_key: str
    relation_type: str
    source_memory_id: int
    confidence: float = 0.8
    weight: float = 1.0
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def edge_key(self) -> str:
        return (
            f"{self.source_key}|{self.relation_type}|"
            f"{self.target_key}|{self.source_memory_id}"
        )


@dataclass(slots=True)
class GraphEntry:
    """A searchable graph artifact mapped back to one memory document."""

    entry_key: str
    source_memory_id: int
    session_id: str | None
    persona_id: str | None
    entry_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    node_keys: list[str] = field(default_factory=list)
    relation_type: str | None = None


@dataclass(slots=True)
class ExtractedGraph:
    """Structured graph snapshot extracted from one memory document."""

    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    entries: list[GraphEntry] = field(default_factory=list)


__all__ = ["GraphNode", "GraphEdge", "GraphEntry", "ExtractedGraph"]
