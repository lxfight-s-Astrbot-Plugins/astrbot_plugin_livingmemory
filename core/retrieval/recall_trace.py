"""Request-level recall traces: ring buffer, optional JSONL persistence, stats.

The recorder keeps the last ``max_records`` recall traces in memory so operators
can explain *why* a memory was or was not injected without turning on verbose
debug logging. Persistence is opt-in and appends one JSON object per line to
``<data_dir>/recall_traces/YYYYMMDD.jsonl``; the raw query text is only stored
when ``include_query_text`` is enabled (otherwise a short hash is kept).

The module is dependency-free apart from the host logger so it stays cheap: when
traces are disabled the recall path only reads ``recorder.enabled`` and never
allocates a trace object.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

TRACE_DIR_NAME = "recall_traces"

DEFAULT_TRACE_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "max_records": 50,
    "persist_enabled": False,
    "include_query_text": False,
    "max_file_mb": 10,
    "retention_days": 7,
}

# Object-size guards: a trace must stay in the few-KB range even when the
# retriever returns hundreds of candidates.
_MAX_CANDIDATES = 20
_MAX_INJECTED = 50
_MAX_NOTES = 20
_MAX_BREAKDOWN_KEYS = 12

# Delay before a finished trace is appended to disk. The event handler updates
# ``injected`` synchronously after ``search_memories`` returns, so waiting one
# tick plus a small margin is enough to persist the complete trace.
_PERSIST_DELAY_SECONDS = 0.2

_ROUTE_NAMES = ("document", "graph", "atoms", "hybrid", "cache")


# ---------------------------------------------------------------------------
# Request context (contextvars)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TraceRequestContext:
    """Per-request request metadata shared with the recall call chain."""

    session_id: str | None = None
    scope: str | None = None
    persona_id: str | None = None
    agentic: bool = False
    budget_truncated: bool = False
    trace_id: str | None = None


_TRACE_CONTEXT: ContextVar[TraceRequestContext | None] = ContextVar(
    "livingmemory_trace_context", default=None
)


@contextmanager
def trace_request_context(
    *,
    session_id: str | None = None,
    scope: str | None = None,
    persona_id: str | None = None,
    agentic: bool = False,
    budget_truncated: bool = False,
) -> Iterator[TraceRequestContext]:
    """Attach request metadata to the current async context.

    Args:
        session_id: Unified message origin (umo) of the request.
        scope: Memory scope used for retrieval.
        persona_id: Persona id used for retrieval.
        agentic: True when the call comes from an agent tool instead of the
            automatic recall hook.
        budget_truncated: True when the caller already knows the tool budget
            cut the result set.

    Yields:
        The mutable request context; ``search_memories`` fills ``trace_id``.
    """
    context = TraceRequestContext(
        session_id=session_id,
        scope=scope,
        persona_id=persona_id,
        agentic=agentic,
        budget_truncated=budget_truncated,
    )
    token = _TRACE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _TRACE_CONTEXT.reset(token)


def current_trace_context() -> TraceRequestContext | None:
    """Return the current request's trace context, if any."""
    return _TRACE_CONTEXT.get()


# ---------------------------------------------------------------------------
# Trace model
# ---------------------------------------------------------------------------


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _compact_breakdown(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, float] = {}
    for key, item in value.items():
        if len(compact) >= _MAX_BREAKDOWN_KEYS:
            break
        rounded = _round(item, 3)
        if rounded is None:
            continue
        compact[str(key)[:40]] = rounded
    return compact


def hash_query(query: str) -> str:
    """Return a stable short digest for a raw query string."""
    return hashlib.sha256(str(query or "").encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class RouteTrace:
    """One retrieval route's contribution to a recall request."""

    candidates: int = 0
    ok: bool = True
    error: str | None = None
    ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": int(self.candidates),
            "ok": bool(self.ok),
            "error": self.error,
            "ms": round(float(self.ms), 2),
        }


@dataclass(slots=True)
class RecallTrace:
    """One recall request's explainable trace."""

    trace_id: str
    ts: float
    session_id: str | None = None
    scope: str | None = None
    persona_id: str | None = None
    adapter_id: str | None = None
    query_hash: str = ""
    query_text: str | None = None
    cache_hit: bool = False
    duration_ms: float = 0.0
    agentic: bool = False
    budget_truncated: bool = False
    routes: dict[str, RouteTrace] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    injected: list[dict[str, Any]] = field(default_factory=list)
    injected_chars: int = 0
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    committed: bool = False
    persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the trace."""
        return {
            "id": self.trace_id,
            "ts": round(float(self.ts), 3),
            "session_id": self.session_id,
            "scope": self.scope,
            "persona_id": self.persona_id,
            "adapter_id": self.adapter_id,
            "query_hash": self.query_hash,
            "query_text": self.query_text,
            "cache_hit": bool(self.cache_hit),
            "duration_ms": round(float(self.duration_ms), 2),
            "agentic": bool(self.agentic),
            "budget_truncated": bool(self.budget_truncated),
            "routes": {name: route.to_dict() for name, route in self.routes.items()},
            "filters": self.filters,
            "candidates": self.candidates,
            "injected": self.injected,
            "injected_chars": int(self.injected_chars),
            "notes": self.notes,
            "error": self.error,
        }

    def summary(self) -> dict[str, Any]:
        """Return the compact row shown by ``/lmem trace`` and list APIs."""
        injected_items = len(self.injected)
        return {
            "id": self.trace_id,
            "ts": round(float(self.ts), 3),
            "session_id": self.session_id,
            "scope": self.scope,
            "persona_id": self.persona_id,
            "query_hash": self.query_hash,
            "cache_hit": bool(self.cache_hit),
            "agentic": bool(self.agentic),
            "budget_truncated": bool(self.budget_truncated),
            "candidates": len(self.candidates),
            "injected": injected_items,
            "injected_chars": int(self.injected_chars),
            "duration_ms": round(float(self.duration_ms), 2),
            "routes": {
                name: {
                    "candidates": int(route.candidates),
                    "ok": bool(route.ok),
                    "ms": round(float(route.ms), 2),
                }
                for name, route in self.routes.items()
            },
            "error": self.error,
        }


def snapshot_candidates(
    results: Any, limit: int = _MAX_CANDIDATES
) -> list[dict[str, Any]]:
    """Build the bounded candidate snapshot stored on a trace.

    Args:
        results: Retrieval results (objects exposing the HybridResult fields).
        limit: Maximum number of candidates kept.

    Returns:
        Candidate dictionaries with ids, scores and a compact score breakdown.
    """
    candidates: list[dict[str, Any]] = []
    for index, result in enumerate(results or []):
        if index >= limit:
            break
        try:
            doc_id = int(getattr(result, "doc_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        candidates.append(
            {
                "doc_id": doc_id,
                "final_score": _round(getattr(result, "final_score", 0.0)) or 0.0,
                "rrf": _round(getattr(result, "rrf_score", 0.0)) or 0.0,
                "bm25": _round(getattr(result, "bm25_score", None)),
                "vector": _round(getattr(result, "vector_score", None)),
                "breakdown": _compact_breakdown(
                    getattr(result, "score_breakdown", None)
                ),
            }
        )
    return candidates


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class RecallTraceRecorder:
    """Ring buffer of recent recall traces with optional JSONL persistence."""

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        *,
        trace_dir: str | Path | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Create a recorder.

        Args:
            settings: ``trace_settings`` mapping; missing keys fall back to
                :data:`DEFAULT_TRACE_SETTINGS`.
            trace_dir: Directory for JSONL files (usually ``<data_dir>/recall_traces``).
            clock: Injectable time source for tests.
        """
        raw = settings if isinstance(settings, dict) else {}
        self.enabled = bool(raw.get("enabled", DEFAULT_TRACE_SETTINGS["enabled"]))
        self.max_records = self._clamp_int(
            raw.get("max_records"), DEFAULT_TRACE_SETTINGS["max_records"], 0, 1000
        )
        self.persist_enabled = bool(
            raw.get("persist_enabled", DEFAULT_TRACE_SETTINGS["persist_enabled"])
        )
        self.include_query_text = bool(
            raw.get("include_query_text", DEFAULT_TRACE_SETTINGS["include_query_text"])
        )
        self.max_file_mb = self._clamp_int(
            raw.get("max_file_mb"), DEFAULT_TRACE_SETTINGS["max_file_mb"], 1, 1024
        )
        self.retention_days = self._clamp_int(
            raw.get("retention_days"), DEFAULT_TRACE_SETTINGS["retention_days"], 1, 365
        )
        self._clock = clock or time.time
        self._records: deque[RecallTrace] = deque(maxlen=max(1, self.max_records))
        self._by_id: dict[str, RecallTrace] = {}
        self._seq = 0
        self._trace_dir = Path(trace_dir) if trace_dir else None
        self._pending_persist: dict[str, RecallTrace] = {}
        self._persist_task: asyncio.Task | None = None
        self._last_cleanup_date: str | None = None
        self._stats: dict[str, Any] = {
            "recorded": 0,
            "evicted": 0,
            "cleared": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "persisted": 0,
            "persist_skipped": 0,
            "persist_errors": 0,
            "route_counts": {},
            "route_failures": {},
            "route_ms": {},
            "filtered": {},
            "injected_traces": 0,
            "injected_items": 0,
            "injected_chars": 0,
            "traces_with_error": 0,
        }

    # -- settings helpers ---------------------------------------------------

    @staticmethod
    def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = int(default)
        return max(low, min(parsed, high))

    @property
    def persist_active(self) -> bool:
        """Whether persistence will actually write files."""
        return bool(self.persist_enabled and self._trace_dir is not None)

    @property
    def trace_dir(self) -> Path | None:
        return self._trace_dir

    # -- trace lifecycle ----------------------------------------------------

    def begin(
        self,
        *,
        query: str = "",
        session_id: str | None = None,
        scope: str | None = None,
        persona_id: str | None = None,
        adapter_id: str | None = None,
        cache_hit: bool = False,
        agentic: bool = False,
        budget_truncated: bool = False,
    ) -> RecallTrace | None:
        """Create a draft trace; returns ``None`` when tracing is disabled."""
        if not self.enabled or self.max_records <= 0:
            return None
        now = float(self._clock())
        self._seq += 1
        trace_id = f"{int(now * 1000):x}-{self._seq:04x}"
        return RecallTrace(
            trace_id=trace_id,
            ts=now,
            session_id=session_id,
            scope=scope,
            persona_id=persona_id,
            adapter_id=adapter_id,
            query_hash=hash_query(query),
            query_text=str(query) if self.include_query_text and query else None,
            cache_hit=bool(cache_hit),
            agentic=bool(agentic),
            budget_truncated=bool(budget_truncated),
        )

    def commit(self, trace: RecallTrace | None) -> str | None:
        """Store a finished trace in the ring buffer and update statistics.

        Args:
            trace: Trace returned by :meth:`begin` (``None`` is a no-op).

        Returns:
            The trace id, or ``None`` when tracing is disabled.
        """
        if trace is None:
            return None
        if not trace.committed:
            trace.committed = True
            trace.duration_ms = round(
                max(0.0, (float(self._clock()) - trace.ts) * 1000.0), 2
            )
            if not trace.notes:
                trace.notes = []
            while len(self._records) == self._records.maxlen and self._records:
                evicted = self._records[0]
                self._records.popleft()
                self._by_id.pop(evicted.trace_id, None)
                self._stats["evicted"] += 1
            self._records.append(trace)
            self._by_id[trace.trace_id] = trace
            self._update_stats(trace)
        if self.persist_active and not trace.persisted:
            self._schedule_persist(trace)
        return trace.trace_id

    def record(self, **fields: Any) -> str | None:
        """Create and commit a trace in one call.

        Args:
            **fields: Same keyword arguments as :meth:`begin`.

        Returns:
            The trace id, or ``None`` when tracing is disabled.
        """
        return self.commit(self.begin(**fields))

    def update_injected(
        self,
        trace_id: str | None,
        injected: list[dict[str, Any]] | None,
        injected_chars: int = 0,
        *,
        notes: list[str] | None = None,
    ) -> bool:
        """Attach injection details to a stored trace.

        Args:
            trace_id: Trace id (``None``/unknown ids are ignored).
            injected: Injected items as ``{"doc_id", "score", "chars"}`` dicts.
            injected_chars: Total characters of the injected block.
            notes: Extra notes appended to the trace.

        Returns:
            True when the trace was found and updated.
        """
        trace = self._by_id.get(str(trace_id)) if trace_id else None
        if trace is None:
            return False
        items: list[dict[str, Any]] = []
        for item in injected or []:
            if len(items) >= _MAX_INJECTED or not isinstance(item, dict):
                break
            try:
                doc_id = int(item.get("doc_id") or 0)
            except (TypeError, ValueError):
                doc_id = 0
            items.append(
                {
                    "doc_id": doc_id,
                    "score": _round(item.get("score")),
                    "chars": int(item.get("chars") or 0),
                }
            )
        trace.injected = items
        trace.injected_chars = max(0, int(injected_chars or 0))
        for note in notes or []:
            self.add_note(trace_id, note)
        self._stats["injected_traces"] += 1
        self._stats["injected_items"] += len(items)
        self._stats["injected_chars"] += trace.injected_chars
        if self.persist_active and not trace.persisted:
            self._pending_persist[trace.trace_id] = trace
        return True

    def add_note(self, trace_id: str | None, note: str) -> bool:
        """Append a bounded note to a stored trace."""
        trace = self._by_id.get(str(trace_id)) if trace_id else None
        if trace is None:
            return False
        text = str(note or "").strip()
        if text and len(trace.notes) < _MAX_NOTES:
            trace.notes.append(text[:200])
        return True

    def route(
        self,
        trace: RecallTrace | None,
        name: str,
        *,
        candidates: int = 0,
        ok: bool = True,
        error: str | None = None,
        ms: float = 0.0,
    ) -> None:
        """Record or merge one route contribution on a draft trace."""
        if trace is None:
            return
        trace.routes[name] = RouteTrace(
            candidates=max(0, int(candidates)),
            ok=bool(ok),
            error=error,
            ms=max(0.0, float(ms)),
        )

    # -- queries ------------------------------------------------------------

    def recent(
        self, n: int = 5, *, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the newest trace summaries, optionally filtered by session."""
        limit = max(1, min(int(n or 1), max(1, self.max_records or 1)))
        rows: list[dict[str, Any]] = []
        for trace in reversed(self._records):
            if session_id is not None and trace.session_id != session_id:
                continue
            rows.append(trace.summary())
            if len(rows) >= limit:
                break
        return rows

    def get(self, trace_id: str) -> dict[str, Any] | None:
        """Return one full trace by id."""
        trace = self._by_id.get(str(trace_id or ""))
        return trace.to_dict() if trace is not None else None

    def find_citing(self, memory_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Return recent traces that contained the memory as candidate/injected."""
        try:
            target = int(memory_id)
        except (TypeError, ValueError):
            return []
        matches: list[dict[str, Any]] = []
        for trace in reversed(self._records):
            candidate_ids = {item.get("doc_id") for item in trace.candidates}
            injected_ids = {item.get("doc_id") for item in trace.injected}
            if target not in candidate_ids and target not in injected_ids:
                continue
            matches.append(trace.summary())
            if len(matches) >= max(1, int(limit or 1)):
                break
        return matches

    def clear(self) -> int:
        """Drop all in-memory traces and return how many were removed."""
        count = len(self._records)
        self._records.clear()
        self._by_id.clear()
        self._stats["cleared"] += count
        return count

    def stats(self) -> dict[str, Any]:
        """Aggregate counters for ``/page/traces/stats``."""
        recorded = max(0, int(self._stats["recorded"]))
        route_stats: dict[str, Any] = {}
        for name in _ROUTE_NAMES:
            count = int(self._stats["route_counts"].get(name, 0))
            failures = int(self._stats["route_failures"].get(name, 0))
            total_ms = float(self._stats["route_ms"].get(name, 0.0))
            if count == 0:
                continue
            route_stats[name] = {
                "count": count,
                "failures": failures,
                "avg_ms": round(total_ms / count, 2),
            }
        filtered = {
            key: int(value) for key, value in self._stats["filtered"].items() if value
        }
        return {
            "enabled": bool(self.enabled),
            "max_records": int(self.max_records),
            "records": len(self._records),
            "persist_enabled": bool(self.persist_enabled),
            "persist_active": self.persist_active,
            "include_query_text": bool(self.include_query_text),
            "recorded": recorded,
            "evicted": int(self._stats["evicted"]),
            "cache_hits": int(self._stats["cache_hits"]),
            "cache_misses": int(self._stats["cache_misses"]),
            "routes": route_stats,
            "filtered": filtered,
            "injected": {
                "traces": int(self._stats["injected_traces"]),
                "items": int(self._stats["injected_items"]),
                "chars": int(self._stats["injected_chars"]),
            },
            "traces_with_error": int(self._stats["traces_with_error"]),
            "persistence": {
                "files": self._list_persisted_files(),
                "persisted": int(self._stats["persisted"]),
                "skipped": int(self._stats["persist_skipped"]),
                "errors": int(self._stats["persist_errors"]),
            },
        }

    def _update_stats(self, trace: RecallTrace) -> None:
        stats = self._stats
        stats["recorded"] += 1
        if trace.cache_hit:
            stats["cache_hits"] += 1
        else:
            stats["cache_misses"] += 1
        if trace.error:
            stats["traces_with_error"] += 1
        for name, route in trace.routes.items():
            stats["route_counts"][name] = stats["route_counts"].get(name, 0) + 1
            stats["route_ms"][name] = stats["route_ms"].get(name, 0.0) + float(route.ms)
            if not route.ok:
                stats["route_failures"][name] = stats["route_failures"].get(name, 0) + 1
        rejected = trace.filters.get("rejected") if trace.filters else None
        if isinstance(rejected, dict):
            for reason, count in rejected.items():
                try:
                    value = int(count)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    stats["filtered"][str(reason)] = (
                        stats["filtered"].get(str(reason), 0) + value
                    )

    # -- persistence --------------------------------------------------------

    def _trace_path(self, ts: float) -> Path | None:
        if self._trace_dir is None:
            return None
        day = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y%m%d")
        return self._trace_dir / f"{day}.jsonl"

    def _schedule_persist(self, trace: RecallTrace) -> None:
        self._pending_persist[trace.trace_id] = trace
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._drain_pending()
            return
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = loop.create_task(self._delayed_drain())

    async def _delayed_drain(self) -> None:
        try:
            await asyncio.sleep(_PERSIST_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        try:
            self._drain_pending()
        except Exception:
            logger.error("[RecallTrace] 追踪落盘任务失败", exc_info=True)

    async def flush_persist(self) -> None:
        """Write pending traces immediately (plugin shutdown, tests)."""
        task, self._persist_task = self._persist_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._drain_pending()

    def _drain_pending(self) -> None:
        pending, self._pending_persist = self._pending_persist, {}
        for trace in pending.values():
            if self._append_line(trace):
                trace.persisted = True

    def _append_line(self, trace: RecallTrace) -> bool:
        path = self._trace_path(trace.ts)
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._cleanup_files(trace.ts)
            limit_bytes = self.max_file_mb * 1024 * 1024
            current_size = path.stat().st_size if path.exists() else 0
            if current_size >= limit_bytes:
                self._stats["persist_skipped"] += 1
                logger.debug("[RecallTrace] 追踪文件超过上限，跳过落盘: %s", path.name)
                return False
            line = json.dumps(
                trace.to_dict(), ensure_ascii=False, separators=(",", ":")
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._stats["persisted"] += 1
            return True
        except OSError as exc:
            self._stats["persist_errors"] += 1
            logger.warning("[RecallTrace] 追踪落盘失败: %s", exc)
            return False

    def _cleanup_files(self, now_ts: float) -> None:
        """Delete JSONL files older than ``retention_days`` (once per day)."""
        if self._trace_dir is None:
            return
        day = datetime.fromtimestamp(float(now_ts), tz=timezone.utc).strftime("%Y%m%d")
        if self._last_cleanup_date == day:
            return
        self._last_cleanup_date = day
        cutoff = datetime.fromtimestamp(float(now_ts), tz=timezone.utc) - timedelta(
            days=self.retention_days
        )
        try:
            for path in self._trace_dir.glob("*.jsonl"):
                try:
                    file_day = datetime.strptime(path.stem, "%Y%m%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue
                if file_day < cutoff:
                    path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[RecallTrace] 清理过期追踪失败: %s", exc)

    def prune(self) -> int:
        """Force the retention cleanup and return how many files were removed.

        Returns:
            Number of deleted JSONL files (0 when persistence is off).
        """
        if self._trace_dir is None or not self._trace_dir.exists():
            return 0
        now = float(self._clock())
        cutoff = datetime.fromtimestamp(now, tz=timezone.utc) - timedelta(
            days=self.retention_days
        )
        removed = 0
        for path in self._trace_dir.glob("*.jsonl"):
            try:
                file_day = datetime.strptime(path.stem, "%Y%m%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            if file_day >= cutoff:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("[RecallTrace] 删除过期追踪失败: %s", exc)
        self._last_cleanup_date = datetime.fromtimestamp(now, tz=timezone.utc).strftime(
            "%Y%m%d"
        )
        return removed

    def _list_persisted_files(self) -> list[dict[str, Any]]:
        if self._trace_dir is None or not self._trace_dir.exists():
            return []
        files: list[dict[str, Any]] = []
        try:
            for path in sorted(self._trace_dir.glob("*.jsonl"), reverse=True)[:14]:
                try:
                    files.append({"name": path.name, "bytes": path.stat().st_size})
                except OSError:
                    continue
        except OSError:
            return []
        return files


__all__ = [
    "DEFAULT_TRACE_SETTINGS",
    "TRACE_DIR_NAME",
    "RecallTrace",
    "RecallTraceRecorder",
    "RouteTrace",
    "TraceRequestContext",
    "current_trace_context",
    "hash_query",
    "snapshot_candidates",
    "trace_request_context",
]
