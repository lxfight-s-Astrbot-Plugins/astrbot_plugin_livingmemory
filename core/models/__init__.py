# -*- coding: utf-8 -*-
"""
core.models 包导出。
"""

from .memory_models import (
    Memory,
    Metadata,
    AccessInfo,
    EmotionalValence,
    UserFeedback,
    CommunityInfo,
    LinkedMedia,
    KnowledgeGraphPayload,
    EventEntity,
    Entity,
)
from .models import (
    MemoryEvent as LegacyMemoryEvent,
    MemoryEventList,
    _LLMExtractionEvent,
    _LLMExtractionEventList,
    _LLMScoreEvaluation,
)

# 导入新的会话管理模型
from ..conversation_models import (
    Message,
    Session,
    MemoryEvent as ConversationMemoryEvent,
    serialize_to_json,
    deserialize_from_json,
)

__all__ = [
    # 旧有模型
    "Memory",
    "Metadata",
    "AccessInfo",
    "EmotionalValence",
    "UserFeedback",
    "CommunityInfo",
    "LinkedMedia",
    "KnowledgeGraphPayload",
    "EventEntity",
    "Entity",
    "LegacyMemoryEvent",
    "MemoryEventList",
    "_LLMExtractionEvent",
    "_LLMExtractionEventList",
    "_LLMScoreEvaluation",
    # 新增会话管理模型
    "Message",
    "Session",
    "ConversationMemoryEvent",
    "serialize_to_json",
    "deserialize_from_json",
]
