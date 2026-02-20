# Changelog

所有重要的更改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.1.8] - 2026-02-20

### 修复
- 修复向量索引冗余槽位导致每次启动都触发全量重建的问题：FAISS `ntotal` 包含逻辑删除后的空槽，属正常行为，不再触发重建；仅 BM25 冗余或索引缺失时才重建
- 修复 `get_persona_id` 与 AstrBot 主流程优先级不一致的问题：新增最高优先级 `session_service_config`（由 `/persona` 等命令写入），并正确处理 `[%None]`（明确无人格）不再 fallback 到默认人格
- 修复 `handle_memory_recall` 中 `persona_id` 获取路径：移除直接读取 `req.conversation.persona_id` 的逻辑（`on_llm_request` 钩子在 `_ensure_persona_and_skills` 之前触发，该字段不含 session_service_config 覆盖），统一走完整三级优先级

### 优化
- Provider 未就绪时的日志提示明确区分 Embedding Provider 和 LLM Provider，并附带配置建议
- 周期性重试日志显示当前哪个 Provider 仍未就绪
- 最终超时失败日志列出具体未就绪的 Provider 名称

## [2.1.7] - 2026-02-19

### 新增
- 新增双通道记忆总结机制：`canonical_summary`（事实导向，用于检索）与 `persona_summary`（人格风格，用于注入表达）解耦存储
- 新增 `SummaryValidator`（`_validate_summary_quality`）：对总结结果进行字段完整性、长度、泛化词检测，质量不达标时标记 `summary_quality=low`
- 新增 MMR（最大边际相关性）去重：召回结果在加权排序后执行 Jaccard 相似度去重，避免语义重复记忆占据 Top-K
- 新增 `score_breakdown` 字段：每条召回结果附带各维度分数明细（`rrf_normalized`、`importance`、`recency_weight`、`days_old`、`final_score`），便于调试
- 新增 `source_window` 元数据：记忆写入时记录来源会话窗口（`session_id`、`start_index`、`end_index`、`message_count`），支持后续溯源
- 新增 `summary_schema_version` 字段：新写入记忆标记为 `v2`，旧记录通过数据库迁移补标 `v1`
- 数据库迁移升级至 v4：为所有旧格式记录批量补充 `summary_schema_version=v1` 和 `summary_quality=unknown` 标记

### 修复
- 修复群聊双重写入 Bug：`handle_all_group_messages` 现在跳过 Bot 自身消息，避免 assistant 响应被写入两次（`handle_memory_reflection` 为唯一写入方）
- 修复 `persona_id` 获取不一致问题：优先从 `req.conversation.persona_id` 读取，确保召回与 LLM 调用使用完全相同的人格 ID
- 修复评分公式"清零"问题：将全乘法 `rrf * importance * recency` 改为加权求和 `0.5*rrf + 0.25*importance + 0.25*recency`，高重要性旧记忆不再被时间衰减压制至接近零
- 修复 `last_access_time` 未参与衰减计算的问题：时间衰减基准改为 `max(create_time, last_access_time)`，高频访问记忆衰减自然放缓
- 修复数据库迁移中 `json_set` 语法错误：将无效的 `CASE` 表达式替换为 `COALESCE(NULLIF(TRIM(metadata), ''), '{}')`
- 修复 `_build_storage_format` 中 `summary_quality` 被硬编码为 `"normal"` 的问题，现由 `_validate_summary_quality` 动态决定

### 优化
- 记忆注入改为追加到 `system_prompt` 末尾，确保人格提示词在前、记忆内容在后，符合 LLM 理解优先级
- `content` 字段默认改为存储 `canonical_summary + key_facts`，提升 BM25 检索稳定性
- MMR 参数（`mmr_lambda`）、评分权重（`score_alpha/beta/gamma`）均可通过配置覆盖

### 测试
- 新增 `MemoryProcessor` 群聊路径测试（7 个）：`interaction_type`、`participants` 提取、双通道摘要、缺失字段默认值、私聊无 `participants`、长内容不崩溃、泛化词质量标记
- 新增 `EventHandler` 边界条件与 `source_window` 测试（8 个）：空 prompt 跳过召回、`user_message_before/after` 注入位置、`source_window` 字段写入验证、过期任务跳过、错误/空响应跳过、重试超限放弃
- 新增 `HybridRetriever` 边界条件与回滚测试（7 个）：空查询返回空列表、两路失败返回空列表、单路降级、空 metadata 不崩溃、k 限制结果数量
- 新增 `MemoryEngine` 过滤/衰减/清理边界测试（11 个）：session 隔离、`decay_rate=0`/`days=0` 边界、衰减实际生效、`cleanup` 负数/零天边界、内容更新先建后删、删除不存在 ID、空查询、统计字段
- 全量测试 118 个，全部通过（pytest + pytest-asyncio）

## [2.1.4] - 2026-02-19

### 优化
- 优化记忆注入方式
- 优化删除逻辑，确保内容安全
- 改进 Webui 的会话处理逻辑
- 添加每日自动清理功能
- 优化记忆管理和初始化逻辑


## [2.1.2] - 2026-01-20

### 修复
- 修复历史消息清理功能无法处理多模态消息格式的问题
  - 支持 OpenAI 多模态格式: `{"role": "user", "content": [{"type": "text", "text": "xxx"}]}`
  - 正确清理 contexts 中 list 类型 content 的记忆注入片段
  - 修复清理逻辑只处理 string 类型 content 导致的清理失败

### 优化
- 简化记忆清理日志输出,移除冗余的 DEBUG 级别日志
- 优化 `_remove_injected_memories_from_context` 方法,支持三种 contexts 格式
- 改进 cleanup 命令,操作 AstrBot 数据库而非插件自身数据库

## [2.1.1] - 2026-01-19

### 新增
- 添加 `/lmem cleanup` 命令，支持清理历史消息中的记忆注入片段
- 增强记忆处理器，支持人格提示和上下文管理
- 处理 Message 对象的 metadata 字段，支持 JSON 字符串解析

### 优化
- 更新人格提示和总结要求，增强记忆生成的个性化和准确性
- 增强命令处理和事件处理逻辑，添加输入验证和后台任务管理
- 更新消息数量上限控制逻辑，仅删除已总结的消息

## [2.0.11] - 2026-01-06

### 新增
- 添加 LLM 调用重试机制和 JSON 修复功能，增强数据处理的鲁棒性
- 添加记忆重要性衰减调度器，支持每日自动衰减处理
- 增强事件处理器和记忆处理器，支持失败总结重试机制和 JSON 格式输出修复

### 优化
- 按创建时间降序排序记忆列表，优化用户体验
- 增强事件处理器和会话管理器，优化群聊判断逻辑

## [2.0.8] - 2026-01-05

### 修复
- 修复命令无法正确响应问题

### 优化
- 更新私聊提示，增强消息格式说明和昵称使用规则
- 重构自动发布工作流，简化版本检查与发布逻辑，移除旧的 release.yml 文件

## [2.0.6] - 2026-01-04

### 新增
- 添加索引维度检查与修复逻辑，确保与当前 embedding provider 维度一致
- 增强数据一致性检查，添加实际消息数量获取和同步逻辑
- 增强响应内容检查，过滤空回复和错误响应，确保消息记录的有效性

### 修复
- 修复指令无法使用问题

### 优化
- 优化代码格式，增强可读性，调整多个文件中的代码缩进和换行
- 增强调试信息，优化消息格式化逻辑，更新群聊提示文档

## [2.0.2] - 2025-12-18

### 修复
- 修复会话 message_count 不一致问题，增强消息获取逻辑和调试信息

### 优化
- 更新默认监听端口至 8888

## [2.0.1] - 2025-12-18

### 优化
- 优化自动发布工作流中的版本检查和日志输出
- 重构和增强代码结构，添加新测试和性能基准
- 删除 lint 和 test 工作流配置文件

## [2.0.0] - 2025-12-17

### 🎉 重大重构

这是一个完全重构的版本，旨在提升代码质量、可维护性和可测试性。

#### 架构改进
- **模块化设计**: 将1663行的main.py拆分为多个职责单一的模块
  - `PluginInitializer`: 负责插件初始化逻辑（380行）
  - `EventHandler`: 负责事件处理（450行）
  - `CommandHandler`: 负责命令处理（220行）
  - `ConfigManager`: 集中配置管理（95行）
  - main.py简化至280行，只保留插件注册和生命周期管理

#### 新增模块
- **异常处理系统** (`core/exceptions.py`)
  - 定义了8个自定义异常类
  - 统一的错误码体系
  - 清晰的异常继承关系

- **配置管理器** (`core/config_manager.py`)
  - 集中配置加载和验证
  - 支持点号分隔的嵌套键访问
  - 提供便捷的配置节访问属性

- **插件初始化器** (`core/plugin_initializer.py`)
  - 非阻塞初始化机制
  - Provider等待和重试逻辑
  - 清晰的初始化状态管理
  - 自动数据库迁移和索引重建

- **事件处理器** (`core/event_handler.py`)
  - 统一处理所有事件钩子
  - 群聊消息捕获
  - 记忆召回和反思
  - 消息去重机制

- **命令处理器** (`core/command_handler.py`)
  - 统一处理所有命令
  - 清晰的命令响应格式
  - 完善的错误处理

#### 测试基础设施
- 创建了完整的测试目录结构
- 添加了pytest配置文件
- 编写了ConfigManager和异常模块的单元测试
- 为后续测试覆盖奠定基础

#### 代码质量提升
- **代码量优化**: 核心代码从1663行优化至1483行（减少11%）
- **职责分离**: 每个模块职责单一，易于理解和维护
- **可测试性**: 模块解耦，支持依赖注入，易于测试
- **错误处理**: 统一的异常体系和错误处理流程
- **配置管理**: 集中化的配置加载和验证

#### 文档完善
- 新增 `REFACTOR_FEATURE_ANALYSIS.md`: 详细的功能分析文档
- 新增 `REFACTOR_PLAN.md`: 完整的重构计划文档
- 所有新模块都有完整的文档字符串

### 保持不变
- ✅ 所有现有功能完全保留
- ✅ 数据库结构完全兼容
- ✅ 配置文件格式完全兼容
- ✅ 所有公开API接口保持不变
- ✅ 向后兼容旧版本数据

### 技术债务清理
- 移除了重复的代码
- 统一了日志记录格式
- 规范了错误处理流程
- 优化了初始化逻辑

---

## [1.5.18] - 2025-11-06

### 工作流优化
- 创建了全新的 GitHub Actions 工作流系统
- 自动化版本发布流程
- 智能 Issue 管理

---

注意：请在每次发版前更新此文件，将 [Unreleased] 部分的内容移动到新版本号下。