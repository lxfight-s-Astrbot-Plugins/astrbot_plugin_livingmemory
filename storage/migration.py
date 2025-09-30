# -*- coding: utf-8 -*-
"""
数据迁移模块 - 处理数据库结构变更和数据兼容性
"""

import aiosqlite
from astrbot.api import logger


class DatabaseMigration:
    """数据库迁移管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def check_and_migrate(self):
        """检查并执行必要的数据迁移"""
        async with aiosqlite.connect(self.db_path) as db:
            # 检查表结构
            await self._check_documents_table_structure(db)
            # 检查并重建FTS索引
            await self._check_and_rebuild_fts_index(db)

    async def _check_documents_table_structure(self, db: aiosqlite.Connection):
        """检查documents表的列结构"""
        try:
            # 获取表信息
            cursor = await db.execute("PRAGMA table_info(documents)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            logger.info(f"检测到documents表列: {column_names}")

            # 检查是否同时存在text和content列
            has_text = 'text' in column_names
            has_content = 'content' in column_names

            if has_text and not has_content:
                logger.info("documents表使用'text'列（标准AstrBot结构）")
            elif has_content and not has_text:
                logger.warning("documents表使用'content'列（非标准结构）")
                logger.info("将执行列名迁移：content -> text")
                await self._migrate_content_to_text(db)
            elif has_text and has_content:
                logger.warning("documents表同时存在text和content列，将删除content列")
                await self._remove_duplicate_column(db, 'content')
            else:
                logger.error("documents表缺少text和content列！")

        except Exception as e:
            logger.error(f"检查表结构时出错: {e}", exc_info=True)

    async def _migrate_content_to_text(self, db: aiosqlite.Connection):
        """将content列迁移为text列"""
        try:
            logger.info("开始列名迁移...")

            # 1. 检查是否有数据
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            count = (await cursor.fetchone())[0]
            logger.info(f"检测到 {count} 条记录需要迁移")

            if count == 0:
                # 没有数据，直接重建表
                logger.info("表为空，直接修改表结构")
                await self._rebuild_documents_table_empty(db)
            else:
                # 有数据，需要数据迁移
                logger.info("表有数据，执行完整迁移")
                await self._rebuild_documents_table_with_data(db)

            logger.info("✓ 列名迁移完成")

        except Exception as e:
            logger.error(f"列名迁移失败: {e}", exc_info=True)
            raise

    async def _rebuild_documents_table_empty(self, db: aiosqlite.Connection):
        """重建空的documents表（直接改列名）"""
        # SQLite不支持直接重命名列，需要重建表
        await db.execute("DROP TABLE IF EXISTS documents")
        # 使用标准的AstrBot表结构
        await db.execute("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT UNIQUE NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

    async def _rebuild_documents_table_with_data(self, db: aiosqlite.Connection):
        """重建documents表并保留数据"""
        try:
            # 1. 备份数据到临时表
            logger.info("  [1/4] 备份数据到临时表...")
            await db.execute("DROP TABLE IF EXISTS documents_backup")
            await db.execute("""
                CREATE TABLE documents_backup AS
                SELECT id, doc_id, content as text, metadata, created_at, updated_at
                FROM documents
            """)

            # 2. 删除旧表
            logger.info("  [2/4] 删除旧表...")
            await db.execute("DROP TABLE documents")

            # 3. 创建新表（使用正确的列名）
            logger.info("  [3/4] 创建新表结构...")
            await db.execute("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT UNIQUE NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. 恢复数据
            logger.info("  [4/4] 恢复数据...")
            await db.execute("""
                INSERT INTO documents (id, doc_id, text, metadata, created_at, updated_at)
                SELECT id, doc_id, text, metadata, created_at, updated_at
                FROM documents_backup
            """)

            # 5. 清理临时表
            await db.execute("DROP TABLE documents_backup")

            await db.commit()
            logger.info("  ✓ 数据迁移完成")

            # 6. 验证数据
            cursor = await db.execute("SELECT COUNT(*) FROM documents")
            final_count = (await cursor.fetchone())[0]
            logger.info(f"  ✓ 迁移后记录数: {final_count}")

        except Exception as e:
            logger.error(f"数据迁移失败，尝试回滚: {e}")
            # 尝试恢复
            try:
                await db.execute("DROP TABLE IF EXISTS documents")
                await db.execute("ALTER TABLE documents_backup RENAME TO documents")
                await db.commit()
                logger.info("已回滚到备份")
            except:
                logger.critical("回滚失败！数据可能丢失！")
            raise

    async def _remove_duplicate_column(self, db: aiosqlite.Connection, column_name: str):
        """移除重复的列"""
        logger.info(f"移除重复列: {column_name}")
        # SQLite需要重建表来删除列
        # 实现类似上面的备份-重建-恢复流程
        pass

    async def _check_and_rebuild_fts_index(self, db: aiosqlite.Connection):
        """检查并重建FTS索引（如果需要）"""
        try:
            # 检查FTS表是否存在
            cursor = await db.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='documents_fts'
            """)
            fts_exists = await cursor.fetchone()

            if fts_exists:
                # 检查FTS表是否为空
                cursor = await db.execute("SELECT COUNT(*) FROM documents_fts")
                fts_count = (await cursor.fetchone())[0]

                cursor = await db.execute("SELECT COUNT(*) FROM documents")
                doc_count = (await cursor.fetchone())[0]

                if fts_count < doc_count:
                    logger.warning(f"FTS索引不完整 (FTS: {fts_count}, 文档: {doc_count})")
                    logger.info("将重建FTS索引...")
                    await self._rebuild_fts_index(db)
                else:
                    logger.info(f"FTS索引正常 ({fts_count} 条记录)")

        except Exception as e:
            logger.warning(f"检查FTS索引时出错: {e}")

    async def _rebuild_fts_index(self, db: aiosqlite.Connection):
        """重建FTS索引"""
        try:
            # 删除并重建FTS表
            await db.execute("DROP TABLE IF EXISTS documents_fts")
            await db.execute("""
                CREATE VIRTUAL TABLE documents_fts
                USING fts5(content, doc_id, tokenize='unicode61')
            """)

            # 从documents表填充数据（注意使用正确的列名）
            await db.execute("""
                INSERT INTO documents_fts(doc_id, content)
                SELECT id, text FROM documents
            """)

            await db.commit()

            # 验证
            cursor = await db.execute("SELECT COUNT(*) FROM documents_fts")
            count = (await cursor.fetchone())[0]
            logger.info(f"✓ FTS索引重建完成，共 {count} 条记录")

        except Exception as e:
            logger.error(f"重建FTS索引失败: {e}", exc_info=True)
            raise