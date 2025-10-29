# LivingMemory - 动态生命周期记忆插件

为 AstrBot 打造的智能长期记忆插件。

**版本**: v1.5.0 | **作者**: lxfight | **许可证**: AGPLv3

---

## 核心特性

- **混合检索**: 结合 BM25 稀疏检索和 Faiss 向量检索，使用 RRF 融合算法
- **智能总结**: 使用 LLM 自动总结对话历史，生成结构化记忆
- **会话隔离**: 支持按人格和会话隔离记忆
- **自动遗忘**: 基于时间和重要性的智能清理机制
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
| `/lmem search <query> [k]` | 搜索记忆 |
| `/lmem forget <id>` | 删除指定记忆 |
| `/lmem rebuild-index` | 重建索引 |
| `/lmem webui` | 查看 WebUI 信息 |
| `/lmem help` | 显示帮助 |

---

## 数据迁移（v1.4.0-1.4.2）

如果您从 v1.4.0-1.4.2 版本升级，旧数据可能无法自动迁移。手动恢复步骤：

1. 找到备份文件：`data/plugin_data/astrbot_plugin_livingmemory/backups/livingmemory_backup_<时间戳>.db`
2. 将该文件移动到：`data/plugin_data/astrbot_plugin_livingmemory/`
3. 重命名为：`livingmemory.db`
4. 重载插件，系统会自动加载和处理数据


---

## 更新记录

### v1.5.0 (2025-10-29)

**重大更新 - 彻底重构**

- ✨ **完全重构**: 对整个插件进行了彻底的架构重构，优化代码结构和性能
- 🔄 **数据迁移**: 保证了旧版本数据的完整迁移，无缝升级体验
- ✅ **充分测试**: 经过全面的测试验证，确保基础功能稳定可靠
- 🎯 **代码精简**: 重构并精简了代码实现，提升可维护性和可读性

---

## 下一步计划（v1.6.0+）

- [ ] 优化对话总结提示词，提升记忆质量。
- [ ] 优化群聊消息的处理，提升多用户场景下的记忆表现。
- [ ] 丰富WebUI功能，提升用户体验。
- [ ] 优化记忆检索算法，提升检索效率和准确性。

---

## 支持

- **GitHub**: [astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
- **问题反馈**: [GitHub Issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues)
- **QQ 群**: [![加入QQ群](https://img.shields.io/badge/QQ群-953245617-blue?style=flat-square&logo=tencent-qq)](https://qm.qq.com/cgi-bin/qm/qr?k=WdyqoP-AOEXqGAN08lOFfVSguF2EmBeO&jump_from=webapi&authKey=tPyfv90TVYSGVhbAhsAZCcSBotJuTTLf03wnn7/lQZPUkWfoQ/J8e9nkAipkOzwh)
  （口令：lxfight）

---

## 许可证

本项目遵循 AGPLv3 许可证。
