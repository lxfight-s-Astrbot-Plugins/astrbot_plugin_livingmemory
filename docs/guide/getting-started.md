# 快速开始

LivingMemory 是一个 AstrBot 长期记忆插件。它会在普通对话之外维护一套长期记忆库，让机器人能记住稳定偏好、长期项目、人物关系、群聊背景和历史约定。

## 安装

1. 将插件目录放到 AstrBot 的 `data/plugins/` 目录下。
2. 重启或重载 AstrBot。
3. AstrBot 会根据 `requirements.txt` 自动安装 Python 依赖。
4. 打开 AstrBot 插件配置页面，找到 `LivingMemory`。

## 必需配置

| 配置项 | 作用 | 建议 |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | 生成记忆向量，用于语义检索 | 留空可使用 AstrBot 默认 Embedding |
| `provider_settings.llm_provider_id` | 总结对话、评估记忆 | 留空可使用默认 LLM，建议选择推理能力稳定的模型 |
| `bot_language` | 命令与状态回复语言 | `zh`、`en`、`ru` |

## 推荐配置

| 场景 | 建议 |
| --- | --- |
| 私聊助手 | 开启人格隔离与会话隔离，避免不同身份之间串记忆 |
| 群聊长期陪伴 | 开启 `enable_full_group_capture`，让插件捕获未直接 @Bot 的群聊上下文 |
| Agent / Tool Loop | 保持主动记忆工具开启，让模型在需要时自行回忆或写入 |
| Gemini Provider | 选择 `fake_tool_call` 时会自动降级到 `extra_user_content` |
| DeepSeek V4 thinking | 现在可以直接使用普通 `fake_tool_call`，旧的 `fake_tool_call_deepseek_v4` 仅作兼容别名 |

## 打开管理页面

AstrBot 版本建议为 `4.24.2` 或更高。进入：

`插件 -> LivingMemory -> Pages -> dashboard`

在这里可以查看记忆列表、调试召回、管理备份，并通过图谱视图观察实体关系。

## 验证是否工作

发送几轮对话后，可以使用：

```text
/lmem status
/lmem summarize
/lmem search 你的关键词
```

如果能看到记忆数量和搜索结果，说明基础链路已经跑通。
