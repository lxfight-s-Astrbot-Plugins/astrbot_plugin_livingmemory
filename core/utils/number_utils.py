"""Small helpers for reading numeric values from persisted metadata."""

from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """Return a finite float or a default for legacy/non-numeric metadata."""
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def clamp_float(
    value: Any,
    *,
    default: float = 0.0,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    """Read a float and clamp it into the requested range."""
    parsed = safe_float(value, default)
    return max(minimum, min(maximum, parsed))
