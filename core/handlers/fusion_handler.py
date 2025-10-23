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
            current_strategy = fusion_config.get("strategy", "rrf")
            
            config_data = {
                "current_strategy": current_strategy,
                "fusion_config": fusion_config
            }
            
            return self.create_response(True, "获取融合配置成功", config_data)
            
        elif strategy in ["rrf", "hybrid_rrf", "weighted", "convex", "interleave", 
                         "rank_fusion", "score_fusion", "cascade", "adaptive"]:
            
            # 更新融合策略
            if "fusion" not in self.config:
                self.config["fusion"] = {}
            
            old_strategy = self.config["fusion"].get("strategy", "rrf")
            self.config["fusion"]["strategy"] = strategy
            
            # 处理参数
            param_result = await self._process_fusion_param(param, strategy)
            if not param_result["success"]:
                return param_result
            
            # 更新 RecallEngine 中的融合配置
            update_result = await self._update_recall_engine_fusion_config(strategy, self.config["fusion"])
            if not update_result["success"]:
                return update_result
            
            return self.create_response(True, f"融合策略已从 '{old_strategy}' 更新为 '{strategy}'{f' (参数: {param})' if param else ''}")
            
        else:
            return self.create_response(False, "不支持的融合策略。使用 show 查看可用选项。")

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
            current_strategy = fusion_config.get("strategy", "rrf")
            
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

    async def _process_fusion_param(self, param: str, strategy: str) -> Dict[str, Any]:
        """处理融合策略参数"""
        if not param or "=" not in param:
            return self.create_response(True, "无参数需要处理")
        
        try:
            key, value = param.split("=", 1)
            key = key.strip()
            value = value.strip()
            
            # 验证参数名
            valid_params = {
                "dense_weight", "sparse_weight", "rrf_k", "convex_lambda",
                "interleave_ratio", "rank_bias_factor", "diversity_bonus"
            }
            
            if key not in valid_params:
                return self.create_response(False, f"无效的参数名: {key}。支持的参数: {', '.join(sorted(valid_params))}")
            
            # 验证参数值
            try:
                if key in ["dense_weight", "sparse_weight", "convex_lambda", "interleave_ratio", "rank_bias_factor", "diversity_bonus"]:
                    param_value = float(value)
                else:
                    param_value = int(value)
            except ValueError:
                return self.create_response(False, f"参数 {key} 的值类型无效: {value}")
            
            # 参数范围和约束检查
            param_constraints = {
                "dense_weight": (0.0, 1.0, "必须在 0.0-1.0 范围内"),
                "sparse_weight": (0.0, 1.0, "必须在 0.0-1.0 范围内"),
                "convex_lambda": (0.0, 1.0, "必须在 0.0-1.0 范围内"),
                "interleave_ratio": (0.0, 1.0, "必须在 0.0-1.0 范围内"),
                "rank_bias_factor": (0.0, 1.0, "必须在 0.0-1.0 范围内"),
                "diversity_bonus": (0.0, 1.0, "必须在 0.0-1.0 范围内"),
                "rrf_k": (1, 1000, "必须是正整数")
            }
            
            if key in param_constraints:
                min_val, max_val, error_msg = param_constraints[key]
                if not min_val <= param_value <= max_val:
                    return self.create_response(False, f"参数 {key} {error_msg}")
            
            # 策略特定参数验证
            strategy_params = {
                "rrf": ["rrf_k"],
                "hybrid_rrf": ["rrf_k", "diversity_bonus"],
                "weighted": ["dense_weight", "sparse_weight"],
                "convex": ["dense_weight", "sparse_weight", "convex_lambda"],
                "interleave": ["interleave_ratio"],
                "rank_fusion": ["dense_weight", "sparse_weight", "rank_bias_factor"],
                "score_fusion": ["dense_weight", "sparse_weight"],
                "cascade": ["dense_weight", "sparse_weight"],
                "adaptive": ["dense_weight", "sparse_weight"]
            }
            
            if strategy in strategy_params and key not in strategy_params[strategy]:
                return self.create_response(False, f"参数 {key} 不适用于策略 {strategy}")
            
            # 权重和检查(对于需要权重的策略)
            if key in ["dense_weight", "sparse_weight"]:
                # 检查策略是否使用权重参数
                if strategy in ["weighted", "convex", "rank_fusion", "score_fusion", "cascade", "adaptive"]:
                    other_key = "sparse_weight" if key == "dense_weight" else "dense_weight"
                    other_value = self.config["fusion"].get(other_key, 0.3 if other_key == "sparse_weight" else 0.7)
                    total_weight = param_value + other_value

                    if total_weight > 1.0:
                        return self.create_response(False, f"权重总和不能超过 1.0 (当前总和: {total_weight:.2f})")
            
            self.config["fusion"][key] = param_value
            logger.info(f"更新融合参数 {key} = {param_value}")
            
            return self.create_response(True, "参数处理成功")
            
        except Exception as e:
            return self.create_response(False, f"参数解析错误: {e}")

    async def _update_recall_engine_fusion_config(self, strategy: str, fusion_config: Dict[str, Any]) -> Dict[str, Any]:
        """更新RecallEngine的融合配置"""
        try:
            if hasattr(self.recall_engine, 'result_fusion'):
                self.recall_engine.update_fusion_config(strategy, fusion_config)
            else:
                logger.warning("RecallEngine 没有 result_fusion 属性，跳过更新")
            
            return self.create_response(True, "融合配置更新成功")
        except AttributeError:
            # 如果 RecallEngine 没有 update_fusion_config 方法，则直接更新属性
            try:
                if hasattr(self.recall_engine, 'result_fusion'):
                    fusion_obj = self.recall_engine.result_fusion
                    fusion_obj.strategy = strategy
                    fusion_obj.config = fusion_config
                    
                    # 更新融合器的参数
                    fusion_obj.dense_weight = fusion_config.get("dense_weight", 0.7)
                    fusion_obj.sparse_weight = fusion_config.get("sparse_weight", 0.3)
                    fusion_obj.rrf_k = fusion_config.get("rrf_k", 60)
                    fusion_obj.convex_lambda = fusion_config.get("convex_lambda", 0.5)
                    fusion_obj.interleave_ratio = fusion_config.get("interleave_ratio", 0.5)
                    fusion_obj.rank_bias_factor = fusion_config.get("rank_bias_factor", 0.1)
                
                return self.create_response(True, "融合配置更新成功")
            except Exception as e:
                logger.error(f"更新融合配置时出错: {e}")
                return self.create_response(False, f"配置已更新，但引擎同步可能失败: {e}")
        except Exception as e:
            logger.error(f"更新融合配置时出错: {e}")
            return self.create_response(False, f"更新融合配置失败: {e}")

    def format_fusion_config_for_display(self, response: Dict[str, Any]) -> str:
        """格式化融合配置用于显示"""
        if not response.get("success"):
            return response.get("message", "获取失败")
        
        data = response.get("data", {})
        current_strategy = data.get("current_strategy", "rrf")
        fusion_config = data.get("fusion_config", {})
        
        response_parts = ["🔄 当前检索融合配置:"]
        response_parts.append(f"策略: {current_strategy}")
        response_parts.append("")
        
        if current_strategy in ["rrf", "hybrid_rrf"]:
            response_parts.append(f"RRF参数k: {fusion_config.get('rrf_k', 60)}")
            if current_strategy == "hybrid_rrf":
                response_parts.append(f"多样性奖励: {fusion_config.get('diversity_bonus', 0.1)}")
        
        if current_strategy in ["weighted", "convex", "rank_fusion", "score_fusion"]:
            response_parts.append(f"密集权重: {fusion_config.get('dense_weight', 0.7)}")
            response_parts.append(f"稀疏权重: {fusion_config.get('sparse_weight', 0.3)}")
        
        if current_strategy == "convex":
            response_parts.append(f"凸组合λ: {fusion_config.get('convex_lambda', 0.5)}")
        
        if current_strategy == "interleave":
            response_parts.append(f"交替比例: {fusion_config.get('interleave_ratio', 0.5)}")
        
        if current_strategy == "rank_fusion":
            response_parts.append(f"排序偏置: {fusion_config.get('rank_bias_factor', 0.1)}")
        
        response_parts.append("")
        response_parts.append("💡 各策略特点:")
        response_parts.append("• rrf: 经典方法，平衡性好")
        response_parts.append("• hybrid_rrf: 动态调整，适应查询类型")
        response_parts.append("• weighted: 简单加权，可解释性强")
        response_parts.append("• convex: 凸组合，数学严格")
        response_parts.append("• interleave: 交替选择，保证多样性")
        response_parts.append("• rank_fusion: 基于排序位置")
        response_parts.append("• score_fusion: Borda Count投票")
        response_parts.append("• cascade: 稀疏初筛+密集精排")
        response_parts.append("• adaptive: 根据查询自适应")
        
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
        if strategy in ["rrf", "hybrid_rrf"]:
            response_parts.append(f"   RRF-k: {fusion_config.get('rrf_k', 60)}")
        if strategy in ["weighted", "convex"]:
            response_parts.append(f"   密集权重: {fusion_config.get('dense_weight', 0.7)}")
            response_parts.append(f"   稀疏权重: {fusion_config.get('sparse_weight', 0.3)}")
        
        return "\n".join(response_parts)