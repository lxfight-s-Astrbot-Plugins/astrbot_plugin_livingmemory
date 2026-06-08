# LivingMemory 插件重构计划

## 📋 概述

本文档记录了 LivingMemory 插件第二阶段的代码重构计划，旨在将大型文件拆分为职责清晰的小模块，提升代码可维护性。

## 🎯 重构目标

- **降低单文件复杂度**：将超大文件（1000+行）拆分为 < 400行的小模块
- **职责单一原则**：每个模块负责一个明确的功能域
- **保持向后兼容**：使用 Facade 模式，对外接口不变
- **测试零回归**：每步重构后确保所有测试通过

## 📊 待重构文件清单

| 文件 | 当前行数 | 方法数 | 目标模块数 | 优先级 |
|------|---------|--------|-----------|--------|
| `memory_engine.py` | 2342 | 40 | 6 | P1 |
| `event_handler.py` | 1324 | 17 | 5 | P2 |
| `page_api.py` | 1210 | ~30 | 5 | P3 |

---

## 🔧 重构1: memory_engine.py (2342行 → 6模块)

### 当前问题
- 单文件过大，难以导航和理解
- 职责混杂：CRUD、检索、维护、修复混在一起
- 40个方法，职责边界不清

### 拆分方案

#### 模块1: `memory_engine_base.py` (~350行)
**职责**: 引擎初始化、配置管理、生命周期

**方法列表**:
```python
class MemoryEngineBase:
    def __init__(...)
    async def initialize()
    async def close()
    def _create_tracked_task()
    async def _create_tables()
    async def _drop_legacy_documents_fts_triggers()
```

#### 模块2: `memory_engine_crud.py` (~500行)
**职责**: 核心CRUD操作 (add/get/update/delete/update_importance/update_access_time)

#### 模块3: `memory_engine_retrieval.py` (~400行)
**职责**: 检索和搜索（含缓存机制）

#### 模块4: `memory_engine_batch.py` (~400行)
**职责**: 批量操作和图谱重建

#### 模块5: `memory_engine_maintenance.py` (~400行)
**职责**: 维护、清理、统计

#### 模块6: `memory_engine_repair.py` (~400行)
**职责**: 写操作修复机制（崩溃恢复）

---

## 🔧 重构2: event_handler.py (1324行 → 5模块)

### 拆分方案

#### 模块1: `message_utils.py` (~250行)
消息去重、内容提取、限制管理

#### 模块2: `group_capture.py` (~150行)
群聊全量消息捕获

#### 模块3: `memory_recall.py` (~350行)
记忆召回和注入

#### 模块4: `memory_reflection.py` (~450行)
记忆反思和存储

#### 模块5: `session_manager.py` (~100行)
会话管理和清理

---

## 🔧 重构3: page_api.py (1210行 → 5模块)

### 拆分方案
- `api_base.py` - 初始化和公共工具
- `memory_routes.py` - 记忆CRUD路由
- `graph_routes.py` - 图谱路由
- `statistics_routes.py` - 统计路由
- `recall_debug_routes.py` - 召回调试路由

---

## 🚀 实施步骤

### 使用 Facade 模式确保零破坏
```python
# 最终 memory_engine.py
class MemoryEngine(
    MemoryEngineBase,
    MemoryEngineCRUD,
    MemoryEngineRetrieval,
    MemoryEngineBatch,
    MemoryEngineMaintenance,
    MemoryEngineRepair,
):
    """统一记忆引擎 - Facade"""
    pass
```

### 渐进式重构流程
1. 创建子模块目录 `memory_engine_modules/`
2. 按顺序提取模块：repair → batch → maintenance → retrieval → crud → base
3. **每个模块提取后立即运行全部测试**
4. 创建 Facade 类整合
5. 提交重构

---

## ✅ 成功标准

- [ ] 所有554个测试通过
- [ ] 每个文件 < 500行
- [ ] 无 import 循环依赖
- [ ] 无性能回归

---

**更新日期**: 2026-06-08  
**作者**: Claude Code  
**状态**: 待实施
