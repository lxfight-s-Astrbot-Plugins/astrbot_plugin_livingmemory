"""Time-aware atom-level retriever for memory atoms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...storage.atom_store import AtomStore
from ..models.memory_atom import MemoryAtom


@dataclass(slots=True)
class AtomRetrievalResult:
    """A single atom retrieval result with temporal scoring."""

    atom_id: int
    parent_memory_id: int
    content: str
    base_score: float  # BM25 or vector similarity
    temporal_score: float  # decay multiplier
    final_score: float  # base_score * temporal_score
    atom_type: str
    importance: float
    confidence: float
    ttl_days: float
    decay_type: str
    metadata: dict[str, Any]


class AtomRetriever:
    """Retrieve memory atoms with time-aware scoring.

    Atoms are sorted by base_score * temporal_score so that both
    semantic relevance and temporal freshness contribute to ranking.
    """

    def __init__(
        self,
        atom_store: AtomStore,
        config: dict[str, Any] | None = None,
    ):
        self.atom_store = atom_store
        self.config = config or {}

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[AtomRetrievalResult]:
        """Search atoms by FTS, score by relevance and temporal decay."""
        atoms = await self.atom_store.search_fts(
            query=query,
            limit=max(k * 2, k),
            session_id=session_id,
            persona_id=persona_id,
        )

        results: list[AtomRetrievalResult] = []
        for atom in atoms:
            base_score = float(atom.metadata.get("bm25_score", 0.5))
            temporal_score = float(atom.metadata.get("temporal_score", 1.0))
            final_score = base_score * temporal_score
            results.append(
                AtomRetrievalResult(
                    atom_id=atom.atom_id,
                    parent_memory_id=atom.parent_memory_id,
                    content=atom.content,
                    base_score=round(base_score, 4),
                    temporal_score=round(temporal_score, 4),
                    final_score=round(final_score, 4),
                    atom_type=atom.atom_type.value,
                    importance=round(atom.importance, 4),
                    confidence=round(atom.confidence, 4),
                    ttl_days=round(atom.ttl_days, 2),
                    decay_type=atom.decay_type.value,
                    metadata=dict(atom.metadata),
                )
            )

        results.sort(key=lambda r: r.final_score, reverse=True)
        return results[:k]

    async def get_atoms_for_memory(self, parent_memory_id: int) -> list[MemoryAtom]:
        """Return all atoms belonging to a parent memory."""
        return await self.atom_store.get_by_parent(parent_memory_id)

    async def touch(self, atom_id: int) -> None:
        """Update access time for an atom."""
        await self.atom_store.touch(atom_id)


__all__ = ["AtomRetriever", "AtomRetrievalResult"]
