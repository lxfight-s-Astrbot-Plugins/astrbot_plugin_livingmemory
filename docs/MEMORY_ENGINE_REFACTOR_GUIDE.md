# Memory Engine 重构实施指南

## 📋 概述

本指南提供了 memory_engine.py 重构的详细步骤，确保每一步都可验证、可回滚。

**当前状态**:
- 文件：`core/managers/memory_engine.py`
- 行数：2342行
- 方法数：40个
- 测试：554个全部通过

**目标**:
- 拆分为6个子模块
- 每个模块 < 500行
- 使用Facade模式保持接口不变
- 所有测试持续通过

---

## 🚀 重构步骤

### 步骤0: 准备工作 ✅

```bash
# 已完成
git checkout master
git pull origin master
git checkout -b feature/refactor-memory-engine
pytest tests/ -q  # 确认554个测试通过
mkdir -p core/managers/memory_engine_modules
```

---

### 步骤1: 提取 Repair 模块 (~400行)

**职责**: 写操作修复机制（崩溃恢复）

**包含方法** (10个):
```python
async def _create_write_ops_table()          # 行257-282
async def _start_write_op()                  # 行284-316
async def _advance_write_op()                # 行318-374
def _serialize_atom_for_repair()             # 行443-471
def _deserialize_atom_from_repair()          # 行473-521
async def _repair_incomplete_write_ops()     # 行523-589
async def _repair_add_write_op()             # 行591-681
async def _repair_delete_write_op()          # 行683-708
async def _repair_batch_delete_write_op()    # 行710-749
def _safe_json_dict()                        # 行796-807
```

**创建文件**: `core/managers/memory_engine_modules/repair.py`

**模板**:
```python
"""
Memory Engine Repair Module
Handles write-operation repair mechanism for crash recovery
"""

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger

class MemoryEngineRepair:
    """
    写操作修复机制
    
    在崩溃后自动恢复未完成的写操作
    """
    
    async def _create_write_ops_table(self) -> None:
        """Create the resumable write-operation log."""
        # 复制 行257-282
        ...
    
    # ... 其他方法
```

**验证**:
```bash
# 创建文件后
pytest tests/ -q
# 预期：554 passed
```

---

### 步骤2: 提取 Batch 模块 (~400行)

**职责**: 批量操作和图谱重建

**包含方法** (5个):
```python
async def batch_delete_memories()                    # 行1796-1912
async def _delete_document_indexes_for_batch()       # 行751-785
async def _delete_graph_and_atoms_for_batch()        # 行787-795
async def rebuild_graph_index()                      # 行1486-1531
def _normalize_batch_metadata()                      # 行2327-2342
```

**创建文件**: `core/managers/memory_engine_modules/batch.py`

**依赖**:
- self.bm25_retriever
- self.vector_retriever
- self.graph_memory_manager (可选)
- self.atom_lifecycle_manager (可选)

**验证**: `pytest tests/ -q`

---

### 步骤3: 提取 Maintenance 模块 (~400行)

**职责**: 维护、清理、统计

**包含方法** (5个):
```python
async def apply_daily_decay()                    # 行1546-1628
async def cleanup_old_memories()                 # 行1914-2011
async def maintain_storage()                     # 行2269-2325
async def get_statistics()                       # 行2143-2267
async def _migrate_session_data_if_needed()      # 行2013-2141
```

**创建文件**: `core/managers/memory_engine_modules/maintenance.py`

**验证**: `pytest tests/ -q`

---

### 步骤4: 提取 Retrieval 模块 (~400行)

**职责**: 检索和搜索（含缓存）

**包含方法** (7个):
```python
async def search_memories()                  # 行1147-1207
async def get_session_memories()             # 行1701-1794
def _normalize_cache_query()                 # 行376-377
def _search_cache_key()                      # 行379-396
def _get_cached_search_results()             # 行398-419
def _set_cached_search_results()             # 行421-436
def _invalidate_search_cache()               # 行438-441
```

**创建文件**: `core/managers/memory_engine_modules/retrieval.py`

**共享状态**:
```python
# 需要访问基类的缓存
self._search_cache
self._search_cache_generation
```

**验证**: `pytest tests/ -q`

---

### 步骤5: 提取 CRUD 模块 (~500行)

**职责**: 核心CRUD操作

**包含方法** (7个):
```python
async def add_memory()                       # 行968-1145
async def get_memory()                       # 行1209-1239
async def update_memory()                    # 行1241-1394
async def delete_memory()                    # 行1396-1484
async def update_importance()                # 行1533-1544
async def update_access_time()               # 行1630-1640
async def _update_access_time_internal()     # 行1642-1699
```

**创建文件**: `core/managers/memory_engine_modules/crud.py`

**关键逻辑**:
- add_memory: 依赖 repair 模块的 _start_write_op, _advance_write_op
- delete_memory: 依赖 repair 模块

**验证**: `pytest tests/ -q`

---

### 步骤6: 创建 Base 模块 (~350行)

**职责**: 初始化、配置管理、生命周期

**包含方法** (6个):
```python
def __init__()                                   # 行80-152
async def initialize()                           # 行154-234
async def close()                                # 行236-249
def _create_tracked_task()                       # 行251-255
async def _create_tables()                       # 行809-949
async def _drop_legacy_documents_fts_triggers()  # 行951-966
```

**创建文件**: `core/managers/memory_engine_modules/base.py`

**关键点**:
- __init__ 定义所有共享状态
- initialize() 初始化所有组件
- 所有子模块通过继承访问共享状态

**验证**: `pytest tests/ -q`

---

### 步骤7: 创建 Facade

**文件**: `core/managers/memory_engine.py`

**内容**:
```python
"""
统一记忆引擎 - Facade

整合所有子模块，对外提供统一接口。
"""

from .memory_engine_modules.base import MemoryEngineBase
from .memory_engine_modules.crud import MemoryEngineCRUD
from .memory_engine_modules.retrieval import MemoryEngineRetrieval
from .memory_engine_modules.batch import MemoryEngineBatch
from .memory_engine_modules.maintenance import MemoryEngineMaintenance
from .memory_engine_modules.repair import MemoryEngineRepair


class MemoryEngine(
    MemoryEngineBase,
    MemoryEngineCRUD,
    MemoryEngineRetrieval,
    MemoryEngineBatch,
    MemoryEngineMaintenance,
    MemoryEngineRepair,
):
    """
    统一记忆引擎
    
    多重继承整合所有子模块，对外提供统一接口。
    使用MRO（Method Resolution Order）确保方法正确解析。
    """
    
    # 文档字符串保留原样
    __doc__ = """
    统一记忆引擎
    
    整合BM25检索、向量检索和混合检索,提供完整的记忆管理接口。
    
    主要功能:
    1. 记忆CRUD操作(添加、检索、更新、删除)
    2. 自动化记忆整理和清理
    3. 重要性评估和时间衰减
    4. 会话隔离和统计
    """
```

**验证**:
```bash
pytest tests/ -q
# 预期：554 passed，无任何失败

# 检查MRO
python3 -c "from core.managers.memory_engine import MemoryEngine; print(MemoryEngine.__mro__)"
```

---

## ✅ 验证清单

每完成一个步骤后，运行：

```bash
# 1. 运行所有测试
pytest tests/ -v

# 2. 检查覆盖率（应保持76%）
pytest tests/ --cov=core --cov-report=term | grep TOTAL

# 3. 检查import
python3 -c "from core.managers.memory_engine import MemoryEngine; print('OK')"

# 4. 提交
git add .
git commit -m "refactor(memory_engine): 提取 xxx 模块"
```

---

## 🎯 共享状态处理

### 在 Base 类中定义：
```python
class MemoryEngineBase:
    def __init__(...):
        # 数据库连接
        self.db_connection = None
        
        # 组件
        self.text_processor = None
        self.bm25_retriever = None
        self.vector_retriever = None
        # ... 其他组件
        
        # 缓存
        self._search_cache = OrderedDict()
        self._search_cache_generation = 0
        
        # 配置
        self.config = config or {}
        
        # 后台任务
        self._pending_tasks = set()
```

### 子类访问：
```python
class MemoryEngineCRUD(MemoryEngineBase):
    async def add_memory(...):
        # 直接访问
        await self.vector_retriever.add_document(...)
        self._invalidate_search_cache()  # 调用其他模块的方法
```

---

## ⚠️ 常见陷阱

### 1. 循环导入
❌ **错误**:
```python
# crud.py
from .repair import MemoryEngineRepair

class MemoryEngineCRUD(MemoryEngineRepair):  # 循环依赖！
    ...
```

✅ **正确**:
```python
# crud.py
class MemoryEngineCRUD:  # 不继承，通过Facade组合
    async def add_memory(...):
        # 调用self的方法（由Facade提供）
        op_id = await self._start_write_op(...)
```

### 2. 方法调用
❌ **错误**:
```python
# 在子模块中直接调用其他模块
from .batch import MemoryEngineBatch
result = MemoryEngineBatch._delete_graph_and_atoms_for_batch(...)
```

✅ **正确**:
```python
# 通过self调用（Facade会解析）
result = await self._delete_graph_and_atoms_for_batch(...)
```

### 3. 类型注解
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api import Context
    
class MemoryEngineBase:
    def __init__(self, context: "Context", ...):  # 使用字符串避免循环导入
        ...
```

---

## 📊 预期结果

### 文件结构
```
core/managers/
├── memory_engine.py           # Facade (~100行)
└── memory_engine_modules/
    ├── __init__.py
    ├── base.py                # ~350行
    ├── crud.py                # ~500行
    ├── retrieval.py           # ~400行
    ├── batch.py               # ~400行
    ├── maintenance.py         # ~400行
    └── repair.py              # ~400行
```

### 测试结果
```
554 passed, 0 failed, 0 skipped
Coverage: 76%
```

---

## 🚀 执行时间估算

| 步骤 | 预计时间 | 累计时间 |
|------|---------|---------|
| 步骤1: Repair | 1.5h | 1.5h |
| 步骤2: Batch | 1h | 2.5h |
| 步骤3: Maintenance | 1h | 3.5h |
| 步骤4: Retrieval | 1.5h | 5h |
| 步骤5: CRUD | 2h | 7h |
| 步骤6: Base | 1.5h | 8.5h |
| 步骤7: Facade | 0.5h | 9h |
| **总计** | **~9小时** | - |

---

## 📝 提交信息模板

```bash
# 步骤1
git commit -m "refactor(memory_engine): 提取 repair 模块

- 创建 memory_engine_modules/repair.py
- 包含10个写操作修复相关方法
- 测试：554 passed"

# 步骤2
git commit -m "refactor(memory_engine): 提取 batch 模块

- 创建 memory_engine_modules/batch.py
- 包含5个批量操作相关方法
- 测试：554 passed"

# ... 以此类推

# 最后
git commit -m "refactor(memory_engine): 完成模块化重构

- 拆分为6个子模块，每个<500行
- 使用Facade模式保持接口不变
- 所有554个测试通过
- 覆盖率保持76%"
```

---

**最后更新**: 2026-06-08  
**作者**: Claude Code  
**分支**: `feature/refactor-memory-engine`  
**状态**: 就绪，可开始实施
