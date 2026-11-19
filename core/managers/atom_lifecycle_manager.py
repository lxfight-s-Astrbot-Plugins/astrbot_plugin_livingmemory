"""Periodic lifecycle manager for memory atoms."""

from __future__ import annotations

import asyncio
import difflib
from typing import Any

from astrbot.api import logger

from ..models.memory_atom import AtomStatus
from ...storage.atom_store import AtomStore


class AtomLifecycleManager:
    """Schedule and execute atom lifecycle maintenance tasks."""

    def __init__(
        self,
        atom_store: AtomStore,
        config: dict[str, Any] | None = None,
    ):
        self.atom_store = atom_store
        self.config = config or {}
        self._maintenance_interval_hours = float(
            self.config.get("atom_maintenance_interval_hours", 24.0)
        )
        self._forget_delay_days = float(self.config.get("atom_forget_delay_days", 7.0))
        self._purge_delay_days = float(
            self.config.get(
                "atom_purge_delay_days",
                max(self._forget_delay_days * 4.0, 30.0),
            )
        )
        self._conflict_detection_enabled = bool(
            self.config.get(
                "atom_conflict_detection_enabled",
                self.config.get("memory_conflict_detection_enabled", False),
            )
        )
        self._conflict_scan_limit = int(
            self.config.get("atom_conflict_scan_limit", 200)
        )
        self._conflict_max_group_size = int(
            self.config.get("atom_conflict_max_group_size", 50)
        )
        self._conflict_similarity_threshold = float(
            self.config.get("atom_conflict_similarity_threshold", 0.9)
        )
        self._conflict_negation_similarity_threshold = float(
            self.config.get("atom_conflict_negation_similarity_threshold", 0.5)
        )
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin periodic maintenance in the background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._maintenance_loop())

    async def stop(self) -> None:
        """Cancel the maintenance loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _maintenance_loop(self) -> None:
        while self._running:
            try:
                await self.run_maintenance()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("[AtomLifecycle] 维护任务异常", exc_info=True)
                await asyncio.sleep(60.0)
                continue
            await asyncio.sleep(self._maintenance_interval_hours * 3600.0)

    async def _detect_conflicts(self) -> int:
        if not self._conflict_detection_enabled:
            return 0
        loader = None
        for name in ("get_recent_atoms", "list_recent_atoms", "get_recent_memories"):
            candidate = getattr(self.atom_store, name, None)
            if callable(candidate):
                loader = candidate
                break
        if loader is None:
            return 0
        try:
            atoms = await loader(limit=self._conflict_scan_limit)
        except TypeError:
            atoms = await loader(self._conflict_scan_limit)
        atoms = list(atoms or [])
        updater = getattr(self.atom_store, "update_atom", None)
        if len(atoms) < 2 or not callable(updater):
            return 0
        groups: dict[str, list[Any]] = {}
        for atom in atoms:
            for entity in getattr(atom, "entities", None) or []:
                key = entity if isinstance(entity, str) else getattr(entity, "name", None)
                if key:
                    groups.setdefault(str(key), []).append(atom)
        marked: set[str] = set()
        conflicts = 0
        for group in groups.values():
            if len(group) < 2 or len(group) > self._conflict_max_group_size:
                continue
            ordered = sorted(
                group, key=lambda atom: getattr(atom, "created_at", 0) or 0
            )
            for index, old_atom in enumerate(ordered):
                old_id = getattr(old_atom, "id", None)
                if not old_id or old_id in marked:
                    continue
                if getattr(old_atom, "status", None) == AtomStatus.SUPERSEDED:
                    continue
                old_text = getattr(old_atom, "summary", None) or getattr(
                    old_atom, "content", ""
                )
                if not isinstance(old_text, str) or not old_text.strip():
                    continue
                for new_atom in ordered[index + 1:]:
                    new_id = getattr(new_atom, "id", None)
                    if not new_id or new_id == old_id or new_id in marked:
                        continue
                    new_text = getattr(new_atom, "summary", None) or getattr(
                        new_atom, "content", ""
                    )
                    if not isinstance(new_text, str) or not new_text.strip():
                        continue
                    if old_text == new_text:
                        continue
                    similarity = difflib.SequenceMatcher(
                        None, old_text, new_text
                    ).ratio()
                    old_negation = any(
                        token in old_text for token in ("不", "没", "无", "别")
                    )
                    new_negation = any(
                        token in new_text for token in ("不", "没", "无", "别")
                    )
                    if old_negation == new_negation:
                        if similarity < self._conflict_similarity_threshold:
                            continue
                    elif similarity < self._conflict_negation_similarity_threshold:
                        continue
                    setattr(old_atom, "status", AtomStatus.SUPERSEDED)
                    setattr(old_atom, "superseded_by", new_id)
                    metadata = getattr(old_atom, "metadata", None)
                    reason = f"conflict similarity={similarity:.2f}"
                    if isinstance(metadata, dict):
                        metadata["superseded_reason"] = reason
                    else:
                        setattr(old_atom, "metadata", {"superseded_reason": reason})
                    try:
                        await updater(old_atom)
                    except Exception:
                        logger.error(
                            "[AtomLifecycle] 标记矛盾记忆失败", exc_info=True
                        )
                        continue
                    marked.add(old_id)
                    conflicts += 1
                    break
        return conflicts

    async def run_maintenance(self) -> dict[str, int]:
        """Execute one full maintenance pass. Returns counts per action."""
        result: dict[str, int] = {}

        # 1. Expire stale atoms
        expired = await self.atom_store.expire_stale_atoms()
        result["expired"] = expired

        # 2. Soft-delete old expired atoms: keep metadata but remove them from FTS.
        forgotten = await self.atom_store.forget_expired_atoms(self._forget_delay_days)
        result["forgotten"] = forgotten

        # 3. Physically purge much older forgotten atoms to cap long-term storage.
        purged = await self.atom_store.cleanup_forgotten(self._purge_delay_days)
        result["purged"] = purged

        # 4. Detect and mark conflicting atoms (local prefilter, non-destructive).
        if self._conflict_detection_enabled:
            result["conflicts"] = await self._detect_conflicts()

        return result


__all__ = ["AtomLifecycleManager"]
