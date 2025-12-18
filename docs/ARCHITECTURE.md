# LivingMemory 架构文档

**版本**: v2.0.0
**更新日期**: 2025-12-17

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
│   │   ├── hybrid_retriever.py      # 混合检索器
│   │   ├── bm25_retriever.py        # BM25检索器
│   │   ├── vector_retriever.py      # 向量检索器
│   │   ├── sparse_retriever.py      # 稀疏检索器
│   │   └── rrf_fusion.py            # RRF融合器
│   │
│   ├── utils/                       # 工具层
│   │   ├── __init__.py
│   │   └── stopwords_manager.py     # 停用词管理器
│   │
│   └── prompts/                     # 提示词模板
│       ├── private_chat_prompt.txt
│       └── group_chat_prompt.txt
│
├── storage/                         # 存储层
│   ├── __init__.py
│   ├── conversation_store.py        # 会话存储
│   └── db_migration.py              # 数据库迁移
│
├── webui/                           # Web管理界面
│   ├── __init__.py
│   ├── server.py                    # FastAPI服务器
│   └── README.md
│
├── static/                          # 静态资源
│   ├── index.html
│   ├── styles.css
│   └── app.js
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
- `hybrid_retriever.py`: 混合检索器（协调者）
- `bm25_retriever.py`: BM25稀疏检索
- `vector_retriever.py`: 向量密集检索
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

**职责**: 提供通用工具函数

**组件**:
- `stopwords_manager.py`: 停用词管理
- 其他工具函数

**依赖**: base/

**被依赖**: processors/, retrieval/

---

### 8. 存储层 (storage/)

**职责**: 数据持久化

**组件**:
- `conversation_store.py`: 会话数据存储
- `db_migration.py`: 数据库迁移

**依赖**: base/, models/

**被依赖**: managers/conversation_manager

---

### 9. 顶层组件

**组件**:
- `plugin_initializer.py`: 插件初始化
- `event_handler.py`: 事件处理
- `command_handler.py`: 命令处理
- `main.py`: 插件注册

**依赖**: 所有下层组件

**被依赖**: 无（最顶层）

---

## 依赖关系图

```
main.py
  ↓
plugin_initializer.py, event_handler.py, command_handler.py
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
| retrieval/ | 检索实现 | BM25、向量检索 |
| managers/ | 业务逻辑 | 记忆管理、会话管理 |
| validators/ | 验证逻辑 | 索引验证 |
| utils/ | 工具函数 | 停用词、辅助函数 |
| storage/ | 数据持久化 | SQLite操作 |

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

**文档版本**: v2.0.0
**最后更新**: 2025-12-17
