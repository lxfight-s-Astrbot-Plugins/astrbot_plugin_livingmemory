# -*- coding: utf-8 -*-
"""
admin_handler.py - 管理员业务逻辑
处理状态查看、配置管理、遗忘代理等管理员功能
"""

from typing import Optional, Dict, Any

from astrbot.api import logger
from astrbot.api.star import Context

from .base_handler import BaseHandler


class AdminHandler(BaseHandler):
    """管理员业务逻辑处理器"""
    
    def __init__(self, context: Context, config: Dict[str, Any], faiss_manager=None, forgetting_agent=None, session_manager=None, recall_engine=None):
        super().__init__(context, config)
        self.faiss_manager = faiss_manager
        self.forgetting_agent = forgetting_agent
        self.session_manager = session_manager
        self.recall_engine = recall_engine

    async def get_memory_status(self) -> Dict[str, Any]:
        """获取记忆库状态"""
        if not self.faiss_manager or not self.faiss_manager.db:
            return self.create_response(False, "记忆库尚未初始化")

        try:
            count = await self.faiss_manager.db.count_documents()
            return self.create_response(True, "获取记忆库状态成功", {"total_count": count})
        except Exception as e:
            logger.error(f"获取记忆库状态失败: {e}", exc_info=True)
            return self.create_response(False, f"获取记忆库状态失败: {e}")

    async def delete_memory(self, doc_id: int) -> Dict[str, Any]:
        """删除指定记忆"""
        if not self.faiss_manager:
            return self.create_response(False, "记忆库尚未初始化")

        try:
            await self.faiss_manager.delete_memories([doc_id])
            return self.create_response(True, f"已成功删除 ID 为 {doc_id} 的记忆")
        except Exception as e:
            logger.error(f"删除记忆时发生错误: {e}", exc_info=True)
            return self.create_response(False, f"删除记忆时发生错误: {e}")

    async def run_forgetting_agent(self) -> Dict[str, Any]:
        """手动触发遗忘代理
        
        注意: 此方法仅供 WebUI 使用，命令行指令 /lmem run_forgetting_agent 已废弃
        """
        if not self.forgetting_agent:
            return self.create_response(False, "遗忘代理尚未初始化")

        try:
            result = await self.forgetting_agent.trigger_manual_run()
            return self.create_response(result["success"], result["message"])
        except Exception as e:
            logger.error(f"调用遗忘代理失败: {e}", exc_info=True)
            return self.create_response(False, f"调用遗忘代理失败: {e}")

    async def set_search_mode(self, mode: str) -> Dict[str, Any]:
        """设置检索模式
        
        注意: 此方法仅供 WebUI 使用，命令行指令 /lmem search_mode 已废弃
        
        推荐使用混合检索模式(hybrid)，支持自动退化机制。
        当某一检索器不可用时，系统会自动降级到可用的检索模式。
        """
        valid_modes = ["hybrid", "dense", "sparse"]
        if mode not in valid_modes:
            return self.create_response(False, f"无效的模式,请使用: {', '.join(valid_modes)}")

        # 更新配置
        if "recall_engine" not in self.config:
            self.config["recall_engine"] = {}

        old_mode = self.config["recall_engine"].get("retrieval_mode", "hybrid")
        self.config["recall_engine"]["retrieval_mode"] = mode

        # 同步到 RecallEngine 实例
        if self.recall_engine:
            try:
                self.recall_engine.retrieval_mode = mode
                if hasattr(self.recall_engine, 'config'):
                    self.recall_engine.config["retrieval_mode"] = mode
                logger.info(f"检索模式已从 '{old_mode}' 更新为: {mode}")
            except Exception as e:
                logger.error(f"同步检索模式到 RecallEngine 时出错: {e}", exc_info=True)
                return self.create_response(False, f"配置已更新,但引擎同步失败: {e}")
        else:
            logger.warning("RecallEngine 尚未初始化,仅更新配置")

        # 根据模式提供不同的提示信息
        if mode == "hybrid":
            mode_tip = (
                "💡 混合检索模式已启用，支持自动退化机制：\n"
                "  • 当密集检索不可用时，自动降级为纯稀疏检索\n"
                "  • 当稀疏检索不可用时，自动降级为纯密集检索\n"
                "  • 两者都可用时，使用混合检索并融合结果"
            )
        else:
            mode_tip = f"💡 注意: 推荐使用混合检索模式以获得更好的检索效果和容错能力"

        return self.create_response(
            True,
            f"检索模式已从 '{old_mode}' 更新为: {mode}\n"
            f"{mode_tip}\n\n"
            f"⚠️ 此更改仅在当前会话有效,重启后将恢复为配置文件中的设置"
        )

    async def get_config_summary(self, action: str = "show") -> Dict[str, Any]:
        """获取配置摘要或验证配置"""
        if action == "show":
            try:
                # 显示主要配置项
                config_summary = {
                    "session_manager": {
                        "max_sessions": self.config.get("session_manager", {}).get("max_sessions", 1000),
                        "session_ttl": self.config.get("session_manager", {}).get("session_ttl", 3600),
                        "current_sessions": self.session_manager.get_session_count() if self.session_manager else 0
                    },
                    "recall_engine": {
                        "retrieval_mode": self.config.get("recall_engine", {}).get("retrieval_mode", "hybrid"),
                        "top_k": self.config.get("recall_engine", {}).get("top_k", 5),
                        "recall_strategy": self.config.get("recall_engine", {}).get("recall_strategy", "weighted")
                    },
                    "reflection_engine": {
                        "summary_trigger_rounds": self.config.get("reflection_engine", {}).get("summary_trigger_rounds", 10),
                        "importance_threshold": self.config.get("reflection_engine", {}).get("importance_threshold", 0.5)
                    },
                    "forgetting_agent": {
                        "enabled": self.config.get("forgetting_agent", {}).get("enabled", True),
                        "check_interval_hours": self.config.get("forgetting_agent", {}).get("check_interval_hours", 24),
                        "retention_days": self.config.get("forgetting_agent", {}).get("retention_days", 90)
                    }
                }
                
                return self.create_response(True, "获取配置摘要成功", config_summary)
                
            except Exception as e:
                return self.create_response(False, f"显示配置时发生错误: {e}")
                
        elif action == "validate":
            try:
                from ..config_validator import validate_config
                # 重新验证当前配置
                validate_config(self.config)
                return self.create_response(True, "配置验证通过，所有参数均有效")
                
            except Exception as e:
                return self.create_response(False, f"配置验证失败: {e}")
                
        else:
            return self.create_response(False, "无效的动作，请使用 'show' 或 'validate'")

    def format_status_for_display(self, response: Dict[str, Any]) -> str:
        """格式化状态信息用于显示"""
        if not response.get("success"):
            return response.get("message", "获取失败")
        
        data = response.get("data", {})
        total_count = data.get("total_count", 0)
        
        return f"📊 LivingMemory 记忆库状态：\n- 总记忆数: {total_count}"

    def format_config_summary_for_display(self, response: Dict[str, Any]) -> str:
        """格式化配置摘要用于显示"""
        if not response.get("success"):
            return response.get("message", "获取失败")
        
        data = response.get("data", {})
        
        config_summary = ["📋 LivingMemory 配置摘要:"]
        config_summary.append("")
        
        # 会话管理器配置
        sm_config = data.get("session_manager", {})
        config_summary.append(f"🗂️ 会话管理:")
        config_summary.append(f"  - 最大会话数: {sm_config.get('max_sessions', 1000)}")
        config_summary.append(f"  - 会话TTL: {sm_config.get('session_ttl', 3600)}秒")
        config_summary.append(f"  - 当前会话数: {sm_config.get('current_sessions', 0)}")
        config_summary.append("")
        
        # 回忆引擎配置
        re_config = data.get("recall_engine", {})
        config_summary.append(f"🧠 回忆引擎:")
        config_summary.append(f"  - 检索模式: {re_config.get('retrieval_mode', 'hybrid')}")
        config_summary.append(f"  - 返回数量: {re_config.get('top_k', 5)}")
        config_summary.append(f"  - 召回策略: {re_config.get('recall_strategy', 'weighted')}")
        config_summary.append("")
        
        # 反思引擎配置
        rf_config = data.get("reflection_engine", {})
        config_summary.append(f"💭 反思引擎:")
        config_summary.append(f"  - 触发轮次: {rf_config.get('summary_trigger_rounds', 10)}")
        config_summary.append(f"  - 重要性阈值: {rf_config.get('importance_threshold', 0.5)}")
        config_summary.append("")
        
        # 遗忘代理配置
        fa_config = data.get("forgetting_agent", {})
        config_summary.append(f"🗑️ 遗忘代理:")
        config_summary.append(f"  - 启用状态: {'是' if fa_config.get('enabled', True) else '否'}")
        config_summary.append(f"  - 检查间隔: {fa_config.get('check_interval_hours', 24)}小时")
        config_summary.append(f"  - 保留天数: {fa_config.get('retention_days', 90)}天")
        
        return "\n".join(config_summary)