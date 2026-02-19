"""
数据库迁移管理器 - 处理数据库版本升级和数据迁移
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger


class DBMigration:
    """数据库迁移管理器"""

    # 当前数据库版本
    CURRENT_VERSION = 4

    # 版本历史记录
    VERSION_HISTORY = {
        1: "初始版本 - 基础记忆存储",
        2: "FTS5索引预处理 - 添加分词和停用词支持",
        3: "会话ID迁移 - 标记需要session_id格式升级",
        4: "Schema v2 - 双通道总结字段 + source_window 溯源支持",
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
                    return 1

        except Exception as e:
            logger.error(f"获取数据库版本失败: {e}", exc_info=True)
            return 1

    async def initialize_version_table(self):
        """初始化版本管理表"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS db_version (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version INTEGER NOT NULL,
                        description TEXT,
                        migrated_at TEXT NOT NULL,
                        migration_duration_seconds REAL
                    )
                """)
                await db.commit()
                logger.info(" 版本管理表初始化完成")
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
                    (version, description, datetime.utcnow().isoformat(), duration),
                )
                await db.commit()
                logger.info(f" 数据库版本已更新至: {version}")
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
                f"️ 数据库需要迁移: v{current_version} -> v{self.CURRENT_VERSION}"
            )
        else:
            logger.info(f" 数据库版本最新: v{current_version}")

        return needs_migration

    async def migrate(
        self,
        sparse_retriever: Any | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """
        执行数据库迁移

        Args:
            sparse_retriever: 稀疏检索器实例（用于重建索引）
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
                    f" 开始数据库迁移: v{current_version} -> v{self.CURRENT_VERSION}"
                )

                # 执行迁移步骤
                migration_steps = []

                # 从版本1升级到版本2
                if current_version == 1:
                    migration_steps.append(self._migrate_v1_to_v2)

                # 从版本2升级到版本3
                if current_version <= 2:
                    migration_steps.append(self._migrate_v2_to_v3)

                # 从版本3升级到版本4
                if current_version <= 3:
                    migration_steps.append(self._migrate_v3_to_v4)

                # 执行所有迁移步骤
                for step in migration_steps:
                    await step(sparse_retriever, progress_callback)

                # 计算耗时
                duration = (datetime.now() - start_time).total_seconds()

                # 更新版本号
                await self.set_db_version(
                    self.CURRENT_VERSION,
                    self.VERSION_HISTORY.get(self.CURRENT_VERSION, ""),
                    duration,
                )

                logger.info(f" 数据库迁移成功完成，耗时: {duration:.2f}秒")

                return {
                    "success": True,
                    "message": f"数据库迁移成功: v{current_version} -> v{self.CURRENT_VERSION}",
                    "from_version": current_version,
                    "to_version": self.CURRENT_VERSION,
                    "duration": duration,
                }

            except Exception as e:
                logger.error(f" 数据库迁移失败: {e}", exc_info=True)
                return {
                    "success": False,
                    "message": f"数据库迁移失败: {str(e)}",
                    "error": str(e),
                }

    async def _migrate_v1_to_v2(
        self,
        sparse_retriever: Any | None,
        progress_callback: Callable[[str, int, int], None] | None,
    ):
        """
        从版本1迁移到版本2
        主要变更：重建BM25索引和向量索引以支持新的检索架构
        """
        logger.info(" 执行迁移步骤: v1 -> v2 (重建索引)")

        try:
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
                    logger.info("ℹ️ 未找到documents表，创建新数据库")
                    return

                # 获取文档总数
                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                total_docs_row = await cursor.fetchone()
                total_docs = total_docs_row[0] if total_docs_row else 0

                if total_docs == 0:
                    logger.info("ℹ️ 数据库为空，无需重建索引")
                    return

                logger.info(f" 发现 {total_docs} 条v1版本数据，开始重建索引...")

                # 获取所有文档数据
                cursor = await db.execute("SELECT id, text, metadata FROM documents")
                await cursor.fetchall()

            # 重建索引需要在插件初始化完成后进行
            # 这里只记录需要重建的标记，实际重建在插件启动时处理
            logger.warning(f"️ 检测到 {total_docs} 条v1迁移数据需要重建索引")
            logger.warning(
                " 请在插件初始化完成后，使用 WebUI 的「数据迁移」功能或执行以下命令："
            )
            logger.warning("   /lmem rebuild-index")
            logger.info(f" 数据库迁移完成（{total_docs} 条文档已保留在documents表）")

            # 创建迁移状态标记
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS migration_status (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TEXT
                    )
                """)
                await db.execute(
                    """
                    INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                    VALUES (?, ?, ?)
                """,
                    ("needs_index_rebuild", "true", datetime.utcnow().isoformat()),
                )
                await db.execute(
                    """
                    INSERT OR REPLACE INTO migration_status (key, value, updated_at)
                    VALUES (?, ?, ?)
                """,
                    (
                        "pending_documents_count",
                        str(total_docs),
                        datetime.utcnow().isoformat(),
                    ),
                )
                await db.commit()

        except Exception as e:
            logger.error(f" 数据库迁移失败: {e}", exc_info=True)
            raise

    async def _migrate_v2_to_v3(
        self,
        sparse_retriever: Any | None,
        progress_callback: Callable[[str, int, int], None] | None,
    ):
        """
        从版本2迁移到版本3
        主要变更：标记需要进行 session_id 格式升级

        策略说明：
        不在迁移阶段进行数据转换，原因：
        1. 大多数用户只有一个Bot，旧的session_id实际上就对应当前Bot的unified_msg_origin
        2. 迁移时无法获取运行时的platform信息，无法生成正确的unified_msg_origin
        3. 插件运行时会自动使用unified_msg_origin，旧数据保持不变不影响使用
        4. 只有多Bot用户才会遇到session_id冲突，这种情况下新消息会使用新格式

        此迁移步骤仅升级版本号，不进行实际数据转换。
        """
        logger.info(" 执行迁移步骤: v2 -> v3 (session_id格式升级)")

        try:
            logger.info(
                "ℹ️ 插件现在使用 unified_msg_origin (格式:platform:type:id) 作为会话标识"
            )
            logger.info("ℹ️ 旧数据保持不变，新消息自动使用新格式")
            logger.info("ℹ️ 对于单Bot用户，这不会导致任何问题")
            logger.info("ℹ️ 对于多Bot用户，新旧数据会自然分离，避免混淆")

            logger.info(" v2 -> v3 迁移完成")

        except Exception as e:
            logger.error(f" v2 -> v3 迁移失败: {e}", exc_info=True)
            raise

    async def _migrate_v3_to_v4(
        self,
        sparse_retriever: Any | None,
        progress_callback: Callable[[str, int, int], None] | None,
    ):
        """
        从版本3迁移到版本4
        主要变更：
        - 旧记录 metadata 中补充 summary_schema_version=v1（标记为旧格式）
        - 新写入记录将自动携带 canonical_summary / persona_summary / source_window
        - 无法回填 source_window 的旧数据不做处理（traceable=false 由读取方判断）
        """
        logger.info(" 执行迁移步骤: v3 -> v4 (Schema v2 双通道总结字段)")

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # 检查 documents 表是否存在
                cursor = await db.execute("""
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='table' AND name='documents'
                """)
                row = await cursor.fetchone()
                if not row or row[0] == 0:
                    logger.info("ℹ️ 未找到 documents 表，跳过 v4 迁移")
                    return

                # 为没有 summary_schema_version 的旧记录打上 v1 标记
                # 使用 JSON 函数更新 metadata 字段
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM documents WHERE metadata NOT LIKE '%summary_schema_version%'"
                )
                count_row = await cursor.fetchone()
                legacy_count = count_row[0] if count_row else 0

                if legacy_count > 0:
                    logger.info(f"ℹ️ 发现 {legacy_count} 条旧格式记录，补充 summary_schema_version=v1 标记")

                    # 批量更新：将旧记录的 metadata 中注入 schema 版本标记
                    # 使用 COALESCE(NULLIF(...)) 处理 NULL/空字符串，再用 json_set 追加字段
                    await db.execute("""
                        UPDATE documents
                        SET metadata = json_set(
                            COALESCE(NULLIF(TRIM(metadata), ''), '{}'),
                            '$.summary_schema_version', 'v1',
                            '$.summary_quality', 'unknown'
                        )
                        WHERE metadata NOT LIKE '%summary_schema_version%'
                    """)
                    await db.commit()
                    logger.info(f" 已为 {legacy_count} 条旧记录补充 schema 版本标记")
                else:
                    logger.info("ℹ️ 所有记录已有 summary_schema_version，无需补充")

            logger.info(" v3 -> v4 迁移完成")

        except Exception as e:
            logger.error(f" v3 -> v4 迁移失败: {e}", exc_info=True)
            raise

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

            logger.info(f" 正在创建数据库备份: {backup_path}")

            # 使用SQLite的备份API
            async with aiosqlite.connect(self.db_path) as source:
                async with aiosqlite.connect(str(backup_path)) as dest:
                    await source.backup(dest)

            logger.info(f" 数据库备份成功: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f" 数据库备份失败: {e}", exc_info=True)
            return None
