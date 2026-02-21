# LivingMemory - 动态生命周期记忆插件

**版本**: v2.1.9 | **作者**: lxfight | **许可证**: AGPLv3

---

## 核心特性

- **混合检索**: 结合 BM25 稀疏检索和 Faiss 向量检索，使用 RRF 融合算法
- **智能总结**: 使用 LLM 自动总结对话历史，生成结构化记忆
- **双通道总结**: `canonical_summary`（事实导向，用于检索）与 `persona_summary`（人格风格，用于注入）解耦存储
- **会话隔离**: 支持按人格和会话隔离记忆
- **自动遗忘**: 基于时间和重要性的智能清理机制
- **数据安全**: 迁移前自动备份、索引重建带备份回滚、删除操作带事务保护
- **WebUI 管理**: 可视化记忆管理界面

---

## 快速开始

### 安装

将插件文件夹放置于 AstrBot 的 `data/plugins` 目录下，AstrBot 将自动安装依赖。

### 配置

通过 AstrBot 控制台的插件配置页面进行配置：

**必需配置**:
- `embedding_provider_id`: 向量嵌入模型 ID（留空使用默认）
- `llm_provider_id`: 大语言模型 ID（留空使用默认）

**WebUI 配置**:
```json
{
  "webui_settings": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8080,
    "access_password": "your_password"
  }
}
```

---

## 命令

| 命令 | 说明 |
| :--- | :--- |
| `/lmem status` | 查看记忆库状态 |
| `/lmem search <query> [k]` | 搜索记忆（默认 5 条） |
| `/lmem forget <id>` | 删除指定记忆 |
| `/lmem rebuild-index` | 重建索引（修复索引不一致） |
| `/lmem webui` | 查看 WebUI 信息 |
| `/lmem reset` | 重置当前会话记忆上下文 |
| `/lmem cleanup [preview\|exec]` | 清理历史消息中的记忆注入片段（默认 preview 预演） |
| `/lmem help` | 显示帮助 |

---

## 架构说明

### 模块结构

```
astrbot_plugin_livingmemory/
├── main.py                          # 插件注册和生命周期管理
├── core/
│   ├── base/                        # 基础组件（配置、常量、异常）
│   ├── managers/                    # 核心管理器（MemoryEngine、ConversationManager）
│   ├── retrieval/                   # 检索层（HybridRetriever、BM25、向量）
│   ├── validators/                  # 验证器（IndexValidator）
│   ├── plugin_initializer.py        # 插件初始化器
│   ├── event_handler.py             # 事件处理器
│   └── command_handler.py           # 命令处理器
├── storage/                         # 存储层（DBMigration、ConversationStore）
├── webui/                           # Web 管理界面
├── tests/                           # 测试套件
└── docs/                            # 文档
```

### 核心组件

1. **PluginInitializer**: 负责插件初始化
   - 非阻塞初始化机制
   - Provider等待和重试
   - 自动数据库迁移

2. **EventHandler**: 处理事件钩子
   - 群聊消息捕获
   - 记忆召回
   - 记忆反思

3. **CommandHandler**: 处理命令
   - 统一命令响应格式
   - 完善的错误处理

4. **ConfigManager**: 配置管理
   - 集中配置加载
   - 配置验证
   - 嵌套键访问

---

## 开发者指南

### 测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试
pytest tests/test_config_manager.py

# 查看覆盖率
pytest --cov=core tests/
```


### 文档

- [API文档](docs/API.md): 详细的API参考
- [架构文档](docs/ARCHITECTURE.md): 系统架构说明
- [开发者指南](docs/DEVELOPMENT.md): 开发和贡献指南

---

## 数据迁移（v1.4.0-1.4.2）

如果您从 v1.4.0-1.4.2 版本升级，旧数据可能无法自动迁移。手动恢复步骤：

1. 找到备份文件：`data/plugin_data/astrbot_plugin_livingmemory/backups/livingmemory_backup_<时间戳>.db`
2. 将该文件移动到：`data/plugin_data/astrbot_plugin_livingmemory/`
3. 重命名为：`livingmemory.db`
4. 重载插件，系统会自动加载和处理数据

---

## 更新记录

详见 [CHANGELOG.md](CHANGELOG.md)

---

## 支持

- **GitHub**: [astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
- **问题反馈**: [GitHub Issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)
- **QQ 群**: [![加入QQ群](https://img.shields.io/badge/QQ群-953245617-blue?style=flat-square&logo=tencent-qq)](https://qm.qq.com/cgi-bin/qm/qr?k=WdyqoP-AOEXqGAN08lOFfVSguF2EmBeO&jump_from=webapi&authKey=tPyfv90TVYSGVhbAhsAZCcSBotJuTTLf03wnn7/lQZPUkWfoQ/J8e9nkAipkOzwh)
  （口令：lxfight）

---

## 许可证

本项目遵循 AGPLv3 许可证。
