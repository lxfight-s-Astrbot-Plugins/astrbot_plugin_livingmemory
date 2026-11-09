"""请求局部的检索路线诊断，不改变旧检索列表契约。"""

import asyncio
from typing import Any


def record_route(
    diagnostics: dict[str, Any] | None, name: str, error: Exception | None
) -> None:
    if diagnostics is None:
        return
    diagnostics.setdefault("routes", {})[name] = error is None
    if error is not None:
        diagnostics.setdefault("route_errors", {})[name] = (
            "timeout" if isinstance(error, asyncio.TimeoutError) else "unavailable"
        )


def merge_route(
    diagnostics: dict[str, Any] | None,
    name: str,
    child: dict[str, Any],
    error: Exception | None,
    details: dict[str, Any] | None = None,
) -> None:
    """Merge one sub-route's diagnostics into the caller's request dict.

    Args:
        diagnostics: Caller-owned request diagnostics (``None`` disables).
        name: Route name (``document``/``graph``/...).
        child: Diagnostics filled by the sub-route.
        error: Exception raised by the sub-route, if any.
        details: Optional ``{"candidates", "ms"}`` metrics for recall traces.
    """
    if diagnostics is None:
        return
    if error is not None or not child.get("routes"):
        record_route(diagnostics, name, error)
    for key in ("routes", "route_errors"):
        for route, value in child.get(key, {}).items():
            diagnostics.setdefault(key, {})[f"{name}.{route}"] = value
    if child.get("candidate_limited"):
        diagnostics["candidate_limited"] = True
    if isinstance(details, dict):
        diagnostics.setdefault("route_details", {})[name] = {
            "candidates": int(details.get("candidates") or 0),
            "ms": round(float(details.get("ms") or 0.0), 2),
        }


def all_routes_failed(diagnostics: dict[str, Any]) -> bool:
    routes = diagnostics.get("routes", {})
    return bool(routes) and not any(routes.values())
