# Event Handler 重构实施指南

## 📋 概述

本指南提供了 event_handler.py 重构的详细步骤。

**当前状态**:
- 文件：`core/event_handler.py`
- 行数：1324行
- 方法数：17个
- 测试：44个相关测试

**目标**:
- 拆分为5个子模块
- 每个模块 < 300行
- 使用Facade模式保持接口不变

---

## 🚀 重构步骤

### 步骤1: 提取 MessageUtils 模块 (~250行)

**职责**: 消息去重、内容提取、限制管理

**包含方法** (6个):
```python
async def _build_dedup_key()           # 行1073-1089
async def _is_duplicate_message()      # 行1091-1105
async def _mark_message_processed()    # 行1107-1116
async def _extract_message_content()   # 行1118-1199
async def _get_event_message_str()     # 行1201-1216
async def _enforce_message_limit()     # 行1218-1303
```

**创建文件**: `core/event_handler_modules/message_utils.py`

**共享状态**:
```python
self._message_dedup_cache: dict[str, float]
self._dedup_cache_max_size = 1000
self._dedup_cache_ttl = 300
```

---

### 步骤2: 提取 GroupCapture 模块 (~150行)

**职责**: 群聊全量消息捕获

**包含方法** (1个):
```python
async def handle_all_group_messages()  # 行81-140
```

**创建文件**: `core/event_handler_modules/group_capture.py`

---

### 步骤3: 提取 MemoryRecall 模块 (~350行)

**职责**: 记忆召回和注入

**包含方法** (3个):
```python
async def handle_memory_recall()                      # 行142-375
def _remove_injected_memories_from_context()          # 行831-1010
def _remove_fake_tool_call_from_context()             # 行1012-1071
```

**创建文件**: `core/event_handler_modules/memory_recall.py`

---

### 步骤4: 提取 MemoryReflection 模块 (~450行)

**职责**: 记忆反思和存储

**包含方法** (4个):
```python
async def handle_memory_reflection()   # 行377-615
def _on_storage_task_done()            # 行617-631
async def _storage_task()              # 行633-794
async def _record_pending_summary()    # 行796-829
```

**创建文件**: `core/event_handler_modules/memory_reflection.py`

**共享状态**:
```python
self._storage_tasks: set[asyncio.Task]
self._storage_sessions_inflight: set[str]
self._storage_state_lock: asyncio.Lock
```

---

### 步骤5: 提取 SessionManager 模块 (~100行)

**职责**: 会话管理和清理

**包含方法** (2个):
```python
async def handle_session_reset()  # 行1305-1314
async def shutdown()              # 行1316-1324
```

**创建文件**: `core/event_handler_modules/session_manager.py`

---

### 步骤6: 创建 Facade

**文件**: `core/event_handler.py`

```python
"""事件处理器"""

from .event_handler_modules.message_utils import MessageUtils
from .event_handler_modules.group_capture import GroupCapture
from .event_handler_modules.memory_recall import MemoryRecall
from .event_handler_modules.memory_reflection import MemoryReflection
from .event_handler_modules.session_manager import SessionManager


class EventHandler(
    MessageUtils,
    GroupCapture,
    MemoryRecall,
    MemoryReflection,
    SessionManager,
):
    """事件处理器 - Facade"""
    
    def __init__(...):
        # 初始化共享状态
        self._message_dedup_cache = {}
        self._storage_tasks = set()
        self._storage_sessions_inflight = set()
        self._storage_state_lock = asyncio.Lock()
        ...
```

---

## ✅ 验证

每步完成后：
```bash
pytest tests/test_event_handler*.py -v
pytest tests/ -q  # 全量测试
```

---

**预计时间**: 4-6小时  
**分支**: `feature/refactor-event-handler`
