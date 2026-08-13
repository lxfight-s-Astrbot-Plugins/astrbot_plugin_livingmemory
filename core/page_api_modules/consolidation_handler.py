"""记忆整合相关的 Page API 处理器。"""

import aiosqlite
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from .utils import PageApiUtils


class ConsolidationHandler:
    """记忆整合状态查询与手动触发。"""

    def __init__(self, utils: "PageApiUtils"):
        self.utils = utils

    async def get_status(
        self, memory_engine, consolidation_manager, config_manager
    ) -> dict[str, Any]:
        """返回整合配置与已整合/已归档统计。"""
        config = (
            config_manager.get_section("memory_consolidation")
            if config_manager
            else {}
        )
        consolidated_count = 0
        archived_count = 0
        db_path = getattr(memory_engine, "db_path", None)
        if db_path:
            try:
                async with aiosqlite.connect(db_path) as db:
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM documents WHERE json_valid(metadata) "
                        "AND json_array_length("
                        "COALESCE(json_extract(metadata, '$.consolidated_from'), '[]')"
                        ") > 0"
                    )
                    row = await cursor.fetchone()
                    consolidated_count = int(row[0]) if row else 0

                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM documents WHERE "
                        "COALESCE("
                        "CASE WHEN json_valid(metadata) "
                        "THEN json_extract(metadata, '$.status') END,"
                        "'active'"
                        ") = 'archived'"
                    )
                    row = await cursor.fetchone()
                    archived_count = int(row[0]) if row else 0
            except Exception as e:
                logger.warning(f"[PageAPI] 获取整合统计失败: {e}")

        return self.utils.ok(
            {
                "config": config,
                "consolidated_count": consolidated_count,
                "archived_count": archived_count,
            }
        )

    async def run(self, consolidation_manager) -> dict[str, Any]:
        """手动触发一轮记忆整合（强制，忽略冷却）。"""
        if consolidation_manager is None:
            return self.utils.error("记忆整合组件未就绪")
        try:
            result = await consolidation_manager.run_consolidation(force=True)
            return self.utils.ok(result)
        except Exception as e:
            logger.error(f"[PageAPI] 手动整合失败: {e}", exc_info=True)
            return self.utils.error(str(e))
