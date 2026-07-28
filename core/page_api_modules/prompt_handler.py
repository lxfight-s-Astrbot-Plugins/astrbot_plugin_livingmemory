"""
提示词管理 API 模块
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quart import request

from astrbot.api import logger

if TYPE_CHECKING:
    from .utils import PageApiUtils


class PromptHandler:
    """提示词管理处理器"""

    def __init__(self, utils: "PageApiUtils") -> None:
        self.utils = utils

    # ---- 辅助 -----------------------------------------------------------

    @staticmethod
    def _get_manager():
        """延迟导入 PromptManager 单例"""
        from ..prompts.prompt_manager import get_prompt_manager

        mgr = get_prompt_manager()
        if mgr is None:
            raise RuntimeError("PromptManager 尚未初始化")
        return mgr

    # ---- API 方法 --------------------------------------------------------

    async def list_prompts(self) -> dict[str, Any]:
        """列出所有提示词及其元数据"""
        try:
            mgr = self._get_manager()
            prompts = mgr.list_prompts()
            categories_list = []
            for cat_id, cat_info in mgr.get_categories().items():
                categories_list.append(
                    {
                        "id": cat_id,
                        **cat_info,
                    }
                )
            return self.utils.ok(
                {
                    "prompts": prompts,
                    "categories": categories_list,
                }
            )
        except Exception as e:
            logger.error(f"[PromptHandler] 列出提示词失败: {e}", exc_info=True)
            return self.utils.error(str(e))

    async def get_prompt_detail(self) -> dict[str, Any]:
        """获取单个提示词的完整信息"""
        try:
            prompt_id = request.args.get("id", "")
            if not prompt_id:
                return self.utils.error("缺少参数: id")

            mgr = self._get_manager()
            detail = mgr.get_prompt_detail(prompt_id)
            return self.utils.ok(detail)
        except KeyError as e:
            return self.utils.error(str(e))
        except Exception as e:
            logger.error(f"[PromptHandler] 获取提示词详情失败: {e}", exc_info=True)
            return self.utils.error(str(e))

    async def get_prompt_default(self) -> dict[str, Any]:
        """获取提示词的内置默认内容（不修改任何状态）"""
        try:
            prompt_id = request.args.get("id", "")
            if not prompt_id:
                return self.utils.error("缺少参数: id")

            mgr = self._get_manager()
            content = mgr.get_default_content(prompt_id)
            return self.utils.ok({"id": prompt_id, "content": content})
        except KeyError as e:
            return self.utils.error(str(e))
        except Exception as e:
            logger.error(f"[PromptHandler] 获取默认提示词失败: {e}", exc_info=True)
            return self.utils.error(str(e))
            return self.utils.error(str(e))

    async def update_prompt(self) -> dict[str, Any]:
        """更新提示词内容"""
        try:
            data = await request.get_json(silent=True) or {}
            prompt_id = str(data.get("id", "")).strip()
            content = str(data.get("content", ""))

            if not prompt_id:
                return self.utils.error("缺少参数: id")
            if not content:
                return self.utils.error("缺少参数: content")

            mgr = self._get_manager()
            mgr.update_prompt(prompt_id, content)
            logger.info(f"[PromptHandler] 提示词 '{prompt_id}' 已更新")

            return self.utils.ok(
                {
                    "id": prompt_id,
                    "message": "提示词已保存",
                }
            )
        except KeyError as e:
            return self.utils.error(str(e))
        except Exception as e:
            logger.error(f"[PromptHandler] 更新提示词失败: {e}", exc_info=True)
            return self.utils.error(str(e))

    async def reset_prompt(self) -> dict[str, Any]:
        """重置提示词为内置默认值"""
        try:
            data = await request.get_json(silent=True) or {}
            prompt_id = str(data.get("id", "")).strip()

            if not prompt_id:
                return self.utils.error("缺少参数: id")

            mgr = self._get_manager()
            mgr.reset_prompt(prompt_id)
            logger.info(f"[PromptHandler] 提示词 '{prompt_id}' 已重置为默认值")

            # 返回重置后的内容
            new_content = mgr.get_prompt(prompt_id)
            return self.utils.ok(
                {
                    "id": prompt_id,
                    "content": new_content,
                    "message": "提示词已恢复为默认值",
                }
            )
        except KeyError as e:
            return self.utils.error(str(e))
        except Exception as e:
            logger.error(f"[PromptHandler] 重置提示词失败: {e}", exc_info=True)
            return self.utils.error(str(e))
