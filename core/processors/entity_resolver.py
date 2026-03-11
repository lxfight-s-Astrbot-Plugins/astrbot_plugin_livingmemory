"""Utilities for canonicalizing graph-memory entities."""

from __future__ import annotations

import re


class EntityResolver:
    """Provide lightweight canonicalization for graph entities."""

    _whitespace_re = re.compile(r"\s+")
    _edge_punctuation_re = re.compile(
        r"^[\s,.;:!?'\"，。；：！？、（）()\[\]{}<>《》]+|[\s,.;:!?'\"，。；：！？、（）()\[\]{}<>《》]+$"
    )

    @classmethod
    def canonicalize(cls, value: str) -> str:
        """Normalize an entity string for deduplication."""
        if not value:
            return ""
        normalized = cls._edge_punctuation_re.sub("", value.strip())
        normalized = cls._whitespace_re.sub(" ", normalized)
        if normalized.isascii():
            normalized = normalized.lower()
        return normalized

    @classmethod
    def dedupe_preserve_order(cls, values: list[str]) -> list[str]:
        """Return unique values while preserving the original order."""
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            canonical = cls.canonicalize(value)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            result.append(value.strip())
        return result


__all__ = ["EntityResolver"]
