<div align="center">

[中文](ARCHITECTURE.md) | [English](ARCHITECTURE_en.md) | [Русский](ARCHITECTURE_ru.md)

</div>

# LivingMemory 架构文档

**版本**: v2.2.10
**更新日期**: 2026-04-28

---

## 目录结构

```
astrbot_plugin_livingmemory/
├── main.py                          # 插件主文件
├── core/                            # 核心模块
│   ├── __init__.py                  # 核心模块导出
│   ├── plugin_initializer.py       # 插件初始化器
│   ├── event_handler.py            # 事件处理器
│   ├── command_handler.py          # 命令处理器
│   ├── tools/                      # Agent/LLM 工具层
│   │
│   ├── base/                        # 基础组件层
│   │   ├── __init__.py
│   │   ├── exceptions.py            # 异常定义
│   │   ├── constants.py             # 常量定义
│   │   ├── config_manager.py        # 配置管理器
│   │   └── config_validator.py      # 配置验证器
│   │
│   ├── models/                      # 数据模型层
│   │   ├── __init__.py
│   │   └── conversation_models.py   # 会话数据模型
│   │
│   ├── managers/                    # 管理器层
│   │   ├── __init__.py
│   │   ├── conversation_manager.py  # 会话管理器
│   │   └── memory_engine.py         # 记忆引擎
│   │
│   ├── processors/                  # 处理器层
│   │   ├── __init__.py
│   │   ├── memory_processor.py      # 记忆处理器
│   │   ├── text_processor.py        # 文本处理器
│   │   ├── chatroom_parser.py       # 聊天室解析器
│   │   └── message_utils.py         # 消息工具
│   │
│   ├── validators/                  # 验证器层
│   │   ├── __init__.py
│   │   └── index_validator.py       # 索引验证器
│   │
│   ├── retrieval/                   # 检索系统层
│   │   ├── __init__.py
│   │   ├── dual_route_retriever.py  # 文档路/图路双路融合
│   │   ├── hybrid_retriever.py      # 文档路混合检索器
│   │   ├── bm25_retriever.py        # BM25检索器
│   │   ├── vector_retriever.py      # 向量检索器
│   │   ├── graph_keyword_retriever.py # 图路关键词检索
│   │   ├── graph_vector_retriever.py  # 图路向量检索
│   │   ├── graph_retriever.py       # 图路融合检索器
│   │   └── rrf_fusion.py            # RRF融合器
│   │
│   ├── utils/                       # 工具层
│   │   ├── __init__.py
│   │   ├── stopwords_manager.py     # 停用词管理器
│   │   └── (FakeToolCallFormatter)   # 伪造工具调用格式化器
│   │
│   ├── tools/                       # Agent 工具层
│   │   ├── __init__.py
│   │   └── memory_search_tool.py    # 主动长期记忆回忆工具
│   │
│   └── prompts/                     # 提示词模板
│       ├── private_chat_prompt.txt
│       └── group_chat_prompt.txt
│
├── storage/                         # 存储层
│   ├── __init__.py
│   ├── conversation_store.py        # 会话存储
│   ├── db_migration.py              # 数据库迁移
│   └── backup_manager.py            # 定时自动备份管理器
│
├── webui/                           # Web管理界面
│   ├── __init__.py
│   ├── server.py                    # FastAPI服务器
│   └── README.md
│
├── static/                          # 静态资源
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   ├── graph-ui.js                  # 3D 知识图谱渲染器
│   └── i18n.js                      # 国际化引擎
│
├── tests/                           # 测试套件
│   ├── conftest.py
│   ├── test_*.py
│   └── integration/
│
└── docs/                            # 文档
    ├── API.md
    ├── DEVELOPMENT.md
    └── ARCHITECTURE.md (本文档)
```

---

## 分层架构

### 1. 基础组件层 (base/)

**职责**: 提供最基础的组件，被其他所有层依赖

**组件**:
- `exceptions.py`: 自定义异常类
- `constants.py`: 常量定义
- `config_manager.py`: 配置管理
- `config_validator.py`: 配置验证

**依赖**: 无（最底层）

**被依赖**: 所有其他层

---

### 2. 数据模型层 (models/)

**职责**: 定义数据结构和模型

**组件**:
- `conversation_models.py`: Message、Session、MemoryEvent等数据模型

**依赖**: base/

**被依赖**: managers/, processors/, storage/

---

### 3. 处理器层 (processors/)

**职责**: 处理和转换数据

**组件**:
- `memory_processor.py`: 使用LLM处理对话历史
- `text_processor.py`: 文本分词和处理
- `chatroom_parser.py`: 聊天室上下文解析
- `message_utils.py`: 消息处理工具

**依赖**: base/, models/

**被依赖**: managers/, event_handler.py

---

### 4. 检索系统层 (retrieval/)

**职责**: 实现记忆检索功能

**组件**:
- `dual_route_retriever.py`: 协调文档路与图路的最终排序
- `hybrid_retriever.py`: 文档路混合检索器（BM25 + 向量）
- `bm25_retriever.py`: 文档路关键词检索
- `vector_retriever.py`: 文档路向量检索
- `graph_keyword_retriever.py`: 图路关键词检索
- `graph_vector_retriever.py`: 图路向量检索
- `graph_retriever.py`: 图路结果融合
- `rrf_fusion.py`: RRF融合算法

**依赖**: base/, processors/text_processor

**被依赖**: managers/memory_engine

---

### 5. 管理器层 (managers/)

**职责**: 管理核心业务逻辑

**组件**:
- `memory_engine.py`: 记忆引擎（核心）
- `conversation_manager.py`: 会话管理

**依赖**: base/, models/, processors/, retrieval/

**被依赖**: plugin_initializer, event_handler, command_handler

---

### 6. 验证器层 (validators/)

**职责**: 验证数据和索引

**组件**:
- `index_validator.py`: 索引一致性验证

**依赖**: base/, managers/

**被依赖**: plugin_initializer, command_handler

---

### 7. 工具层 (utils/)

**职责**: 提供通用工具函数和格式化器

**组件**:
- `stopwords_manager.py`: 停用词管理
- `FakeToolCallFormatter` (`__init__.py`): 伪造工具调用格式化器
  - 将记忆内容封装为 `tool_calls` + `tool` 消息对
  - 使用固定前缀 `fake_recall_` 作为 call_id，便于 `EventHandler` 自动清理
  - 支持 token 预算截断，避免超出上下文限制

**依赖**: base/

**被依赖**: processors/, retrieval/, event_handler.py

---

### 8. Agent 工具层 (tools/)

**职责**: 为 AstrBot 的 tool loop / agent 模式提供可主动调用的业务工具。

**组件**:
- `memory_search_tool.py`: 主动长期记忆回忆工具，复用现有记忆检索引擎和过滤配置

**依赖**: base/, managers/, utils/

**被依赖**: main.py（注册到 AstrBot context）

---

### 9. 存储层 (storage/)

**职责**: 数据持久化与备份

**组件**:
- `conversation_store.py`: 会话数据存储
- `db_migration.py`: 数据库迁移
- `backup_manager.py`: 定时自动备份管理器
  - 基于间隔时间的备份调度（cron / asyncio sleep）
  - 可配置的保留策略（按天数或数量）
  - 失败时自动重试与告警

**依赖**: base/, models/

**被依赖**: managers/conversation_manager, plugin_initializer

---

### 10. 顶层组件

**组件**:
- `plugin_initializer.py`: 插件初始化
- `event_handler.py`: 事件处理
- `command_handler.py`: 命令处理
- `main.py`: 插件注册
- `tools/`: Agent 工具注册入口的实现来源

**依赖**: 所有下层组件

**被依赖**: 无（最顶层）

---

## 依赖关系图

```
main.py
  ↓
plugin_initializer.py, event_handler.py, command_handler.py, tools/
  ↓
managers/ (memory_engine, conversation_manager)
  ↓
processors/, retrieval/, validators/, storage/
  ↓
models/
  ↓
base/ (exceptions, config, constants)
```

---

## 导入规范

### 从core导入

```python
# 推荐：从core顶层导入
from core import (
    ConfigManager,
    MemoryEngine,
    ConversationManager,
    MemoryProcessor,
    Message,
    Session,
)

# 也可以：从子模块导入
from core.base import ConfigManager
from core.managers import MemoryEngine
from core.models import Message
```

### 在core内部导入

```python
# 在core/managers/memory_engine.py中
from ..base import ConfigManager, LivingMemoryException
from ..models import Message
from ..processors import TextProcessor
from ..retrieval import HybridRetriever
```

### 在子模块内部导入

```python
# 在core/managers/conversation_manager.py中
from ..base import ConfigManager
from ..models import Message, Session
from ...storage import ConversationStore
```

---

## 设计原则

### 1. 单一职责原则 (SRP)

每个模块只负责一个明确的功能领域：
- base/: 基础设施
- models/: 数据结构
- processors/: 数据处理
- managers/: 业务逻辑
- validators/: 验证逻辑

### 2. 依赖倒置原则 (DIP)

高层模块不依赖低层模块，都依赖抽象：
- 使用接口和抽象类
- 通过依赖注入传递依赖

### 3. 开闭原则 (OCP)

对扩展开放，对修改关闭：
- 新功能通过添加新模块实现
- 不修改现有稳定代码

### 4. 接口隔离原则 (ISP)

客户端不应依赖它不需要的接口：
- 每个模块只暴露必要的接口
- 使用__all__明确导出

### 5. 最少知识原则 (LoD)

模块只与直接依赖交互：
- 避免跨层调用
- 通过中间层传递

---

## 模块职责矩阵

| 层级 | 职责 | 示例 |
|------|------|------|
| base/ | 基础设施 | 异常、配置、常量 |
| models/ | 数据定义 | Message、Session |
| processors/ | 数据处理 | 文本处理、LLM调用 |
| retrieval/ | 检索实现 | 文档路、图路、RRF 融合 |
| managers/ | 业务逻辑 | 记忆管理、会话管理 |
| validators/ | 验证逻辑 | 索引验证 |
| utils/ | 工具函数 | 停用词、辅助函数 |
| storage/ | 数据持久化 | SQLite操作 |

---

### WebUI 前端架构

**文件**:
- `index.html`: 单页应用骨架，包含双语切换器、深色模式切换器
- `app.js`: 主仪表盘逻辑（记忆表格、搜索、分页、CRUD）
- `graph-ui.js`: 3D 知识图谱渲染器
  - 基于 `ForceGraph3D` (Three.js) 的力导向图
  - 支持节点拖拽、缩放、旋转、hover 提示
  - 实体类型着色：人物、地点、事件、概念、其他
  - 与 `app.js` 通过事件总线通信，共享过滤条件
- `i18n.js`: 国际化引擎
  - 支持 `zh` / `en` / `ru`（俄语为回退）
  - 基于 `data-i18n` 属性的 DOM 翻译
  - `localStorage` 持久化 + `navigator.language` 自动检测
- `styles.css`: 玻璃拟态 (glassmorphism) 设计系统

---

## 扩展指南

### 添加新的处理器

1. 在 `core/processors/` 创建新文件
2. 实现处理器类
3. 在 `core/processors/__init__.py` 中导出
4. 在需要的地方导入使用

### 添加新的检索器

1. 在 `core/retrieval/` 创建新文件
2. 实现检索器接口
3. 在 `HybridRetriever` 中集成
4. 更新配置和文档

### 添加新的管理器

1. 在 `core/managers/` 创建新文件
2. 实现管理器类
3. 在 `core/managers/__init__.py` 中导出
4. 在初始化器中集成

---

## 最佳实践

### 1. 导入顺序

```python
# 标准库
import os
import time

# 第三方库
import aiosqlite
from astrbot.api import logger

# 本地导入 - 按层级顺序
from ..base import ConfigManager, LivingMemoryException
from ..models import Message
from ..processors import TextProcessor
```

### 2. 循环依赖避免

- 使用类型注解的字符串形式
- 延迟导入（在函数内部导入）
- 重新设计模块边界

### 3. 接口设计

- 使用类型注解
- 提供文档字符串
- 明确参数和返回值

### 4. 错误处理

- 使用自定义异常
- 在合适的层级捕获
- 提供清晰的错误信息

---

## 版本历史

### v2.2.10

- **Fake Tool Call Injection**: 新增伪造工具调用注入策略，兼容 Agent / Tool Loop 模式
- **Image Caption Memory**: 支持将 AstrBot 图片转述结果自动存入长期记忆
- **3D Knowledge Graph WebUI**: 新增基于 ForceGraph3D 的 3D 力导向图可视化
- **Scheduled Auto-Backup**: 新增定时自动备份子系统 (`backup_manager.py`)
- **Safe Batched Index Rebuild**: 索引重建支持安全分批模式，避免内存溢出
- **i18n**: WebUI 新增完整国际化支持（中/英/俄），命令响应全面英文化

### v2.0.0 (2025-12-17)

- 重新组织目录结构
- 引入分层架构
- 改进模块职责划分
- 完成目录重组验证

**验证结果**:
- ✓ 目录结构验证通过
- ✓ __init__.py 导出验证通过
- ✓ 导入路径验证通过
- ✓ 7个功能子目录，18个Python文件
- ✓ 13个文件的导入路径已更新

---

**文档版本**: v2.2.10
**最后更新**: 2026-04-28
