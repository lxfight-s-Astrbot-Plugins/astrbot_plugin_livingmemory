"""
数据库迁移管理器 - 处理数据库版本升级和数据迁移
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger

from .schema import (
    DB_VERSION_SCHEMA_STATEMENTS,
    DOCUMENTS_INDEX_STATEMENTS,
    GRAPH_SCHEMA_STATEMENTS,
    MIGRATION_STATUS_SCHEMA_STATEMENTS,
)


class DBMigration:
    """数据库迁移管理器"""

    # 当前数据库版本
    CURRENT_VERSION = 8

    # 版本历史记录
    VERSION_HISTORY = {
        1: "初始版本 - 基础记忆存储",
        2: "FTS5索引预处理 - 添加分词和停用词支持",
        3: "会话ID迁移 - 标记需要session_id格式升级",
        4: "Schema v2 - 双通道总结字段 + source_window 溯源支持",
        5: "Graph memory - graph tables and dual-route retrieval metadata",
        6: "插件 FTS 表统一 livingmemory 前缀，旧 documents_fts 安全重命名备份",
        7: "Storage indexes and FTS optimization for graph and atom data",
        8: "Write-operation log and access-aware metadata indexes",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.migration_lock = asyncio.Lock()

    async def get_db_version(self) -> int:
        """
        获取当前数据库版本

        Returns:
            int: 数据库版本号，如果不存在版本表则返回1（旧版本）
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 检查版本表是否存在
                cursor = await db.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='db_version'
                """)
                table_exists = await cursor.fetchone()

                if not table_exists or len(table_exists) == 0:
                    # 没有版本表，检查是否有documents表（判断是否为旧数据库）
                    cursor = await db.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='documents'
                    """)
                    has_documents = await cursor.fetchone()

                    if has_documents:
                        # 有documents表但没有版本表，检查是否有数据
                        cursor = await db.execute("SELECT COUNT(*) FROM documents")
                        doc_count_row = await cursor.fetchone()
                        doc_count = doc_count_row[0] if doc_count_row else 0

                        if doc_count > 0:
                            # 有数据但无版本表，判定为v1旧数据库
                            # 注意：v2数据库在初始化时会自动创建版本表，不会出现这种情况
                            logger.info(
                                f"检测到旧版本数据库（无版本表，有{doc_count}条数据），当前版本: 1"
                            )
                            return 1
                        else:
                            # 空数据库，视为最新版本
                            logger.info(
                                "检测到空数据库（已初始化但无数据），视为最新版本"
                            )
                            return self.CURRENT_VERSION
                    else:
                        # 全新数据库，没有任何表，视为最新版本
                        logger.info("检测到全新数据库，视为最新版本")
                        return self.CURRENT_VERSION

                # 读取版本号
                cursor = await db.execute(
                    "SELECT version FROM db_version ORDER BY id DESC LIMIT 1"
                )
                row = await cursor.fetchone()

                if row and len(row) > 0:
                    version = row[0]
                    logger.info(f"当前数据库版本: {version}")
                    return version
                else:
                    # 版本表存在但为空（initialize_version_table 刚创建或迁移中断）。
                    # 不能直接判为 v1，否则全新库也会跑完整迁移链；根据 documents
                    # 表是否有数据区分旧库与全新库。
                    cursor = await db.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='documents'
                    """)
                    has_documents = await cursor.fetchone()
                    if has_documents:
                        cursor = await db.execute("SELECT COUNT(*) FROM documents")
                        doc_count_row = await cursor.fetchone()
                        doc_count = doc_count_row[0] if doc_count_row else 0
                        if doc_count > 0:
                            logger.info(
                                f"检测到旧版本数据库（空版本表，有{doc_count}条数据），当前版本: 1"
                            )
                            return 1
                    logger.info("检测到全新数据库（空版本表且无数据），视为最新版本")
                    return self.CURRENT_VERSION

        except Exception as e:
            logger.error(f"获取数据库版本失败: {e}", exc_info=True)
            return 1

    async def initialize_version_table(self):
        """初始化版本管理表"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                for statement in DB_VERSION_SCHEMA_STATEMENTS:
                    await db.execute(statement)
                await db.commit()
                logger.info("数据库版本管理表初始化完成")
        except Exception as e:
            logger.error(f"初始化版本表失败: {e}", exc_info=True)
            raise

    async def set_db_version(
        self, version: int, description: str = "", duration: float = 0.0
    ):
        """
        设置数据库版本

        Args:
            version: 版本号
            description: 版本描述
            duration: 迁移耗时（秒）
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO db_version (version, description, migrated_at, migration_duration_seconds)
                    VALUES (?, ?, ?, ?)
                """,
                    (
                        version,
                        description,
                        datetime.now(timezone.utc).isoformat(),
                        duration,
                    ),
                )
                await db.commit()
                logger.info(f"数据库版本已更新至: {version}")
        except Exception as e:
            logger.error(f"设置数据库版本失败: {e}", exc_info=True)
            raise

    async def needs_migration(self) -> bool:
        """
        检查是否需要迁移

        Returns:
            bool: True表示需要迁移
        """
        current_version = await self.get_db_version()
        needs_migration = current_version < self.CURRENT_VERSION

        if needs_migration:
            logger.warning(
                f"数据库需要迁移: v{current_version} -> v{self.CURRENT_VERSION}"
            )
        else:
            logger.info(f"数据库版本最新: v{current_version}")

        return needs_migration

    async def migrate(
        self,
        progress_callback: Callable[[str, int, int], None] | None = None,
        *,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """
        执行数据库迁移

        Args:
            progress_callback: 进度回调函数 (message, current, total)

        Returns:
            Dict: 迁移结果
        """
        async with self.migration_lock:
            start_time = datetime.now()

            try:
                # 初始化版本表
                await self.initialize_version_table()

                # 获取当前版本
                current_version = await self.get_db_version()

                if current_version >= self.CURRENT_VERSION:
                    return {
                        "success": True,
                        "message": "数据库已是最新版本，无需迁移",
                        "from_version": current_version,
                        "to_version": self.CURRENT_VERSION,
                        "duration": 0,
                    }

                logger.info(
                    f"开始数据库迁移: v{current_version} -> v{self.CURRENT_VERSION}"
                )

                # 迁移前自动备份，确保数据安全；调用方已完成备份时跳过
                backup_path = None
                if create_backup:
                    backup_path = await self.create_backup()
                    if backup_path:
                        logger.info(f"迁移前备份已创建: {backup_path}")
                    else:
                        logger.warning(
                            "迁移前备份失败，迁移将继续执行。请确认磁盘空间与文件权限。"
                        )

                # 版本 → 步骤声明：数据库版本 <= 起始版本时执行该步骤。
                # v2 -> v3 是纯版本号升级（空步骤），已删除，故 v2 直接进入 v3 -> v4。
                steps = [
                    (1, self._migrate_v1_to_v2),
                    (3, self._migrate_v3_to_v4),
                    (4, self._migrate_v4_to_v5),
                    (5, self._migrate_v5_to_v6),
                    (6, self._migrate_v6_to_v7),
                    (7, self._migrate_v7_to_v8),
                ]
                for from_version, step in steps:
                    if current_version <= from_version:
                        logger.info(
                            f"执行迁移步骤: v{from_version} -> {from_version + 1}: "
                            f"{self.VERSION_HISTORY.get(from_version + 1, '')}"
                        )
                        await step()

                # 计算耗时
                duration = (datetime.now() - start_time).total_seconds()

                # 更新版本号
                await self.set_db_version(
                    self.CURRENT_VERSION,
                    self.VERSION_HISTORY.get(self.CURRENT_VERSION, ""),
                    duration,
                )

                logger.info(f"数据库迁移成功完成，耗时: {duration:.2f}秒")

                return {
                    "success": True,
                    "message": f"数据库迁移成功: v{current_version} -> v{self.CURRENT_VERSION}",
                    "from_version": current_version,
                    "to_version": self.CURRENT_VERSION,
                    "duration": duration,
                    "backup_path": backup_path,
                }

            except Exception as e:
                logger.error(f"数据库迁移失败: {e}", exc_info=True)
                return {
                    "success": False,
                    "message": f"数据库迁移失败: {str(e)}",
                    "error": str(e),
                }

    async def _migrate_v1_to_v2(self):
        """
        从版本1迁移到版本2
        主要变更：重建BM25索引和向量索引以支持新的检索架构
        """
        # 检查是否有documents表
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name='documents'
            """)
            has_table_row = await cursor.fetchone()
            has_table = (
                has_table_row[0] if has_table_row and len(has_table_row) > 0 else 0
            ) > 0

            if not has_table:
                logger.info("未找到 documents 表，按新数据库处理")
                return

            # 获取文档总数
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            total_docs_row = await cursor.fetchone()
            total_docs = total_docs_row[0] if total_docs_row else 0

            if total_docs == 0:
                logger.info("数据库为空，无需重建索引")
                return

            logger.info(f"发现 {total_docs} 条 v1 数据，标记待重建索引")

        # 重建索引需要在插件初始化完成后进行
        # 这里只记录需要重建的标记，实际重建在插件启动时处理
        logger.warning(f"检测到 {total_docs} 条 v1 迁移数据需要重建索引")
        logger.warning("请在插件初始化完成后，使用 WebUI 数据迁移功能或执行命令:")
        logger.warning("/lmem rebuild-index")
        logger.info(f"数据库迁移完成（{total_docs} 条文档已保留在 documents 表）")

        # 创建迁移状态标记
        async with aiosqlite.connect(self.db_path) as db:
            for statement in MIGRATION_STATUS_SCHEMA_STATEMENTS:
                await db.execute(statement)
            await db.execute(
                """
                INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                VALUES (?, ?, ?)
            """,
                (
                    "needs_index_rebuild",
                    "true",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                VALUES (?, ?, ?)
            """,
                (
                    "pending_documents_count",
                    str(total_docs),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

    async def _migrate_v3_to_v4(self):
        """
        从版本3迁移到版本4
        主要变更：
        - 旧记录 metadata 中补充 summary_schema_version=v1（标记为旧格式）
        - 新写入记录将自动携带 canonical_summary / persona_summary / source_window
        - 无法回填 source_window 的旧数据不做处理（traceable=false 由读取方判断）
        """
        async with aiosqlite.connect(self.db_path) as db:
            # 检查 documents 表是否存在
            cursor = await db.execute("""
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='table' AND name='documents'
            """)
            row = await cursor.fetchone()
            if not row or row[0] == 0:
                logger.info("未找到 documents 表，跳过 v4 迁移")
                return

            # 为没有 summary_schema_version 的旧记录打上 v1 标记
            # 使用 JSON 函数更新 metadata 字段
            cursor = await db.execute(
                "SELECT COUNT(*) FROM documents WHERE metadata IS NULL OR metadata NOT LIKE '%summary_schema_version%'"
            )
            count_row = await cursor.fetchone()
            legacy_count = count_row[0] if count_row else 0

            if legacy_count > 0:
                logger.info(
                    f"发现 {legacy_count} 条旧格式记录，补充 summary_schema_version=v1 标记"
                )

                # 批量更新：将旧记录的 metadata 中注入 schema 版本标记
                # 使用 COALESCE(NULLIF(...)) 处理 NULL/空字符串，再用 json_set 追加字段
                await db.execute("""
                    UPDATE documents
                    SET metadata = json_set(
                        COALESCE(NULLIF(TRIM(COALESCE(metadata, '')), ''), '{}'),
                        '$.summary_schema_version', 'v1',
                        '$.summary_quality', 'unknown'
                    )
                    WHERE metadata IS NULL OR metadata NOT LIKE '%summary_schema_version%'
                """)
                await db.commit()
                logger.info(f"已为 {legacy_count} 条旧记录补充 schema 版本标记")
            else:
                logger.info("所有记录已有 summary_schema_version，无需补充")

    async def _migrate_v4_to_v5(self):
        """
        Migrate from version 4 to version 5.

        Main changes:
        - Create graph-memory tables used by the dual-route retrieval layer
        - Keep legacy document memory data unchanged
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            for statement in GRAPH_SCHEMA_STATEMENTS:
                await db.execute(statement)
            await db.commit()

    async def _migrate_v5_to_v6(self):
        """
        从版本5迁移到版本6
        主要变更：插件 FTS 表统一 livingmemory 前缀，旧 documents_fts 仅在精确匹配旧结构时重命名备份。
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS livingmemory_memories_fts
                USING fts5(content, doc_id UNINDEXED, tokenize='unicode61')
            """)
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS livingmemory_graph_entries_fts
                USING fts5(content, entry_id UNINDEXED, tokenize='unicode61')
            """)

            await self._copy_fts_rows_if_exists(
                db,
                source_table="memories_fts",
                target_table="livingmemory_memories_fts",
                columns=("doc_id", "content"),
            )
            await self._copy_fts_rows_if_exists(
                db,
                source_table="graph_entries_fts",
                target_table="livingmemory_graph_entries_fts",
                columns=("entry_id", "content"),
            )

            await self._backup_legacy_documents_fts_if_safe(db)

            await db.commit()

    async def _migrate_v6_to_v7(self):
        """Add storage indexes and run lightweight FTS maintenance."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            await db.execute("PRAGMA foreign_keys = ON")

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_edges_semantic
                ON graph_edges(source_node_id, target_node_id, relation_type)
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_entries_scope_latest
                ON graph_entries(session_id, persona_id, source_memory_id, id DESC)
                """
            )

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entry_nodes_node ON graph_entry_nodes(node_id)"
            )

            # memory_atoms 的索引与 FTS 表由 AtomStore.initialize 负责（迁移阶段该表可能尚不存在）

            for table_name in (
                "livingmemory_memories_fts",
                "livingmemory_graph_entries_fts",
                "memory_atoms_fts",
            ):
                try:
                    await db.execute(
                        f"INSERT INTO {table_name}({table_name}) VALUES ('optimize')"
                    )
                except Exception:
                    logger.debug(
                        f"跳过 FTS optimize: {table_name}",
                        exc_info=True,
                    )

            await db.commit()
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    async def _migrate_v7_to_v8(self):
        """Add expression indexes for hot metadata fields."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA busy_timeout = 10000")
            for statement in DOCUMENTS_INDEX_STATEMENTS:
                await db.execute(statement)
            await db.execute(
                """
                UPDATE documents
                SET metadata = json_set(
                    COALESCE(NULLIF(TRIM(COALESCE(metadata, '')), ''), '{}'),
                    '$.access_count',
                    COALESCE(json_extract(metadata, '$.access_count'), 0)
                )
                WHERE json_valid(
                    COALESCE(NULLIF(TRIM(COALESCE(metadata, '')), ''), '{}')
                )
                  AND json_extract(metadata, '$.access_count') IS NULL
                """
            )

            await db.commit()

    async def _copy_fts_rows_if_exists(
        self,
        db: aiosqlite.Connection,
        source_table: str,
        target_table: str,
        columns: tuple[str, str],
    ):
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (source_table,),
        )
        if not await cursor.fetchone():
            return

        first_column, second_column = columns
        await db.execute(f"DELETE FROM {target_table}")
        await db.execute(
            f"""
            INSERT INTO {target_table}({first_column}, {second_column})
            SELECT {first_column}, {second_column} FROM {source_table}
            """
        )
        await db.execute(f"DROP TABLE IF EXISTS {source_table}")
        logger.info(f"已迁移并删除旧 FTS 表: {source_table} -> {target_table}")

    async def _backup_legacy_documents_fts_if_safe(self, db: aiosqlite.Connection):
        cursor = await db.execute("""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name='documents_fts'
        """)
        row = await cursor.fetchone()
        if not row:
            logger.info("未发现 documents_fts 表，跳过旧表备份")
            return

        if not await self._is_legacy_livingmemory_documents_fts(db, row[0] or ""):
            logger.warning(
                "documents_fts 不完全匹配旧 LivingMemory FTS 结构，保留不处理"
            )
            return

        cursor = await db.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='livingmemory_legacy_documents_fts_backup'
        """)
        if await cursor.fetchone():
            logger.warning(
                "旧表备份 livingmemory_legacy_documents_fts_backup 已存在，保留 documents_fts 不处理"
            )
            return

        await db.execute(
            "ALTER TABLE documents_fts RENAME TO livingmemory_legacy_documents_fts_backup"
        )
        logger.warning(
            "已将旧 LivingMemory documents_fts 重命名为 livingmemory_legacy_documents_fts_backup"
        )

    async def _is_legacy_livingmemory_documents_fts(
        self,
        db: aiosqlite.Connection,
        create_sql: str,
    ) -> bool:
        normalized_sql = " ".join(create_sql.lower().replace("\n", " ").split())
        expected_sql = "create virtual table documents_fts using fts5(content, doc_id, tokenize='unicode61')"
        if normalized_sql != expected_sql:
            return False

        cursor = await db.execute("PRAGMA table_xinfo(documents_fts)")
        rows = await cursor.fetchall()
        visible_columns = [row[1] for row in rows if int(row[6]) == 0]
        return visible_columns == ["content", "doc_id"]

    async def get_migration_info(self) -> dict[str, Any]:
        """
        获取迁移信息

        Returns:
            Dict: 迁移信息
        """
        try:
            current_version = await self.get_db_version()
            needs_migration = await self.needs_migration()

            # 获取迁移历史
            migration_history = []
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute("""
                        SELECT version, description, migrated_at, migration_duration_seconds
                        FROM db_version
                        ORDER BY id DESC
                        LIMIT 10
                    """)
                    rows = await cursor.fetchall()

                    for row in rows:
                        migration_history.append(
                            {
                                "version": row[0],
                                "description": row[1],
                                "migrated_at": row[2],
                                "duration": row[3],
                            }
                        )
            except Exception as e:
                logger.error(f"获取迁移历史失败: {e}", exc_info=True)

            return {
                "current_version": current_version,
                "latest_version": self.CURRENT_VERSION,
                "needs_migration": needs_migration,
                "version_history": self.VERSION_HISTORY,
                "migration_history": migration_history,
                "db_path": self.db_path,
            }

        except Exception as e:
            logger.error(f"获取迁移信息失败: {e}", exc_info=True)
            return {"error": str(e)}

    async def create_backup(self) -> str | None:
        """
        创建数据库备份

        Returns:
            Optional[str]: 备份文件路径，失败返回None
        """
        try:
            db_path = Path(self.db_path)
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = (
                backup_dir / f"{db_path.stem}_backup_{timestamp}{db_path.suffix}"
            )

            logger.info(f"正在创建数据库备份: {backup_path}")

            # 使用SQLite的备份API
            async with aiosqlite.connect(self.db_path) as source:
                async with aiosqlite.connect(str(backup_path)) as dest:
                    await source.backup(dest)

            logger.info(f"数据库备份成功: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f"数据库备份失败: {e}", exc_info=True)
            return None
