# 快速开始

LivingMemory 是一个 AstrBot 长期记忆插件。它会在普通对话之外维护一套长期记忆库，让机器人能记住稳定偏好、长期项目、人物关系、群聊背景和历史约定。

## 安装

当前稳定版为 **2.9.0**（上一版本 2.8.4）。指定标签下载、升级和回退步骤见[版本与升级](/releases)。

1. 将插件目录放到 AstrBot 的 `data/plugins/` 目录下。
2. 重启或重载 AstrBot。
3. AstrBot 会根据 `requirements.txt` 自动安装 Python 依赖。
4. 打开 AstrBot 插件配置页面，找到 `LivingMemory`。

若管理页面的接口均提示“未找到该路由”（[#374](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/374)），请确认运行 AstrBot 的环境已安装 `networkx`：该问题的报告环境安装此库后恢复正常，依赖文件已包含 `networkx>=3.0`。可在 AstrBot 根目录使用运行 AstrBot 的 Python 环境执行以下命令安装完整依赖，然后重新启动：

```bash
python -m pip install -r data/plugins/astrbot_plugin_livingmemory/requirements.txt
```

插件直接使用的 Python 库由 `requirements.txt` 声明；`faiss-cpu` 沿用 AstrBot 提供的版本，避免覆盖宿主的运行时。

## 写入时间戳错误

若写入记忆时出现 `Datetime values must have timezone information`（[#370](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/370)），请升级 **AstrBot 至 4.29.0 或更高版本**，然后重新启动。升级前保留 AstrBot 配置与插件数据备份。

SQLModel 从 0.0.45 起将默认的 `datetime` 字段改为 UTC 时区类型；AstrBot 4.24.2 等旧版本的文档模型仍写入无时区时间，因此会拒绝写入。AstrBot [#10211](https://github.com/AstrBotDevs/AstrBot/pull/10211) 已明确声明原有本地时间类型，并随 4.29.0 发布。修复位于宿主，单独更新 LivingMemory 无法替换该模型；升级后保留历史时间戳语义，无需重建嵌入。

## 必需配置

> 高级配置（召回、反思、图谱、原子、整合、备份等）已迁移到 Dashboard 的「设置」页；AstrBot 插件配置页只保留 Provider 与语言。


| 配置项 | 作用 | 建议 |
| --- | --- | --- |
| `provider_settings.embedding_provider_id` | 生成记忆向量，用于语义检索 | 必填：填写 AstrBot 中已启用的 Embedding Provider ID（如 `openai_embedding`） |
| `provider_settings.llm_provider_id` | 总结对话、评估记忆 | 留空可使用默认 LLM，建议选择推理能力稳定的模型 |
| `bot_language` | 命令与状态回复语言 | `zh`、`en`、`ru`、`ja` |

## 嵌入模型

LivingMemory 的语义召回依赖 Embedding Provider，而不是用于聊天的 LLM。推荐免费的 `BAAI/bge-m3`（1024 维）；从获取密钥、创建 Provider 到查找 ID 的完整步骤，见[嵌入模型与 BGE-M3](/guide/embedding-model)。

## 推荐配置

| 场景 | 建议 |
| --- | --- |
| 私聊助手 | 开启人格隔离与会话隔离，避免不同身份之间串记忆 |
| 群聊长期陪伴 | 开启 `enable_full_group_capture`，让插件捕获未直接 @Bot 的群聊上下文 |
| Agent / Tool Loop | 保持主动记忆工具开启，让模型在需要时自行回忆或写入 |
| 注入方式 | 仅支持 `extra_user_content`（add-only 临时片段，不写入对话历史）；旧方式自动回退 |

## 打开管理页面

AstrBot 版本建议为 `4.24.2` 或更高。进入：

`插件 -> LivingMemory -> 视图 -> dashboard`（旧版 AstrBot 显示为 `Pages`）

在这里可以查看记忆列表、调试召回、管理备份，并通过图谱视图观察实体关系。

## 验证是否工作

发送几轮对话后，可以使用：

```text
/lmem status
/lmem summarize
/lmem search 你的关键词
```

如果能看到记忆数量和搜索结果，说明基础链路已经跑通。
