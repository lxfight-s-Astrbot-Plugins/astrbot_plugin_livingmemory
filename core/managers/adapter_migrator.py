"""Backfill and explicitly assign adapter ownership without rewriting memory content."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from ..memory_scope import infer_adapter_id
from ..utils.json_utils import safe_json_dict


class AdapterMigrator:
    """Own metadata-only migration for the main and graph document databases."""

    def __init__(self, db_path: str, graph_path: str | None = None, instances=None):
        self.db_path = Path(db_path)
        self.graph_path = Path(graph_path) if graph_path else None
        self.instances = instances or {}

    async def migrate(self):
        """Backfill once at startup, retaining unknown and mixed source records."""
        async with aiosqlite.connect(self.db_path) as db:
            done = await (
                await db.execute(
                    "SELECT value FROM migration_status WHERE key = 'adapter_ownership_v1'"
                )
            ).fetchone()
            graph_done = await (
                await db.execute(
                    "SELECT value FROM migration_status WHERE key = 'adapter_graph_ownership_v1'"
                )
            ).fetchone()
            graph_pending = (
                self.graph_path is not None
                and self.graph_path.exists()
                and not graph_done
            )
            if done and not graph_pending:
                return
            assignments = {}
            last_id = -1
            source_column = "NULL" if done else "s.source_json"
            while True:
                rows = await (
                    await db.execute(
                        f"SELECT d.id, d.metadata, {source_column} FROM documents d "
                        "LEFT JOIN memory_sources s ON s.memory_id = d.id "
                        "WHERE d.id > ? ORDER BY d.id LIMIT 200",
                        (last_id,),
                    )
                ).fetchall()
                if not rows:
                    break
                for memory_id, raw, source in rows:
                    metadata = safe_json_dict(raw)
                    if metadata.get("adapter_id"):
                        if graph_pending:
                            assignments[int(memory_id)] = str(metadata["adapter_id"])
                        continue
                    if done:
                        continue
                    adapter_id = infer_adapter_id(
                        metadata, json.loads(source) if source else [], self.instances
                    )
                    if adapter_id:
                        assignments[int(memory_id)] = adapter_id
                last_id = int(rows[-1][0])
        await self.assign(
            assignments, mark_migrated=not done, mark_graph=bool(graph_pending)
        )

    async def survey(self):
        """List ownership and original scopes for explicit legacy assignment."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT json_extract(metadata, '$.adapter_id'), "
                    "CASE WHEN json_extract(metadata, '$.adapter_id') IS NULL THEN json_extract(metadata, '$.session_id') END, COUNT(*) "
                    "FROM documents GROUP BY 1, 2 ORDER BY 1, 2"
                )
            ).fetchall()
        return [dict(adapter_id=a, session_id=s, count=n) for a, s, n in rows]

    async def assign(
        self, assignments: dict[int, str], *, mark_migrated=False, mark_graph=False
    ):
        """Back up affected databases and commit ownership updates together.

        Args:
            assignments: Parent memory IDs and their explicitly selected ownership.
            mark_migrated: Complete the one-time startup backfill.

        Returns:
            Number of parent documents updated and the backup directory.
        """
        backup_dir = None
        graph_exists = self.graph_path is not None and self.graph_path.exists()
        async with aiosqlite.connect(self.db_path) as db:
            if assignments:
                backup_dir = (
                    self.db_path.parent
                    / "backups"
                    / (
                        "adapter_ownership_"
                        + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    )
                )
                backup_dir.mkdir(parents=True)
                async with aiosqlite.connect(
                    backup_dir / self.db_path.name
                ) as snapshot:
                    await db.backup(snapshot)
                if graph_exists:
                    async with aiosqlite.connect(self.graph_path) as graph:
                        async with aiosqlite.connect(
                            backup_dir / self.graph_path.name
                        ) as snapshot:
                            await graph.backup(snapshot)
            if graph_exists:
                await db.execute(
                    "ATTACH DATABASE ? AS adapter_graph", (str(self.graph_path),)
                )
            tables = {
                row[0]
                for row in await (
                    await db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                ).fetchall()
            }
            await db.execute("BEGIN IMMEDIATE")
            updated = 0
            try:
                updates = [
                    (adapter_id, memory_id)
                    for memory_id, adapter_id in assignments.items()
                ]
                cursor = await db.executemany(
                    "UPDATE documents SET metadata = json_set(metadata, '$.adapter_id', ?) WHERE id = ?",
                    updates,
                )
                updated = cursor.rowcount
                for table, parent in (
                    ("memory_atoms", "parent_memory_id"),
                    ("graph_entries", "source_memory_id"),
                ):
                    if table in tables:
                        await db.executemany(
                            f"UPDATE {table} SET metadata = json_set(COALESCE(metadata, '{{}}'), '$.adapter_id', ?) WHERE {parent} = ?",
                            updates,
                        )
                if graph_exists:
                    await db.execute(
                        "CREATE INDEX IF NOT EXISTS adapter_graph.idx_graph_adapter ON documents(json_extract(metadata, '$.adapter_id'))"
                    )
                    await db.execute(
                        "CREATE INDEX IF NOT EXISTS adapter_graph.idx_graph_parent ON documents(CAST(json_extract(metadata, '$.source_memory_id') AS INTEGER))"
                    )
                    await db.executemany(
                        "UPDATE adapter_graph.documents SET metadata = json_set(metadata, '$.adapter_id', ?) "
                        "WHERE CAST(json_extract(metadata, '$.source_memory_id') AS INTEGER) = ?",
                        updates,
                    )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_doc_adapter ON documents(json_extract(metadata, '$.adapter_id'))"
                )
                if mark_migrated:
                    await db.execute(
                        "INSERT INTO migration_status(key, value, updated_at) VALUES ('adapter_ownership_v1', '1', datetime('now'))"
                    )
                if mark_graph:
                    await db.execute(
                        "INSERT INTO migration_status(key, value, updated_at) VALUES ('adapter_graph_ownership_v1', '1', datetime('now'))"
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return {
            "updated": updated,
            "backup_path": str(backup_dir) if backup_dir else None,
        }
