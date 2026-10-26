# 嵌入模型与 BGE-M3

LivingMemory 用关键词和向量两套线索找回记忆。向量由嵌入模型（Embedding Model）生成。它不负责聊天或总结，但决定了“意思相近”的记忆能不能被找到。

## 嵌入模型在做什么

嵌入模型把一段文字映射成固定长度的数字向量，例如 BGE-M3 输出 1024 个浮点数。语义越接近的文本，向量方向通常也越接近。LivingMemory 写入记忆时把正文向量化，查询时再把问题向量化，最后在 FAISS 中寻找距离最近的记忆。

因此有两条使用规则：

- 它擅长处理改写、同义表达和跨语言问法；关键词检索仍然负责精确命中。
- 建立索引和查询必须使用同一个模型、同一维度。更换模型后旧向量不可比较，需要重建索引。

## 为什么推荐 BGE-M3

[BAAI/bge-m3](https://siliconflow.cn/models/BAAI/bge-m3) 是多语言文本嵌入模型，对中文语义召回友好，也支持较长输入。硅基流动提供兼容 OpenAI Embeddings 的免费 API，适合作为 LivingMemory 的入门选择。免费价格、限流和可用性可能调整，以控制台为准。

| 项目 | 值 |
| --- | --- |
| API 模型 ID | `BAAI/bge-m3` |
| 向量维度 | `1024` |
| 最大输入 | 8192 tokens |
| 多语言 | 支持 100 多种语言 |
| 硅基流动计费 | 模型页当前标为免费 |

模型网页或接口列表中的数字 `modelId` 不是这里要填的模型名。AstrBot 的“嵌入模型”字段填写 `BAAI/bge-m3`。

## 在 AstrBot 中配置

### 1. 获取硅基流动 API Key

登录[硅基流动控制台](https://cloud.siliconflow.cn/account/ak)创建 API Key。密钥只显示一次，复制后妥善保存。

### 2. 创建 Embedding Provider

进入 AstrBot 后台的“模型提供商（旧版本称 LLM 配置）”，切到“嵌入”，点击“新增模型提供商”，选择 **OpenAI Embedding**。按下面的值填写：

| 字段 | 填写内容 |
| --- | --- |
| Provider ID | `openai_embedding`（以 AstrBot 页面实际显示为准） |
| API Key | 硅基流动 API Key |
| API Base URL | `https://api.siliconflow.cn/v1` |
| 嵌入模型 | `BAAI/bge-m3` |
| 嵌入维度 | `1024` |
| 嵌入维度参数发送模式 | `auto` |

Provider ID 由 AstrBot 生成并在提供商列表或配置标题中显示。同名时可能自动变成 `openai_embedding_1` 等值，保持默认即可，不要把它和模型 ID `BAAI/bge-m3` 混为一谈。

保存后启用该提供商，并点击“测试”。测试会实际请求一次 Embedding API，成功即表示密钥、地址、模型名和维度可用。

### 3. 接入 LivingMemory

打开 LivingMemory 插件配置，把 `provider_settings.embedding_provider_id` 填为 AstrBot 里显示的 Provider ID，例如 `openai_embedding`。保存后重载插件。

发送几轮对话，再执行：

```text
/lmem status
/lmem search 你记得的关键词
```

状态正常且搜索能返回记忆，说明向量链路已经工作。

## 常见问题

- **401 或鉴权失败**：重新复制 API Key，确认没有多余空格，并确认密钥属于当前硅基流动账号。
- **404**：API Base URL 应是 `https://api.siliconflow.cn/v1`，不要填写具体接口路径。
- **维度不匹配**：BGE-M3 固定输出 1024 维，应把嵌入维度设为 `1024`；旧索引来自其他模型时，执行 `/lmem rebuild-index` 重建。
- **429 或限流**：索引重建时增大 `index_rebuild_settings.request_delay`，并保持较小批量。

更换嵌入模型会改变向量空间。插件会通过 Provider 指纹检查触发完整向量重建，也可手动执行 `/lmem rebuild-index`；图谱索引是独立流程，需要时再执行 `/lmem rebuild-graph`。确定模型后尽量不要频繁更换。
