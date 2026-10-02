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

    def __init__(self, utils: "PageApiUtils", plugin: Any = None) -> None:
        self.utils = utils
        self._plugin = plugin

    def _beta_recall_enabled(self) -> bool:
        """Beta 自主回忆是否生效（同时要求主动回忆主开关开启）。"""
        if self._plugin is None:
            return True
        config_manager = getattr(self._plugin, "config_manager", None) or getattr(
            getattr(self._plugin, "initializer", None), "config_manager", None
        )
        if config_manager is None:
            return True
        try:
            recall_on = bool(
                config_manager.get("agent_tools.enable_recall_tool", True)
            )
            beta_on = bool(
                config_manager.get("agent_tools.enable_agentic_recall_beta", False)
            )
            return recall_on and beta_on
        except Exception:
            return True

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
        """列出所有提示词及其元数据。

        Beta 自主回忆关闭时不展示其专属模板与分类（模板加载能力保留，
        不删除用户自定义内容），保持关闭态界面与旧版一致。
        """
        try:
            mgr = self._get_manager()
            prompts = mgr.list_prompts()
            categories_list = []
            for cat_id, cat_info in mgr.get_categories().items():
                if cat_id == "agent_recall" and not self._beta_recall_enabled():
                    continue
                categories_list.append(
                    {
                        "id": cat_id,
                        **cat_info,
                    }
                )
            if not self._beta_recall_enabled():
                prompts = [p for p in prompts if p.get("id") != "agent_recall_policy"]
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
