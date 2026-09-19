"""
constants.py - 插件使用的常量
"""

# 注入到 System Prompt 的记忆头尾格式
MEMORY_INJECTION_HEADER = "<RAG-Faiss-Memory>"
MEMORY_INJECTION_FOOTER = "</RAG-Faiss-Memory>"

# 伪造工具调用注入相关常量（fake_tool_call 注入方式已废弃，仅保留
# FAKE_TOOL_CALL_ID_PREFIX 供 legacy 残留清理识别使用）
FAKE_TOOL_CALL_NAME = "recall_long_term_memory"  # 复用已注册的工具名
FAKE_TOOL_CALL_ID_PREFIX = "fake_recall_"  # ID 前缀，用于清理时识别伪造消息

# Beta 自主回忆规则的专用边界标记（与记忆注入头尾区分，用于重复钩子去重）
AGENT_RECALL_POLICY_HEADER = "<Agent-Recall-Policy>"
AGENT_RECALL_POLICY_FOOTER = "</Agent-Recall-Policy>"
