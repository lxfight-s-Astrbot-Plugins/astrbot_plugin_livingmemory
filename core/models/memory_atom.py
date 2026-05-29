"""Time-aware memory atom models with configurable TTL and decay."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AtomType(str, Enum):
    EPISODIC = "episodic"
    FACTUAL = "factual"
    RELATIONAL = "relational"
    PREFERENCE = "preference"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class DecayType(str, Enum):
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    STEP = "step"


class AtomStatus(str, Enum):
    ACTIVE = "active"
    DORMANT = "dormant"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    FORGOTTEN = "forgotten"


# Base TTL (days) and decay configuration per atom type.
_ATOM_TTL_CONFIG: dict[AtomType, dict[str, Any]] = {
    AtomType.EPISODIC:   {"base_ttl": 7,   "decay_type": DecayType.EXPONENTIAL},
    AtomType.PLANNED:    {"base_ttl": 2,   "decay_type": DecayType.STEP},
    AtomType.FACTUAL:    {"base_ttl": 180, "decay_type": DecayType.EXPONENTIAL},
    AtomType.RELATIONAL: {"base_ttl": 90,  "decay_type": DecayType.LINEAR},
    AtomType.PREFERENCE: {"base_ttl": 60,  "decay_type": DecayType.EXPONENTIAL},
    AtomType.UNKNOWN:    {"base_ttl": 30,  "decay_type": DecayType.EXPONENTIAL},
}


@dataclass(slots=True)
class MemoryAtom:
    """A fine-grained, time-aware memory unit extracted from a conversation."""

    parent_memory_id: int
    atom_type: AtomType = AtomType.UNKNOWN
    content: str = ""
    entities: list[str] = field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.7

    # Temporal fields
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    last_reinforced_at: float | None = None
    event_time: float | None = None
    ttl_days: float = 30.0
    expires_at: float = 0.0

    # Lifecycle
    status: AtomStatus = AtomStatus.ACTIVE
    reinforcement_count: int = 0
    decay_type: DecayType = DecayType.EXPONENTIAL

    session_id: str | None = None
    persona_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Internal id, set after insertion
    atom_id: int = 0

    def compute_temporal_score(self, reference_time: float | None = None) -> float:
        """Return the decay multiplier for this atom at the given time (0-1)."""
        if reference_time is None:
            reference_time = time.time()
        days_since = max(0.0, (reference_time - self.last_accessed_at) / 86400.0)
        effective_ttl = max(1.0, self.ttl_days)

        if self.decay_type == DecayType.LINEAR:
            return max(0.0, 1.0 - days_since / effective_ttl)
        elif self.decay_type == DecayType.STEP:
            return 1.0 if days_since <= effective_ttl else 0.05
        else:  # EXPONENTIAL
            half_life = effective_ttl / 2.0
            return math.exp(-math.log(2) * days_since / max(0.5, half_life))

    def is_expired(self, reference_time: float | None = None) -> bool:
        """Check whether the atom has passed its expiry threshold."""
        if reference_time is None:
            reference_time = time.time()
        return reference_time >= self.expires_at


def compute_ttl(
    atom_type: AtomType,
    importance: float = 0.5,
    reinforcement_count: int = 0,
    event_time: float | None = None,
) -> tuple[float, DecayType]:
    """Compute TTL (days) and decay type for a given atom classification."""
    config = _ATOM_TTL_CONFIG.get(atom_type, _ATOM_TTL_CONFIG[AtomType.UNKNOWN])
    base_ttl = float(config["base_ttl"])
    decay_type = DecayType(config["decay_type"])

    if atom_type == AtomType.PLANNED and event_time is not None:
        days_until_event = max(0.0, (event_time - time.time()) / 86400.0)
        base_ttl = days_until_event + base_ttl

    importance_factor = 0.5 + max(0.0, min(1.0, importance))
    reinforcement_factor = 1.0 + min(0.5, reinforcement_count * 0.1)
    ttl = base_ttl * importance_factor * reinforcement_factor

    return max(1.0, ttl), decay_type


__all__ = [
    "MemoryAtom",
    "AtomType",
    "DecayType",
    "AtomStatus",
    "compute_ttl",
]
