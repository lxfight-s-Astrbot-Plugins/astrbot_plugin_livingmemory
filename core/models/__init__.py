# -*- coding: utf-8 -*-
"""
Models module - 定义数据模型
"""

from .memory_models import (
    Memory,
    Metadata,
    AccessInfo,
    EmotionalValence,
    UserFeedback,
    CommunityInfo,
    EventEntity,
    Entity,
    KnowledgeGraphPayload,
    LinkedMedia,
)

# 为反思引擎添加必要的数据模型
from pydantic import BaseModel, Field
from typing import List, Optional


class MemoryEvent(BaseModel):
    """单个记忆事件"""

    memory_content: str = Field(..., description="记忆内容")
    event_type: str = Field(default="OTHER", description="事件类型")
    importance: Optional[float] = Field(default=None, description="重要性评分")


class _LLMExtractionEventList(BaseModel):
    """LLM 提取的事件列表"""

    events: List[MemoryEvent] = Field(
        default_factory=list, description="提取的事件列表"
    )


class _LLMScoreEvaluation(BaseModel):
    """LLM 评分结果"""

    score: float = Field(..., ge=0.0, le=1.0, description="重要性评分")


__all__ = [
    "Memory",
    "Metadata",
    "AccessInfo",
    "EmotionalValence",
    "UserFeedback",
    "CommunityInfo",
    "EventEntity",
    "Entity",
    "KnowledgeGraphPayload",
    "LinkedMedia",
    "MemoryEvent",
    "_LLMExtractionEventList",
    "_LLMScoreEvaluation",
]
