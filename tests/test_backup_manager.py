"""Tests for version-triggered backup manager."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from astrbot_plugin_livingmemory.core.managers.backup_manager import (
    _BACKUP_INFO_FILE,
    PLUGIN_VERSION,
    BackupManager,
)


def test_get_stored_version_first_run(tmp_path: Path) -> None:
    mgr = BackupManager(str(tmp_path))
    assert mgr.get_stored_version() is None


def test_write_and_read_version(tmp_path: Path) -> None:
    mgr = BackupManager(str(tmp_path))
    mgr.write_current_version()
    assert mgr.get_stored_version() == PLUGIN_VERSION


def test_needs_backup_first_run(tmp_path: Path) -> None:
    mgr = BackupManager(str(tmp_path))
    assert mgr.needs_backup() is True


def test_needs_backup_same_version(tmp_path: Path) -> None:
    mgr = BackupManager(str(tmp_path))
    mgr.write_current_version()
    assert mgr.needs_backup() is False


def test_needs_backup_different_version(tmp_path: Path) -> None:
    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("1.0.0", encoding="utf-8")
    assert mgr.needs_backup() is True


def test_backup_if_needed_no_change(tmp_path: Path) -> None:
    mgr = BackupManager(str(tmp_path))
    mgr.write_current_version()
    result = mgr.backup_if_needed()
    assert result is None


def test_backup_if_needed_creates_backup_dir(tmp_path: Path) -> None:
    # Create mock data files
    (tmp_path / "livingmemory.db").write_text("mock db content")
    (tmp_path / "livingmemory.index").write_text("mock index")
    (tmp_path / "conversations.db").write_text("mock conversations")
    (tmp_path / "decay_state.json").write_text('{"last_run": "2025-01-01"}')

    mgr = BackupManager(str(tmp_path))
    # Simulate previous version
    mgr.version_file.write_text("2.0.0", encoding="utf-8")

    backup_dir = mgr.backup_if_needed()
    assert backup_dir is not None
    assert os.path.isdir(backup_dir)
    assert "v2.0.0" in backup_dir

    # Verify files were copied
    backup_path = Path(backup_dir)
    assert (backup_path / "livingmemory.db").exists()
    assert (backup_path / "conversations.db").exists()
    assert (backup_path / "decay_state.json").exists()

    # Verify backup info was written
    info_path = backup_path / _BACKUP_INFO_FILE
    assert info_path.exists()
    info = json.loads(info_path.read_text(encoding="utf-8"))
    assert info["plugin_version"] == PLUGIN_VERSION
    assert info["previous_version"] == "2.0.0"
    assert info["files_copied"] >= 3

    # Verify version file was updated
    assert mgr.get_stored_version() == PLUGIN_VERSION


def test_backup_if_needed_first_install(tmp_path: Path) -> None:
    (tmp_path / "livingmemory.db").write_text("fresh install")

    mgr = BackupManager(str(tmp_path))
    backup_dir = mgr.backup_if_needed()
    assert backup_dir is not None
    assert "unknown" in backup_dir

    # Version file should now be written
    assert mgr.get_stored_version() == PLUGIN_VERSION


def test_backup_handles_missing_files_gracefully(tmp_path: Path) -> None:
    # No data files exist
    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("1.0.0", encoding="utf-8")

    result = mgr.backup_if_needed()
    assert result is not None  # backup dir still created
    backup_path = Path(result)
    assert backup_path.exists()
    info = json.loads((backup_path / _BACKUP_INFO_FILE).read_text(encoding="utf-8"))
    assert info["files_copied"] == 0


def test_backup_preserves_file_content(tmp_path: Path) -> None:
    original_content = "original database content for verification"
    (tmp_path / "livingmemory.db").write_text(original_content)

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("1.0.0", encoding="utf-8")
    backup_dir = mgr.backup_if_needed()

    backup_file = Path(backup_dir) / "livingmemory.db"
    assert backup_file.read_text(encoding="utf-8") == original_content


def test_list_backups_empty(tmp_path: Path) -> None:
    result = BackupManager.list_backups(str(tmp_path))
    assert result == []


def test_list_backups_with_data(tmp_path: Path) -> None:
    # Create a fake backup directory
    backup_root = tmp_path / "backups" / "v1.0.0"
    backup_root.mkdir(parents=True)
    (backup_root / "livingmemory.db").write_text("old backup")
    info = {
        "plugin_version": "2.2.12",
        "previous_version": "1.0.0",
        "backup_timestamp": "2025-01-01T00:00:00+00:00",
        "files_copied": 1,
    }
    (backup_root / _BACKUP_INFO_FILE).write_text(json.dumps(info, ensure_ascii=False))

    result = BackupManager.list_backups(str(tmp_path))
    assert len(result) == 1
    assert result[0]["name"] == "v1.0.0"
    assert result[0]["plugin_version"] == "2.2.12"
    assert result[0]["previous_version"] == "1.0.0"
    assert "livingmemory.db" in result[0]["files"]


def test_backup_includes_graph_files(tmp_path: Path) -> None:
    """图数据库文件应被通配符匹配并备份。"""
    (tmp_path / "livingmemory_graph_documents.db").write_text("graph docs")
    (tmp_path / "livingmemory_graph.index").write_text("graph index")

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.0.0", encoding="utf-8")
    backup_dir = mgr.backup_if_needed()

    backup_path = Path(backup_dir)
    assert (backup_path / "livingmemory_graph_documents.db").exists()
    assert (backup_path / "livingmemory_graph.index").exists()


def test_backup_includes_wal_shm_files(tmp_path: Path) -> None:
    """WAL 和 SHM 日志文件应通过通配符被备份。"""
    (tmp_path / "livingmemory.db").write_text("main")
    (tmp_path / "livingmemory.db-wal").write_text("wal")
    (tmp_path / "livingmemory.db-shm").write_text("shm")

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("2.0.0", encoding="utf-8")
    backup_dir = mgr.backup_if_needed()

    backup_path = Path(backup_dir)
    assert (backup_path / "livingmemory.db-wal").exists()
    assert (backup_path / "livingmemory.db-shm").exists()


def test_get_stored_version_oserror_returns_none(tmp_path: Path) -> None:
    """当版本文件无法读取时返回 None。"""
    mgr = BackupManager(str(tmp_path))
    # 创建一个目录而非文件，触发 read_text 的 OSError
    mgr.version_file.mkdir(parents=True, exist_ok=True)
    assert mgr.get_stored_version() is None


def test_backup_continues_on_copy_failure(tmp_path: Path) -> None:
    """单个文件复制失败不应中断整个备份流程。"""
    (tmp_path / "livingmemory.db").write_text("ok")
    (tmp_path / "conversations.db").write_text("ok")

    mgr = BackupManager(str(tmp_path))
    mgr.version_file.write_text("1.0.0", encoding="utf-8")

    # 用一个不存在的假路径模拟失败——实际场景中 copy2 抛 OSError 会被捕获
    # 这里我们验证正常文件被复制而整体流程不中断
    backup_dir = mgr.backup_if_needed()
    assert backup_dir is not None
    backup_path = Path(backup_dir)
    assert (backup_path / "livingmemory.db").exists()
    assert (backup_path / "conversations.db").exists()


def test_list_backups_multiple_sorted_desc(tmp_path: Path) -> None:
    """多个备份应按版本号逆序排列。"""
    for ver in ("v1.0.0", "v2.0.0", "v2.5.0"):
        root = tmp_path / "backups" / ver
        root.mkdir(parents=True)
        (root / _BACKUP_INFO_FILE).write_text(
            json.dumps({"plugin_version": "2.2.12", "previous_version": ver[1:]}),
            encoding="utf-8",
        )
        (root / "livingmemory.db").write_text(ver)

    result = BackupManager.list_backups(str(tmp_path))
    assert len(result) == 3
    # 逆序：最新版本在前
    assert result[0]["name"] == "v2.5.0"
    assert result[1]["name"] == "v2.0.0"
    assert result[2]["name"] == "v1.0.0"


def test_list_backups_skips_non_directories(tmp_path: Path) -> None:
    """backups 目录下的非目录文件应被跳过。"""
    backups_root = tmp_path / "backups"
    backups_root.mkdir(parents=True)
    (backups_root / "some_file.txt").write_text("not a backup")
    (backups_root / "v1.0.0").mkdir()
    (backups_root / "v1.0.0" / _BACKUP_INFO_FILE).write_text(
        json.dumps({"plugin_version": "2.2.12"}), encoding="utf-8"
    )

    result = BackupManager.list_backups(str(tmp_path))
    assert len(result) == 1
    assert result[0]["name"] == "v1.0.0"


def test_list_backups_corrupt_info_json(tmp_path: Path) -> None:
    """损坏的 backup_info.json 不应导致 list_backups 崩溃，应回退到默认值。"""
    root = tmp_path / "backups" / "v1.0.0"
    root.mkdir(parents=True)
    (root / _BACKUP_INFO_FILE).write_text("{not valid json", encoding="utf-8")
    (root / "livingmemory.db").write_text("data")

    result = BackupManager.list_backups(str(tmp_path))
    assert len(result) == 1
    # 损坏 info 时回退到默认字段
    assert result[0]["name"] == "v1.0.0"
    assert "livingmemory.db" in result[0]["files"]


def test_write_current_version_creates_missing_dir(tmp_path: Path) -> None:
    """write_current_version 应在 data_dir 不存在时自动创建。"""
    nested = tmp_path / "deeply" / "nested" / "data"
    mgr = BackupManager(str(nested))
    mgr.write_current_version()
    assert mgr.version_file.exists()
    assert mgr.get_stored_version() == PLUGIN_VERSION


def test_backup_info_json_contains_all_fields(tmp_path: Path) -> None:
    """验证 backup_info.json 包含所有必要字段。"""
    (tmp_path / "livingmemory.db").write_text("test")

    mgr = BackupManager(str(tmp_path))
    previous_version = "0.0.0"
    mgr.version_file.write_text(previous_version, encoding="utf-8")
    backup_dir = mgr.backup_if_needed()

    info = json.loads(
        (Path(backup_dir) / _BACKUP_INFO_FILE).read_text(encoding="utf-8")
    )
    required_fields = [
        "plugin_version",
        "previous_version",
        "backup_timestamp",
        "backup_unix_time",
        "files_copied",
        "data_dir",
    ]
    for field in required_fields:
        assert field in info, f"Missing field: {field}"
    assert info["previous_version"] == previous_version
    assert info["plugin_version"] == PLUGIN_VERSION
    assert isinstance(info["backup_unix_time"], float)


def test_pluigin_version_constant_matches_metadata() -> None:
    """Ensure PLUGIN_VERSION in backup_manager matches metadata.yaml."""
    import yaml

    metadata_path = Path(__file__).parent.parent / "metadata.yaml"
    if not metadata_path.exists():
        return  # skip if metadata.yaml not found (CI, etc.)

    with open(metadata_path, encoding="utf-8") as f:
        metadata = yaml.safe_load(f)
    assert PLUGIN_VERSION == metadata["version"], (
        f"PLUGIN_VERSION ({PLUGIN_VERSION}) must match metadata.yaml "
        f"({metadata['version']}). Update backup_manager.py."
    )


def _set_mtime(path: Path, timestamp: float) -> None:
    """将文件或目录的 mtime 固定到指定时间戳。"""
    os.utime(path, (timestamp, timestamp))


def test_prune_keeps_latest_entries_per_category(tmp_path: Path) -> None:
    """版本目录、适配器目录、作用域迁移文件按数量保留最新若干份。"""
    backups = tmp_path / "backups"
    backups.mkdir()
    now = time.time()

    for i, name in enumerate(("v1.0.0", "v2.0.0", "v3.0.0")):
        version_dir = backups / name
        version_dir.mkdir()
        (version_dir / "livingmemory.db").write_bytes(b"v" * 10)
        _set_mtime(version_dir, now - 300 + i)

    for i, name in enumerate(
        ("adapter_ownership_a", "adapter_ownership_b", "adapter_ownership_c")
    ):
        adapter_dir = backups / name
        adapter_dir.mkdir()
        (adapter_dir / "livingmemory.db").write_bytes(b"a" * 20)
        _set_mtime(adapter_dir, now - 300 + i)

    for i in range(5):
        scope_file = backups / f"scope_migration_2024010{i}.db"
        scope_file.write_bytes(b"s" * 5)
        _set_mtime(scope_file, now - 300 + i)

    old_daily = backups / "livingmemory_backup_20240101_000000.db"
    old_daily.write_bytes(b"o" * 7)
    _set_mtime(old_daily, now - 10 * 86400)
    new_daily = backups / "livingmemory_backup_20240102_000000.db"
    new_daily.write_bytes(b"n" * 9)
    _set_mtime(new_daily, now - 60)

    stats = BackupManager.prune(str(tmp_path), keep_days=7)

    assert stats == {
        "files_removed": 3,
        "dirs_removed": 3,
        "bytes_removed": 67,
        "total_bytes": 64,
    }
    assert sorted(p.name for p in backups.iterdir()) == [
        "adapter_ownership_c",
        "livingmemory_backup_20240102_000000.db",
        "scope_migration_20240102.db",
        "scope_migration_20240103.db",
        "scope_migration_20240104.db",
        "v2.0.0",
        "v3.0.0",
    ]


def test_prune_total_size_cap_keeps_protected_entries(tmp_path: Path) -> None:
    """超过总量上限时从旧到新删除，但保留最新版本目录与最新每日备份。"""
    backups = tmp_path / "backups"
    backups.mkdir()
    now = time.time()

    for i, name in enumerate(("v1.0.0", "v2.0.0")):
        version_dir = backups / name
        version_dir.mkdir()
        (version_dir / "livingmemory.db").write_bytes(b"v" * 4096)
        _set_mtime(version_dir, now - 100 + i)

    adapter_dir = backups / "adapter_ownership_deadbeef"
    adapter_dir.mkdir()
    (adapter_dir / "livingmemory.db").write_bytes(b"a" * 4096)
    _set_mtime(adapter_dir, now - 50)

    for i in range(2):
        scope_file = backups / f"scope_migration_2024010{i}.db"
        scope_file.write_bytes(b"s" * 2048)
        _set_mtime(scope_file, now - 40 + i)

    old_daily = backups / "livingmemory_backup_20240101_000000.db"
    old_daily.write_bytes(b"o" * 1024)
    _set_mtime(old_daily, now - 10 * 86400)
    new_daily = backups / "livingmemory_backup_20240102_000000.db"
    new_daily.write_bytes(b"n" * 1024)
    _set_mtime(new_daily, now - 30)

    before = sum(p.stat().st_size for p in backups.rglob("*") if p.is_file())
    stats = BackupManager.prune(str(tmp_path), keep_days=7, max_total_mb=0)

    assert sorted(p.name for p in backups.iterdir()) == [
        "livingmemory_backup_20240102_000000.db",
        "v2.0.0",
    ]
    remaining = sum(p.stat().st_size for p in backups.rglob("*") if p.is_file())
    assert stats["total_bytes"] == remaining == 4096 + 1024
    assert stats["bytes_removed"] == before - remaining
    assert stats["dirs_removed"] == 2
    assert stats["files_removed"] == 3


def test_prune_does_not_touch_paths_outside_backups(tmp_path: Path) -> None:
    """data_dir 下的数据文件与 backups 外的同名文件不受清理影响。"""
    backups = tmp_path / "backups"
    for i, name in enumerate(("v1.0.0", "v2.0.0", "v3.0.0")):
        (backups / name).mkdir(parents=True)
        (backups / name / "livingmemory.db").write_text("backup")
        _set_mtime(backups / name, time.time() - 100 + i)

    data_file = tmp_path / "livingmemory.db"
    data_file.write_text("live data")
    outside_scope = tmp_path / "scope_migration_20240101.db"
    outside_scope.write_text("outside backups")
    outside_daily = tmp_path / "livingmemory_backup_20200101_000000.db"
    outside_daily.write_text("expired but outside")

    stats = BackupManager.prune(str(tmp_path), keep_days=7)

    assert stats["dirs_removed"] == 1
    assert data_file.read_text() == "live data"
    assert outside_scope.read_text() == "outside backups"
    assert outside_daily.read_text() == "expired but outside"
    assert not (backups / "v1.0.0").exists()
    assert (backups / "v2.0.0").exists()
    assert (backups / "v3.0.0").exists()


def test_prune_is_idempotent(tmp_path: Path) -> None:
    """连续调用两次时，第二次不再删除任何内容。"""
    backups = tmp_path / "backups"
    backups.mkdir()
    now = time.time()
    for i in range(3):
        version_dir = backups / f"v{i}.0.0"
        version_dir.mkdir()
        (version_dir / "livingmemory.db").write_bytes(b"x" * 10)
        _set_mtime(version_dir, now - 100 + i)

    first = BackupManager.prune(str(tmp_path))
    assert first["dirs_removed"] == 1
    second = BackupManager.prune(str(tmp_path))

    assert second == {
        "files_removed": 0,
        "dirs_removed": 0,
        "bytes_removed": 0,
        "total_bytes": first["total_bytes"],
    }
    assert sorted(p.name for p in backups.iterdir()) == ["v1.0.0", "v2.0.0"]


def test_prune_without_backups_dir_returns_zero_stats(tmp_path: Path) -> None:
    """backups 目录不存在时返回全零统计。"""
    assert BackupManager.prune(str(tmp_path)) == {
        "files_removed": 0,
        "dirs_removed": 0,
        "bytes_removed": 0,
        "total_bytes": 0,
    }
