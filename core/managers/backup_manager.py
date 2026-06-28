"""Version-triggered data backup manager.

Automatically backs up all plugin data files when the plugin version changes,
storing each backup under a version-tagged directory for easy recovery.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import logger

# Match metadata.yaml — single source of truth for the plugin version.
# Keep in sync with the @register decorator in main.py.
PLUGIN_VERSION = "2.3.6"

_VERSION_FILE = ".plugin_version"
_BACKUP_INFO_FILE = "backup_info.json"

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


__all__ = ["BackupManager", "PLUGIN_VERSION"]
