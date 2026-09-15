"""Small helpers for safely reading JSON blobs persisted in metadata columns."""

from __future__ import annotations

import json
from typing import Any


def safe_json_dict(value: Any) -> dict[str, Any]:
    """Parse a persisted metadata/payload blob into a dict, tolerating junk.

    Accepts dicts as-is and JSON strings; legacy/corrupt values (None, blanks,
    non-dict JSON, invalid JSON, arbitrary objects) collapse to an empty dict.

    Args:
        value: dict, JSON string, or any value stored in a metadata column.

    Returns:
        The parsed dict; empty dict for anything that is not a JSON object.
    """
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
