# -*- coding: utf-8 -*-
"""
测试模块配置文件
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 测试配置
TEST_CONFIG = {
    "session_manager": {
        "max_sessions": 100,
        "session_ttl": 3600
    },
    "recall_engine": {
        "retrieval_mode": "hybrid",
        "top_k": 5,
        "recall_strategy": "weighted"
    },
    "reflection_engine": {
        "summary_trigger_rounds": 10,
        "importance_threshold": 0.5
    },
    "forgetting_agent": {
        "enabled": True,
        "check_interval_hours": 24,
        "retention_days": 90
    },
    "timezone_settings": {
        "timezone": "Asia/Shanghai"
    },
    "fusion": {
        "strategy": "rrf",
        "rrf_k": 60,
        "dense_weight": 0.7,
        "sparse_weight": 0.3
    }
}