# LivingMemory 插件功能分析文档

**版本**: v1.7.2
**分析日期**: 2025-12-17
**目的**: 为代码重构提供完整的功能清单和实现细节

---

## 一、插件概述

### 1.1 核心定位
LivingMemory 是一个为 AstrBot 设计的智能长期记忆插件，通过混合检索技术（BM25 + 向量检索）和 LLM 自动总结，为聊天机器人提供持久化的上下文记忆能力。

### 1.2 技术架构
- **存储层**: SQLite + FAISS 向量数据库
- **检索层**: BM25 稀疏检索 + FAISS 向量检索 + RRF 融合
- **处理层**: LLM 对话总结 + 重要性评估
- **管理层**: 会话管理 + 记忆生命周期管理
- **展示层**: FastAPI WebUI

---

## 二、核心功能模块

### 2.1 记忆引擎 (MemoryEngine)

**文件**: `core/memory_engine.py`

#### 功能清单
1. **记忆CRUD操作**
   - `add_memory()`: 添加新记忆到多个索引（BM25 + 向量）
   - `search_memories()`: 混合检索相关记忆
   - `get_memory()`: 根据ID获取单条记忆
   - `update_memory()`: 更新记忆内容或元数据
   - `delete_memory()`: 从所有索引中删除记忆

2. **高级功能**
   - `update_importance()`: 更新记忆重要性评分
   - `update_access_time()`: 更新最后访问时间
   - `get_session_memories()`: 获取特定会话的所有记忆
   - `cleanup_old_memories()`: 清理过期低重要性记忆
   - `get_statistics()`: 获取记忆统计信息

3. **数据迁移**
   - `_migrate_session_data_if_needed()`: 运行时自动迁移旧格式session_id

#### 关键实现细节
- **ID管理体系**: 使用整数ID作为统一标识符，跨三层存储（documents表、BM25索引、FAISS向量）
- **时间衰减**: 使用指数衰减公式 `exp(-decay_rate * days_old)`
- **重要性加权**: `final_score = rrf_score * importance * importance_weight * recency_weight`
- **分批处理**: 所有大规模数据操作使用批次处理（batch_size=500）避免内存问题

---

### 2.2 混合检索系统

#### 2.2.1 混合检索器 (HybridRetriever)

**文件**: `core/retrieval/hybrid_retriever.py`

**功能清单**:
1. **并行检索**: 使用 `asyncio.gather` 同时执行 BM25 和向量检索
2. **RRF融合**: 通过 Reciprocal Rank Fusion 算法融合两路结果
3. **智能加权**: 应用重要性和时间衰减权重
4. **退化机制**: 某一路失败时自动使用另一路结果
5. **元数据同步**: 确保三个存储层的元数据一致性

**关键参数**:
- `decay_rate`: 时间衰减率（默认0.01）
- `importance_weight`: 重要性权重（默认1.0）
- `fallback_enabled`: 启用退化机制（默认True）

#### 2.2.2 BM25检索器 (BM25Retriever)

**文件**: `core/retrieval/bm25_retriever.py`

**功能清单**:
1. 使用 SQLite FTS5 实现全文检索
2. 支持中文分词（jieba）
3. 停用词过滤
4. 会话和人格过滤

**关键参数**:
- `bm25_k1`: 词频饱和度参数（默认1.2）
- `bm25_b`: 文档长度归一化参数（默认0.75）

#### 2.2.3 向量检索器 (VectorRetriever)

**文件**: `core/retrieval/vector_retriever.py`

**功能清单**:
1. 基于 FAISS 的向量相似度检索
2. 使用 Embedding Provider 生成向量
3. 支持元数据过滤

#### 2.2.4 RRF融合器 (RRFFusion)

**文件**: `core/retrieval/rrf_fusion.py`

**功能清单**:
1. 实现 Reciprocal Rank Fusion 算法
2. 公式: `score = sum(1 / (k + rank))`
3. 支持单路退化

**关键参数**:
- `rrf_k`: RRF参数（默认60，推荐30-120）

---

### 2.3 记忆处理器 (MemoryProcessor)

**文件**: `core/memory_processor.py`

#### 功能清单
1. **对话总结**: 使用 LLM 将对话历史转换为结构化记忆
2. **场景适配**: 支持私聊和群聊两种场景的不同处理策略
3. **结构化提取**: 从 LLM 响应中提取 JSON 格式的结构化数据
4. **降级处理**: LLM 失败时使用简单文本拼接

#### 输出格式
```json
{
  "summary": "对话摘要（第一人称）",
  "topics": ["主题1", "主题2"],
  "key_facts": ["关键事实1", "关键事实2"],
  "sentiment": "positive|neutral|negative",
  "importance": 0.0-1.0,
  "participants": ["参与者1", "参与者2"]  // 仅群聊
}
```

#### 提示词模板
- **私聊**: `core/prompts/private_chat_prompt.txt`
- **群聊**: `core/prompts/group_chat_prompt.txt`

---

### 2.4 会话管理器 (ConversationManager)

**文件**: `core/conversation_manager.py`

#### 功能清单
1. **会话生命周期管理**
   - `create_or_get_session()`: 创建或获取会话
   - `get_session_info()`: 获取会话信息
   - `clear_session()`: 清空会话历史
   - `cleanup_expired_sessions()`: 清理过期会话

2. **消息管理**
   - `add_message_from_event()`: 从 AstrBot 事件添加消息
   - `add_message()`: 添加消息到会话
   - `get_messages()`: 获取会话消息
   - `get_messages_range()`: 获取指定范围的消息（用于滑动窗口）
   - `get_context()`: 获取会话上下文（格式化为 LLM 格式）

3. **元数据管理**
   - `update_session_metadata()`: 更新会话元数据
   - `get_session_metadata()`: 获取会话元数据
   - `reset_session_metadata()`: 重置会话元数据

4. **LRU缓存**
   - 使用 `OrderedDict` 实现 LRU 缓存
   - 缓存热点会话的消息列表
   - 自动淘汰最久未使用的会话

#### 关键参数
- `max_cache_size`: LRU缓存大小（默认100）
- `context_window_size`: 上下文窗口大小（默认50）
- `session_ttl`: 会话过期时间（默认3600秒）

---

### 2.5 会话存储层 (ConversationStore)

**文件**: `storage/conversation_store.py`

#### 数据库表结构

**sessions 表**:
```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_active_at REAL NOT NULL,
    message_count INTEGER DEFAULT 0,
    participants TEXT DEFAULT '[]',  -- JSON数组
    metadata TEXT DEFAULT '{}'       -- JSON对象
)
```

**messages 表**:
```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- "user" | "assistant" | "system"
    content TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender_name TEXT,
    group_id TEXT,                   -- 群聊ID（私聊为NULL）
    platform TEXT,
    timestamp REAL NOT NULL,
    metadata TEXT DEFAULT '{}',      -- JSON对象
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
)
```

#### 功能清单
1. **会话CRUD**: 创建、读取、更新、删除会话
2. **消息CRUD**: 添加、查询、删除消息
3. **高级查询**: 按发送者过滤、关键词搜索、统计分析
4. **参与者管理**: 添加和查询会话参与者

---

### 2.6 数据模型 (ConversationModels)

**文件**: `core/conversation_models.py`

#### 数据类定义

**Message**:
- 表示单条消息
- 支持群聊场景（sender_id, sender_name, group_id）
- `format_for_llm()`: 格式化为 LLM 输入格式

**Session**:
- 表示一段对话会话
- 记录会话元信息和统计数据
- 支持参与者列表

**MemoryEvent**:
- 表示从对话中提取的结构化记忆
- 包含重要性评分和元数据

---

### 2.7 文本处理器 (TextProcessor)

**文件**: `core/text_processor.py`

#### 功能清单
1. **中文分词**: 使用 jieba 进行中文分词
2. **停用词过滤**: 支持多种停用词表（hit、baidu、cn）
3. **文本清洗**: 去除特殊字符、标点符号
4. **查询预处理**: 为检索优化查询文本

---

### 2.8 配置验证器 (ConfigValidator)

**文件**: `core/config_validator.py`

#### 功能清单
1. **配置验证**: 使用 Pydantic 验证配置结构
2. **默认值合并**: 将用户配置与默认配置合并
3. **类型检查**: 确保配置项类型正确

---

### 2.9 索引验证器 (IndexValidator)

**文件**: `core/index_validator.py`

#### 功能清单
1. **一致性检查**: 检查 documents、BM25、FAISS 三个索引的一致性
2. **自动重建**: 检测到不一致时自动重建索引
3. **迁移状态管理**: 管理 v1 到 v2 的数据迁移状态

---

### 2.10 数据库迁移 (DBMigration)

**文件**: `storage/db_migration.py`

#### 功能清单
1. **版本检测**: 检测数据库版本
2. **自动迁移**: 从 v1 迁移到 v2 架构
3. **备份机制**: 迁移前自动备份数据库
4. **进度回调**: 支持迁移进度回调

---

### 2.11 WebUI 管理界面

**文件**: `webui/server.py`, `static/index.html`

#### 功能清单
1. **记忆管理**
   - 查看所有记忆
   - 搜索记忆
   - 编辑记忆
   - 删除记忆

2. **会话管理**
   - 查看所有会话
   - 查看会话详情
   - 清空会话历史

3. **统计分析**
   - 记忆数量统计
   - 会话统计
   - 重要性分布

4. **系统管理**
   - 索引重建
   - 数据迁移
   - 配置查看

#### API端点
- `GET /api/memories`: 获取记忆列表
- `GET /api/memories/{id}`: 获取单条记忆
- `PUT /api/memories/{id}`: 更新记忆
- `DELETE /api/memories/{id}`: 删除记忆
- `GET /api/sessions`: 获取会话列表
- `GET /api/statistics`: 获取统计信息
- `POST /api/rebuild-index`: 重建索引

---

## 三、事件钩子系统

### 3.1 on_llm_request 钩子

**方法**: `handle_memory_recall()`

#### 功能流程
1. 检查插件初始化状态
2. 提取 session_id 和 persona_id
3. 自动删除旧的注入记忆（如果启用）
4. 提取真实用户消息（处理群聊上下文格式）
5. 执行混合检索
6. 格式化记忆为注入格式
7. 根据配置注入到 system_prompt 或 user_message
8. 存储用户消息到数据库（仅私聊）

#### 记忆注入方式
- `system_prompt`: 注入到系统提示词
- `user_message_before`: 在用户消息前注入
- `user_message_after`: 在用户消息后注入

### 3.2 on_llm_response 钩子

**方法**: `handle_memory_reflection()`

#### 功能流程
1. 检查插件初始化状态
2. 存储助手响应到数据库
3. 获取会话信息
4. 检查是否达到总结触发条件
5. 使用滑动窗口获取未总结的消息
6. 调用 MemoryProcessor 生成结构化记忆
7. 存储记忆到 MemoryEngine
8. 更新 last_summarized_index

#### 滑动窗口机制
- 使用 `last_summarized_index` 跟踪已总结的消息位置
- 每次只总结未总结的消息
- 避免重复总结

### 3.3 handle_all_group_messages 钩子

**方法**: `handle_all_group_messages()`

#### 功能流程
1. 捕获所有群聊消息（包括非@Bot的消息）
2. 判断是否为Bot自己发送的消息
3. 提取消息内容（包括非文本消息的描述）
4. 存储到数据库并标记 is_bot_message
5. 执行消息数量上限控制

#### 消息去重
- 使用内存缓存 `_message_dedup_cache`
- 缓存最近1000条消息ID
- TTL为5分钟

---

## 四、命令系统

### 4.1 命令列表

| 命令 | 权限 | 功能 |
|------|------|------|
| `/lmem status` | 管理员 | 查看记忆系统状态 |
| `/lmem search <query> [k]` | 管理员 | 搜索记忆 |
| `/lmem forget <id>` | 管理员 | 删除指定记忆 |
| `/lmem rebuild-index` | 管理员 | 重建索引 |
| `/lmem webui` | 管理员 | 查看 WebUI 信息 |
| `/lmem reset` | 管理员 | 重置当前会话的记忆上下文 |
| `/lmem help` | 管理员 | 显示帮助信息 |

---

## 五、配置系统

### 5.1 配置文件

**文件**: `_conf_schema.json`

### 5.2 配置项分类

#### Provider 设置
- `embedding_provider_id`: 向量嵌入模型ID
- `llm_provider_id`: 大语言模型ID

#### WebUI 设置
- `enabled`: 启用WebUI
- `host`: 监听地址
- `port`: 监听端口
- `access_password`: 访问密码
- `session_timeout`: 会话超时时间

#### 会话管理器
- `max_sessions`: 最大会话缓存数量
- `session_ttl`: 会话生存时间
- `context_window_size`: 上下文窗口大小

#### 召回引擎
- `top_k`: 单次检索数量
- `importance_weight`: 重要性权重
- `fallback_to_vector`: 向量检索回退
- `injection_method`: 记忆注入方式
- `auto_remove_injected`: 自动删除旧记忆片段

#### 重要性衰减
- `decay_rate`: 每日衰减率

#### 融合策略
- `rrf_k`: RRF参数k

#### 过滤设置
- `use_persona_filtering`: 启用人格记忆过滤
- `use_session_filtering`: 启用会话记忆隔离

#### 反思引擎
- `summary_trigger_rounds`: 总结触发轮次
- `save_original_conversation`: 保存原始对话历史

#### 迁移设置
- `auto_migrate`: 启用自动迁移
- `create_backup`: 迁移前自动备份

#### 遗忘代理
- `cleanup_days_threshold`: 清理天数阈值
- `cleanup_importance_threshold`: 清理重要性阈值

#### 稀疏检索器
- `enabled`: 启用稀疏检索
- `bm25_k1`: BM25 K1参数
- `bm25_b`: BM25 B参数
- `use_chinese_tokenizer`: 使用中文分词
- `enable_stopwords_filtering`: 启用停用词过滤
- `stopwords_source`: 停用词来源
- `custom_stopwords`: 自定义停用词

#### 稠密检索器
- `enable_query_preprocessing`: 启用查询预处理

---

## 六、依赖关系

### 6.1 外部依赖

```
faiss-cpu>=1.7.0        # 向量检索
networkx>=3.0           # 图算法（未使用）
jieba>=0.42.1           # 中文分词
fastapi>=0.110.0        # Web框架
uvicorn>=0.23.0         # ASGI服务器
pytz>=2021.3            # 时区处理
```

### 6.2 AstrBot API 依赖

- `astrbot.api.logger`: 日志记录
- `astrbot.api.event.AstrMessageEvent`: 消息事件
- `astrbot.api.event.filter`: 事件过滤器
- `astrbot.api.provider.Provider`: LLM提供者
- `astrbot.api.provider.LLMResponse`: LLM响应
- `astrbot.api.provider.ProviderRequest`: Provider请求
- `astrbot.api.star.Context`: 插件上下文
- `astrbot.api.star.Star`: 插件基类
- `astrbot.api.star.StarTools`: 插件工具
- `astrbot.api.star.register`: 插件注册装饰器
- `astrbot.core.db.vec_db.faiss_impl.vec_db.FaissVecDB`: FAISS向量数据库
- `astrbot.core.provider.provider.EmbeddingProvider`: 嵌入提供者

---

## 七、数据流图

### 7.1 记忆存储流程

```
用户消息 → AstrMessageEvent
    ↓
handle_all_group_messages (群聊) / handle_memory_recall (私聊)
    ↓
ConversationManager.add_message_from_event()
    ↓
ConversationStore.add_message()
    ↓
SQLite messages表
    ↓
达到触发轮次
    ↓
handle_memory_reflection()
    ↓
ConversationManager.get_messages_range()
    ↓
MemoryProcessor.process_conversation()
    ↓
LLM Provider (生成结构化记忆)
    ↓
MemoryEngine.add_memory()
    ↓
HybridRetriever.add_memory()
    ↓
并行添加到:
  - VectorRetriever (FAISS)
  - BM25Retriever (FTS5)
  - documents表
```

### 7.2 记忆召回流程

```
用户消息 → AstrMessageEvent
    ↓
handle_memory_recall()
    ↓
提取session_id和persona_id
    ↓
MemoryEngine.search_memories()
    ↓
HybridRetriever.search()
    ↓
并行检索:
  - BM25Retriever.search()
  - VectorRetriever.search()
    ↓
RRFFusion.fuse()
    ↓
应用重要性和时间衰减加权
    ↓
格式化记忆
    ↓
注入到ProviderRequest
    ↓
LLM Provider
```

---

## 八、关键算法

### 8.1 RRF融合算法

```python
def rrf_score(rank, k=60):
    return 1 / (k + rank)

final_score = sum(rrf_score(rank_in_list_i, k) for each list)
```

### 8.2 时间衰减算法

```python
days_old = (current_time - create_time) / 86400
recency_weight = exp(-decay_rate * days_old)
```

### 8.3 最终评分算法

```python
final_score = rrf_score * importance * importance_weight * recency_weight
```

---

## 九、已知问题和限制

### 9.1 性能限制
1. 大规模数据（>10000条记忆）时检索性能下降
2. 向量索引重建耗时较长
3. LLM总结可能较慢

### 9.2 功能限制
1. 不支持跨会话的记忆关联
2. 不支持记忆的手动编辑（仅WebUI支持）
3. 群聊场景下的记忆质量依赖LLM能力

### 9.3 兼容性问题
1. 依赖AstrBot特定版本的API
2. 需要Embedding Provider支持
3. 需要LLM Provider支持

---

## 十、测试覆盖需求

### 10.1 单元测试
- [ ] MemoryEngine CRUD操作
- [ ] HybridRetriever 检索逻辑
- [ ] MemoryProcessor LLM解析
- [ ] ConversationManager 缓存机制
- [ ] TextProcessor 分词和停用词

### 10.2 集成测试
- [ ] 完整的记忆存储和召回流程
- [ ] 多会话隔离
- [ ] 数据迁移
- [ ] WebUI API

### 10.3 性能测试
- [ ] 大规模数据检索性能
- [ ] 并发请求处理
- [ ] 内存使用情况

---

## 十一、重构优先级建议

### 高优先级
1. 简化 main.py 的初始化逻辑
2. 统一错误处理机制
3. 优化数据库查询性能
4. 改进日志记录

### 中优先级
1. 重构配置管理
2. 优化缓存策略
3. 改进测试覆盖
4. 文档完善

### 低优先级
1. WebUI 功能增强
2. 支持更多检索算法
3. 性能监控和指标

---

**文档结束**
