# Event Handler 重构 - 快速启动指南

## 🚀 当前状态

**分支**: `feature/refactor-event-handler`  
**已完成**: 创建子模块目录  
**下一步**: 按步骤提取模块

---

## ⚡ 快速执行（推荐使用脚本）

### 步骤1: 提取 MessageUtils 模块

创建文件 `core/event_handler_modules/message_utils.py`:

```python
"""消息工具模块 - 去重、提取、限制管理"""

import asyncio
import hashlib
import time
from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest

if TYPE_CHECKING:
    pass


class MessageUtils:
    """消息去重、内容提取、限制管理"""

    # 将以下方法从 event_handler.py 复制过来：
    # - _build_dedup_key (行1073-1089)
    # - _is_duplicate_message (行1091-1105)
    # - _mark_message_processed (行1107-1116)
    # - _extract_message_content (行1118-1199)
    # - _get_event_message_str (行1201-1216)
    # - _enforce_message_limit (行1218-1303)
```

**提取命令**:
```bash
# 使用sed提取方法
sed -n '1073,1303p' core/event_handler.py > /tmp/message_utils_methods.txt
# 手动组装成完整的类
```

**验证**:
```bash
pytest tests/test_event_handler*.py -v
```

---

## 📝 完整步骤清单

- [ ] **步骤1**: MessageUtils (~250行)
- [ ] **步骤2**: GroupCapture (~150行)
- [ ] **步骤3**: MemoryRecall (~350行)
- [ ] **步骤4**: MemoryReflection (~450行)
- [ ] **步骤5**: SessionManager (~100行)
- [ ] **步骤6**: 创建Facade
- [ ] **步骤7**: 删除原文件中的方法
- [ ] **步骤8**: 最终验证

---

## ⚠️ 重要提示

1. **每步都验证测试**: `pytest tests/ -q`
2. **小步提交**: 每完成一个模块就commit
3. **保持共享状态**: 通过self访问
4. **避免循环导入**: 子模块不相互导入

---

## 🎯 预期结果

```
core/event_handler_modules/
├── __init__.py              ✅ 已创建
├── message_utils.py         ⏳ 下一步
├── group_capture.py
├── memory_recall.py
├── memory_reflection.py
└── session_manager.py

core/event_handler.py        (Facade, ~80行)
```

---

**下次session从这里继续！**
