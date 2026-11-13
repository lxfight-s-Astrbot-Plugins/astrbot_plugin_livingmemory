"""Version-triggered data backup manager.

Automatically backs up all plugin data files when the plugin version changes,
storing each backup under a version-tagged directory for easy recovery.
Also prunes expired or oversized backups so the data directory stays bounded.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from astrbot.api import logger

# Match metadata.yaml; backup manifests use this plugin version.
PLUGIN_VERSION = "2.13.0"

# Retention policies enforced by BackupManager.prune().
KEEP_VERSION_BACKUPS = 2
KEEP_ADAPTER_BACKUPS = 1
KEEP_SCOPE_BACKUPS = 3
MAX_BACKUP_TOTAL_MB = 512

_VERSION_FILE = ".plugin_version"
_BACKUP_INFO_FILE = "backup_info.json"

_VERSION_DIR_RE = re.compile(r"^v\d")
_ADAPTER_BACKUP_PREFIX = "adapter_ownership_"
_SCOPE_BACKUP_PREFIX = "scope_migration_"
_DAILY_BACKUP_GLOB = "*_backup_*.db"


class _BackupEntry(NamedTuple):
    """A direct child of the backups directory with its retention metadata."""

    path: Path
    is_dir: bool
    mtime: float
    size: int


# Files/patterns to include in a full backup (relative to data_dir).
_BACKUP_PATTERNS: list[str] = [
    "livingmemory.db",
    "livingmemory.index",
    "livingmemory_graph_documents.db",
    "livingmemory_graph.index",
    "conversations.db",
    "decay_state.json",
    "*.db-wal",
    "*.db-shm",
]


class BackupManager:
    """Detect version changes and create full data backups."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.version_file = self.data_dir / _VERSION_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_stored_version(self) -> str | None:
        """Return the last-known plugin version, or None on first run."""
        if not self.version_file.exists():
            return None
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def write_current_version(self) -> None:
        """Persist the current plugin version."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.version_file.write_text(PLUGIN_VERSION, encoding="utf-8")

    def needs_backup(self) -> bool:
        """Return True when the plugin version has changed (or is fresh)."""
        stored = self.get_stored_version()
        if stored is None:
            return True  # first install — backup for safety
        return stored != PLUGIN_VERSION

    def backup_if_needed(self) -> str | None:
        """Create a full backup when the version changed. Returns backup dir path or None."""
        if not self.needs_backup():
            return None

        stored = self.get_stored_version()
        old_label = stored or "unknown"
        backup_dir = self.data_dir / "backups" / f"v{old_label}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"[BackupManager] 检测到版本变更 ({old_label} → {PLUGIN_VERSION})，"
            f"正在备份数据到 {backup_dir} ..."
        )

        copied_count = 0
        for pattern in _BACKUP_PATTERNS:
            for file_path in self.data_dir.glob(pattern):
                if not file_path.is_file():
                    continue
                dest = backup_dir / file_path.name
                try:
                    shutil.copy2(file_path, dest)
                    copied_count += 1
                except OSError as exc:
                    logger.error(
                        f"[BackupManager] 备份文件失败 {file_path.name}: {exc}"
                    )

        # Write backup metadata
        info = {
            "plugin_version": PLUGIN_VERSION,
            "previous_version": old_label,
            "backup_timestamp": datetime.now(timezone.utc).isoformat(),
            "backup_unix_time": time.time(),
            "files_copied": copied_count,
            "data_dir": str(self.data_dir),
        }
        info_path = backup_dir / _BACKUP_INFO_FILE
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Update stored version AFTER successful backup
        self.write_current_version()

        logger.info(f"[BackupManager] 备份完成: {copied_count} 个文件 → {backup_dir}")
        return str(backup_dir)

    async def backup_if_needed_async(self) -> str | None:
        """异步版本：通过 asyncio.to_thread 将同步文件 I/O 卸载到线程池。"""
        return await asyncio.to_thread(self.backup_if_needed)

    @staticmethod
    def prune(
        data_dir: str,
        *,
        keep_days: int = 7,
        max_total_mb: int = MAX_BACKUP_TOTAL_MB,
    ) -> dict[str, int]:
        """Remove expired and oversized backups under ``<data_dir>/backups``.

        Only direct children of the backups directory are touched. Every child
        is resolved first and skipped if it escapes the backups directory.
        Retention per category:

        - ``v<digit>...`` directories keep the newest KEEP_VERSION_BACKUPS;
        - ``adapter_ownership_*`` directories keep the newest
          KEEP_ADAPTER_BACKUPS;
        - ``scope_migration_*`` files keep the newest KEEP_SCOPE_BACKUPS;
        - ``*_backup_*.db`` files older than ``keep_days`` are removed.

        If the remaining entries exceed ``max_total_mb``, the oldest entries are
        removed until the cap is met or nothing else can be removed. The newest
        version directory and the newest daily DB backup are never removed by
        the cap.

        Args:
            data_dir: Plugin data directory that owns the backups folder.
            keep_days: Retention period for daily DB backups, in days.
            max_total_mb: Total backups size cap, in MiB.

        Returns:
            Counters of removed files/directories and bytes, plus the final
            backups size in bytes.
        """
        stats = {
            "files_removed": 0,
            "dirs_removed": 0,
            "bytes_removed": 0,
            "total_bytes": 0,
        }
        backups_root = Path(data_dir) / "backups"
        if not backups_root.is_dir():
            return stats

        root = backups_root.resolve()
        entries: list[_BackupEntry] = []
        for child in backups_root.iterdir():
            try:
                path = child.resolve()
            except OSError as exc:
                logger.warning(
                    f"[BackupManager] Skipping unresolvable backup path {child}: {exc}"
                )
                continue
            if path.parent != root:
                logger.warning(
                    f"[BackupManager] Skipping path outside backups dir: {child}"
                )
                continue
            try:
                mtime = path.stat().st_mtime
                is_dir = path.is_dir()
                size = (
                    sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                    if is_dir
                    else path.stat().st_size
                )
            except OSError as exc:
                logger.warning(
                    f"[BackupManager] Skipping unreadable backup entry {child}: {exc}"
                )
                continue
            entries.append(_BackupEntry(path, is_dir, mtime, size))

        removed: set[Path] = set()

        def _remove(entry: _BackupEntry) -> None:
            try:
                if entry.is_dir:
                    shutil.rmtree(entry.path)
                else:
                    entry.path.unlink()
            except OSError as exc:
                logger.warning(
                    f"[BackupManager] Failed to remove backup entry {entry.path}: {exc}"
                )
                return
            removed.add(entry.path)
            stats["bytes_removed"] += entry.size
            if entry.is_dir:
                stats["dirs_removed"] += 1
            else:
                stats["files_removed"] += 1

        # Category retention: keep the newest entries of each backup source.
        version_dirs = sorted(
            (e for e in entries if e.is_dir and _VERSION_DIR_RE.match(e.path.name)),
            key=lambda e: e.mtime,
            reverse=True,
        )
        for entry in version_dirs[KEEP_VERSION_BACKUPS:]:
            _remove(entry)

        adapter_dirs = sorted(
            (
                e
                for e in entries
                if e.is_dir and e.path.name.startswith(_ADAPTER_BACKUP_PREFIX)
            ),
            key=lambda e: e.mtime,
            reverse=True,
        )
        for entry in adapter_dirs[KEEP_ADAPTER_BACKUPS:]:
            _remove(entry)

        scope_files = sorted(
            (
                e
                for e in entries
                if not e.is_dir and e.path.name.startswith(_SCOPE_BACKUP_PREFIX)
            ),
            key=lambda e: e.mtime,
            reverse=True,
        )
        for entry in scope_files[KEEP_SCOPE_BACKUPS:]:
            _remove(entry)

        cutoff = time.time() - keep_days * 86400
        for entry in entries:
            if (
                not entry.is_dir
                and fnmatch.fnmatch(entry.path.name, _DAILY_BACKUP_GLOB)
                and entry.mtime < cutoff
            ):
                _remove(entry)

        # Total size cap: drop oldest entries first, never the protected ones.
        remaining = [e for e in entries if e.path not in removed]
        total_bytes = sum(e.size for e in remaining)
        limit = max_total_mb * 1024 * 1024
        if total_bytes > limit:
            protected: set[Path] = set()
            kept_versions = [
                e for e in remaining if e.is_dir and _VERSION_DIR_RE.match(e.path.name)
            ]
            if kept_versions:
                protected.add(max(kept_versions, key=lambda e: e.mtime).path)
            kept_daily = [
                e
                for e in remaining
                if not e.is_dir and fnmatch.fnmatch(e.path.name, _DAILY_BACKUP_GLOB)
            ]
            if kept_daily:
                protected.add(max(kept_daily, key=lambda e: e.mtime).path)
            for entry in sorted(remaining, key=lambda e: e.mtime):
                if total_bytes <= limit:
                    break
                if entry.path in protected:
                    continue
                _remove(entry)
                if entry.path in removed:
                    total_bytes -= entry.size

        stats["total_bytes"] = sum(e.size for e in entries if e.path not in removed)
        logger.info(
            f"[BackupManager] Backup prune finished: removed "
            f"{stats['files_removed']} files / {stats['dirs_removed']} dirs "
            f"({stats['bytes_removed']} bytes), "
            f"{stats['total_bytes']} bytes kept"
        )
        return stats

    async def prune_async(
        self,
        *,
        keep_days: int = 7,
        max_total_mb: int = MAX_BACKUP_TOTAL_MB,
    ) -> dict[str, int]:
        """Async wrapper that runs prune() in the thread pool.

        Args:
            keep_days: Retention period for daily DB backups, in days.
            max_total_mb: Total backups size cap, in MiB.

        Returns:
            Same counters as prune().
        """
        return await asyncio.to_thread(
            self.prune,
            str(self.data_dir),
            keep_days=keep_days,
            max_total_mb=max_total_mb,
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def list_backups(data_dir: str) -> list[dict]:
        """Enumerate existing backups with their metadata."""
        backups_path = Path(data_dir) / "backups"
        if not backups_path.exists():
            return []

        result: list[dict] = []
        for backup_dir in sorted(backups_path.iterdir(), reverse=True):
            if not backup_dir.is_dir():
                continue
            info_path = backup_dir / _BACKUP_INFO_FILE
            info: dict = {}
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            info.setdefault("directory", str(backup_dir))
            info.setdefault("name", backup_dir.name)
            files = [p.name for p in backup_dir.iterdir() if p.is_file()]
            info.setdefault("files", files)
            info.setdefault("file_count", len(files))
            result.append(info)

        return result


__all__ = [
    "KEEP_ADAPTER_BACKUPS",
    "KEEP_SCOPE_BACKUPS",
    "KEEP_VERSION_BACKUPS",
    "MAX_BACKUP_TOTAL_MB",
    "BackupManager",
    "PLUGIN_VERSION",
]
