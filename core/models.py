# -*- coding: utf-8 -*-
"""
models.py - 插件的核心数据模型
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

# --- 公开的数据模型 ---


class EventType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    OPINION = "opinion"
    RELATIONSHIP = "relationship"
    OTHER = "other"


class Entity(BaseModel):
    name: str = Field(..., description="实体名称")
    type: str = Field(..., description="实体类型")


class MemoryEvent(BaseModel):
    # --- 系统生成字段 ---
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="事件创建的UTC时间戳",
    )

    # --- 系统生成字段 ---
    id: Optional[int] = Field(None, description="记忆的唯一整数ID，由存储后端生成")

    # --- LLM 生成字段 (第一阶段) ---
    temp_id: str = Field(..., description="由LLM生成的临时唯一ID，用于评分匹配")
    memory_content: str = Field(..., description="对事件的简洁、客观的描述")
    event_type: EventType = Field(default=EventType.OTHER, description="事件的分类")
    entities: List[Entity] = Field(
        default_factory=list, description="事件中涉及的关键实体"
    )

    # --- LLM 生成字段 (第二阶段) ---
    importance_score: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="记忆的重要性评分 (0.0-1.0)"
    )

    # --- 系统关联字段 ---
    related_event_ids: List[int] = Field(
        default_factory=list, description="与此事件相关的其他事件ID"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="用于存储其他附加信息的灵活字段"
    )


class MemoryEventList(BaseModel):
    events: List[MemoryEvent]


# --- 用于生成 Prompt Schema 的私有模型 ---


# 用于第一阶段：事件提取
class _LLMExtractionEvent(BaseModel):
    temp_id: str = Field(
        ...,
        description="由LLM或系统生成的临时唯一ID",
    )
    memory_content: str = Field(...)
    event_type: EventType = Field(default=EventType.OTHER)
    entities: List[Entity] = Field(default_factory=list)
    related_event_ids: List[int] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class _LLMExtractionEventList(BaseModel):
    events: List[_LLMExtractionEvent]


# 用于第二阶段：评分
class _LLMScoreEvaluation(BaseModel):
    scores: Dict[str, float] = Field(
        ...,
        description="一个字典，key是事件的临时ID (temp_id)，value是对应的0.0-1.0的重要性分数",
    )
