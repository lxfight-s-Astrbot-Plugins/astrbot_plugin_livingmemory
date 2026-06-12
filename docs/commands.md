# 命令速查

LivingMemory 的命令统一使用 `/lmem` 前缀。

| 命令 | 说明 |
| --- | --- |
| `/lmem status` | 查看记忆库状态 |
| `/lmem search <query> [k]` | 搜索长期记忆，`k` 默认为 5 |
| `/lmem forget <id>` | 删除指定记忆 |
| `/lmem rebuild-index` | 重建文档索引 |
| `/lmem rebuild-graph` | 重建图谱记忆索引 |
| `/lmem webui` | 查看 WebUI 入口信息 |
| `/lmem summarize` | 立即总结当前会话 |
| `/lmem reset` | 重置当前会话记忆上下文 |
| `/lmem cleanup [preview\|exec]` | 清理历史消息中的旧记忆注入片段 |
| `/lmem help` | 显示帮助 |

## 常用排查

| 现象 | 建议 |
| --- | --- |
| 搜不到刚聊过的内容 | 先执行 `/lmem summarize`，确认对话已经写入长期记忆 |
| 记忆明显串到其他人格 | 检查 `filtering_settings.use_persona_filtering` 是否开启 |
| 群聊上下文不完整 | 检查 `session_manager.enable_full_group_capture` 是否开启 |
| 索引疑似异常 | 执行 `/lmem rebuild-index`，图谱异常则执行 `/lmem rebuild-graph` |
