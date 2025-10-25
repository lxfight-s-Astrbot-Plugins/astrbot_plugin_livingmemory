# -*- coding: utf-8 -*-
"""
fusion_handler.py - 融合策略业务逻辑
处理检索融合策略的管理和测试
"""

from typing import Optional, Dict, Any, List

from astrbot.api import logger
from astrbot.api.star import Context

from .base_handler import BaseHandler


class FusionHandler(BaseHandler):
    """融合策略业务逻辑处理器"""

    # 搜索结果数量限制常量
    MAX_SEARCH_RESULTS = 50

    def __init__(self, context: Context, config: Dict[str, Any], recall_engine=None):
        super().__init__(context, config)
        self.recall_engine = recall_engine

    async def manage_fusion_strategy(self, strategy: str = "show", param: str = "") -> Dict[str, Any]:
        """管理检索融合策略"""
        if not self.recall_engine:
            return self.create_response(False, "回忆引擎尚未初始化")
        
        if strategy == "show":
            # 显示当前融合配置
            fusion_config = self.config.get("fusion", {})
            current_strategy = "rrf"  # 固定为RRF
            
            config_data = {
                "current_strategy": current_strategy,
                "fusion_config": fusion_config
            }
            
            return self.create_response(True, "获取融合配置成功", config_data)
        else:
            # 尝试切换策略时返回提示
            return self.create_response(False, "融合策略已固定为RRF,无需切换")

    async def test_fusion_strategy(self, query: str, k: int = 5) -> Dict[str, Any]:
        """测试融合策略效果"""
        if not self.recall_engine:
            return self.create_response(False, "回忆引擎尚未初始化")

        # 验证 k 值
        if k > self.MAX_SEARCH_RESULTS:
            return self.create_response(
                False,
                f"返回数量不能超过 {self.MAX_SEARCH_RESULTS} (当前: {k})"
            )

        if k < 1:
            return self.create_response(False, "返回数量必须至少为 1")

        try:
            # 执行搜索
            session_id = await self.context.conversation_manager.get_curr_conversation_id(None)
            from ..utils import get_persona_id
            persona_id = await get_persona_id(self.context, None)
            
            results = await self.recall_engine.recall(
                self.context, query, session_id, persona_id, k
            )
            
            if not results:
                return self.create_response(True, "未找到相关记忆", [])
            
            # 格式化结果
            formatted_results = []
            fusion_config = self.config.get("fusion", {})
            current_strategy = "rrf"  # 固定为RRF
            
            for result in results:
                metadata = self.safe_parse_metadata(result.data.get("metadata", {}))
                formatted_results.append({
                    "id": result.data['id'],
                    "similarity": result.similarity,
                    "text": result.data['text'],
                    "importance": metadata.get("importance", 0.0),
                    "event_type": metadata.get("event_type", "未知")
                })
            
            test_data = {
                "query": query,
                "strategy": current_strategy,
                "fusion_config": fusion_config,
                "results": formatted_results
            }
            
            return self.create_response(True, f"融合测试完成，找到 {len(results)} 条结果", test_data)
            
        except Exception as e:
            logger.error(f"融合策略测试失败: {e}", exc_info=True)
            return self.create_response(False, f"测试失败: {e}")

    def format_fusion_config_for_display(self, response: Dict[str, Any]) -> str:
        """格式化融合配置用于显示"""
        if not response.get("success"):
            return response.get("message", "获取失败")
        
        data = response.get("data", {})
        current_strategy = data.get("current_strategy", "rrf")
        fusion_config = data.get("fusion_config", {})
        
        response_parts = ["🔄 当前检索融合配置:"]
        response_parts.append(f"策略: {current_strategy} (固定)")
        response_parts.append("")
        response_parts.append(f"RRF参数k: {fusion_config.get('rrf_k', 60)}")
        response_parts.append("")
        response_parts.append("💡 RRF策略特点:")
        response_parts.append("• 经典的融合方法，平衡性好")
        response_parts.append("• 基于排序位置进行融合")
        response_parts.append("• 不依赖具体分数值")
        response_parts.append("• 对不同检索器的结果具有良好的兼容性")
        
        return "\n".join(response_parts)

    def format_fusion_test_for_display(self, response: Dict[str, Any]) -> str:
        """格式化融合测试结果用于显示"""
        if not response.get("success"):
            return response.get("message", "测试失败")
        
        data = response.get("data", {})
        query = data.get("query", "")
        strategy = data.get("strategy", "rrf")
        fusion_config = data.get("fusion_config", {})
        results = data.get("results", [])
        
        response_parts = [f"🎯 融合测试结果 (策略: {strategy})"]
        response_parts.append("=" * 50)
        
        for i, result in enumerate(results, 1):
            response_parts.append(f"\n{i}. [ID: {result['id']}] 分数: {result['similarity']:.4f}")
            response_parts.append(f"   重要性: {result['importance']:.3f} | 类型: {result['event_type']}")
            response_parts.append(f"   内容: {result['text'][:100]}{'...' if len(result['text']) > 100 else ''}")
        
        response_parts.append("\n" + "=" * 50)
        response_parts.append(f"💡 当前融合配置:")
        response_parts.append(f"   策略: {strategy}")
        response_parts.append(f"   RRF-k: {fusion_config.get('rrf_k', 60)}")
        
        return "\n".join(response_parts)