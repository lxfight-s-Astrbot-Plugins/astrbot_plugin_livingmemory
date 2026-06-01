"""Periodic lifecycle manager for memory atoms."""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

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

        return result

    async def run_manual_reinforcement(
        self,
        new_atoms: list,
        similarity_threshold: float = 0.6,
    ) -> int:
        """Attempt to find and reinforce existing atoms similar to new ones.

        Uses simple content-based overlap (Jaccard on token sets) for efficiency.
        Returns the number of atoms that were reinforced.
        """
        if not new_atoms:
            return 0

        reinforced = 0
        for new_atom in new_atoms:
            content = str(new_atom.content)
            new_tokens = set(content.lower().split())
            # For CJK or short text, use character bigrams as fallback tokens
            if len(new_tokens) < 3:
                chars = content.replace(" ", "")
                if len(chars) >= 4:
                    new_tokens = {chars[i : i + 2] for i in range(len(chars) - 1)}

            if len(new_tokens) < 2:
                continue

            search_query = " ".join(list(new_tokens)[:8])
            existing = await self.atom_store.search_fts(
                search_query,
                limit=5,
                include_expired=False,
            )
            for ex in existing:
                ex_content = ex.content.lower()
                ex_tokens = set(ex_content.split())
                if len(ex_tokens) < 2:
                    ex_tokens = (
                        {ex_content[i : i + 2] for i in range(len(ex_content) - 1)}
                        if len(ex_content) >= 4
                        else set()
                    )
                if not ex_tokens or not new_tokens:
                    continue
                jaccard = len(new_tokens & ex_tokens) / max(
                    1, len(new_tokens | ex_tokens)
                )
                if jaccard >= similarity_threshold:
                    await self.atom_store.reinforce(
                        ex.atom_id,
                        new_confidence=float(getattr(new_atom, "confidence", 0.7)),
                    )
                    reinforced += 1
                    break

        return reinforced


__all__ = ["AtomLifecycleManager"]
