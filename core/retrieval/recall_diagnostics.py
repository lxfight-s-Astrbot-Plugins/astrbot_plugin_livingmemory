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
) -> None:
    if diagnostics is None:
        return
    if error is not None or not child.get("routes"):
        record_route(diagnostics, name, error)
    for key in ("routes", "route_errors"):
        for route, value in child.get(key, {}).items():
            diagnostics.setdefault(key, {})[f"{name}.{route}"] = value
    if child.get("candidate_limited"):
        diagnostics["candidate_limited"] = True


def all_routes_failed(diagnostics: dict[str, Any]) -> bool:
    routes = diagnostics.get("routes", {})
    return bool(routes) and not any(routes.values())
