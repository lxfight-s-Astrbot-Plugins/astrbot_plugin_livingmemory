"""Helpers for the optional memory visibility metadata.

Two optional fields live in ``documents.metadata``:

- ``visibility``: ``"public"`` (default) or ``"private"``.
- ``disclosed``: whether the user already knows about the memory (default
  ``True``). Only meaningful when ``visibility`` is ``"private"``.

Missing fields keep the legacy meaning: public and already disclosed. No
database migration is required because metadata is a JSON blob.
"""

from typing import Any

VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"
VISIBILITY_VALUES = frozenset({VISIBILITY_PUBLIC, VISIBILITY_PRIVATE})
DEFAULT_DISCLOSED = True


def normalize_visibility(value: Any) -> str:
    """Return a known visibility value, falling back to ``public``."""
    text = str(value or "").strip().lower()
    return text if text in VISIBILITY_VALUES else VISIBILITY_PUBLIC


def normalize_disclosed(value: Any) -> bool:
    """Coerce common JSON/string representations to a boolean."""
    if value is None:
        return DEFAULT_DISCLOSED
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return bool(value)


def is_undisclosed_private(metadata: Any) -> bool:
    """Whether a memory is private and the user has not been told about it."""
    if not isinstance(metadata, dict):
        return False
    if normalize_visibility(metadata.get("visibility")) != VISIBILITY_PRIVATE:
        return False
    return not normalize_disclosed(metadata.get("disclosed"))
