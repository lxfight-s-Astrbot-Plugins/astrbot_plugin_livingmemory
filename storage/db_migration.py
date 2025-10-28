# -*- coding: utf-8 -*-
"""
数据库迁移管理器 - 处理数据库版本升级和数据迁移
"""

import asyncio
import aiosqlite
from typing import Optional, Dict, Any, Callable
from datetime import datetime
from pathlib import Path

from astrbot.api import logger


class DBMigration:
    """数据库迁移管理器"""

    # 当前数据库版本
    CURRENT_VERSION = 2

    # 版本历史记录
    VERSION_HISTORY = {
        1: "初始版本 - 基础记忆存储",
        2: "FTS5索引预处理 - 添加分词和停用词支持",
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

                if not table_exists:
                    # 检查是否有documents表（判断是否为旧数据库）
                    cursor = await db.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name='documents'
                    """)
                    has_documents = await cursor.fetchone()

                    if has_documents:
                        logger.info("检测到旧版本数据库（无版本表），当前版本: 1")
                        return 1
                    else:
                        # 全新数据库
                        logger.info("检测到全新数据库")
                        return 0

                # 读取版本号
                cursor = await db.execute(
                    "SELECT version FROM db_version ORDER BY id DESC LIMIT 1"
                )
                row = await cursor.fetchone()

                if row:
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
                logger.info("✅ 版本管理表初始化完成")
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
                logger.info(f"✅ 数据库版本已更新至: {version}")
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
                f"⚠️ 数据库需要迁移: v{current_version} -> v{self.CURRENT_VERSION}"
            )
        else:
            logger.info(f"✅ 数据库版本最新: v{current_version}")

        return needs_migration

    async def migrate(
        self,
        sparse_retriever: Optional[Any] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
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
                    f"🔄 开始数据库迁移: v{current_version} -> v{self.CURRENT_VERSION}"
                )

                # 执行迁移步骤
                migration_steps = []

                # 从版本1升级到版本2
                if current_version == 1:
                    migration_steps.append(self._migrate_v1_to_v2)

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

                logger.info(f"✅ 数据库迁移成功完成，耗时: {duration:.2f}秒")

                return {
                    "success": True,
                    "message": f"数据库迁移成功: v{current_version} -> v{self.CURRENT_VERSION}",
                    "from_version": current_version,
                    "to_version": self.CURRENT_VERSION,
                    "duration": duration,
                }

            except Exception as e:
                logger.error(f"❌ 数据库迁移失败: {e}", exc_info=True)
                return {
                    "success": False,
                    "message": f"数据库迁移失败: {str(e)}",
                    "error": str(e),
                }

    async def _migrate_v1_to_v2(
        self,
        sparse_retriever: Optional[Any],
        progress_callback: Optional[Callable[[str, int, int], None]],
    ):
        """
        从版本1迁移到版本2
        主要变更：重建FTS5索引以支持分词和停用词过滤
        """
        logger.info("📦 执行迁移步骤: v1 -> v2 (FTS5索引预处理)")

        if not sparse_retriever:
            logger.warning("⚠️ 未提供稀疏检索器，跳过FTS5索引重建")
            return

        if not sparse_retriever.enabled:
            logger.info("ℹ️ 稀疏检索器未启用，跳过FTS5索引重建")
            return

        try:
            # 检查是否有documents表
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("""
                    SELECT COUNT(*) FROM sqlite_master 
                    WHERE type='table' AND name='documents'
                """)
                has_table = (await cursor.fetchone())[0] > 0

                if not has_table:
                    logger.info("ℹ️ 未找到documents表，跳过FTS5索引重建")
                    return

                # 获取文档总数
                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                total_docs = (await cursor.fetchone())[0]

                if total_docs == 0:
                    logger.info("ℹ️ 数据库为空，跳过FTS5索引重建")
                    return

                logger.info(f"📊 发现 {total_docs} 条文档需要重新索引")

            # 重建FTS5索引
            if progress_callback:
                progress_callback("正在重建FTS5索引...", 0, total_docs)

            await sparse_retriever.rebuild_index()

            if progress_callback:
                progress_callback("FTS5索引重建完成", total_docs, total_docs)

            logger.info(f"✅ FTS5索引重建完成，共处理 {total_docs} 条文档")

        except Exception as e:
            logger.error(f"❌ FTS5索引重建失败: {e}", exc_info=True)
            raise

    async def get_migration_info(self) -> Dict[str, Any]:
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
            except:
                pass

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

    async def create_backup(self) -> Optional[str]:
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

            logger.info(f"🔄 正在创建数据库备份: {backup_path}")

            # 使用SQLite的备份API
            async with aiosqlite.connect(self.db_path) as source:
                async with aiosqlite.connect(str(backup_path)) as dest:
                    await source.backup(dest)

            logger.info(f"✅ 数据库备份成功: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f"❌ 数据库备份失败: {e}", exc_info=True)
            return None
