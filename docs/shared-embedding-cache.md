# 统一向量缓存

LivingMemory 可选择开启公共向量缓存，供使用同一上游提供商的插件复用向量。
支持 AstrBot 的 DashScope 原生文本 Embedding 和 OpenAI 兼容文本 Embedding。
已有原生 `text-embedding-v4` 配置和缓存键保持兼容，添加缓存本身不改变提供商
ID、模型、维度或类型，不会因此触发 FAISS 全量重建。

## 接入方式

### 1. 先配置真实上游

在 AstrBot「模型提供商 → 嵌入」中添加提供商。百炼原生文本模型选择 DashScope
Embedding；支持 OpenAI `/embeddings` 协议的云服务或本地服务选择 OpenAI
Embedding。填服务商的 API Base URL、上游 API Key、真实模型名和实际输出维度，
启用并测试成功。多模态、Ollama 等其他原生协议目前不直接支持；只有服务提供
OpenAI 兼容的文本向量接口时，才可通过 OpenAI Embedding 接入。

### 2. 在 LivingMemory 选择提供商并填写缓存

`provider_settings.embedding_provider_id` 选择上一步的 **Provider ID**，例如
`openai_embedding_1`。它是 AstrBot 配置实例的 ID，不是模型名。

在「统一向量缓存」中开启 `enabled`，按下表填写：

| 配置字段 | 填写方法 |
| --- | --- |
| `model_name` | 除原生 DashScope v4 外必填。复制提供商的「嵌入模型」，大小写、斜杠保持一致；不要填写 Provider ID |
| `dimensions` | 除原生 DashScope v4 外必填。复制并核实提供商的实际输出维度，范围 1–16384；原生 v4 可填 0 自动读取 |
| `upstream_batch_size` | 查服务商的单次输入条数上限，填 1–128；默认 10，不确定先填 1。DashScope 原生最多按 10 条拆批 |
| `port` | 默认 17695；更改后所有独立客户端也要改地址 |
| `revision` | 默认 `v4-cache-1`。上游同名模型确认更换向量空间后再修改，不要为普通升级随意修改 |

模型名和维度必须与选中的提供商一致，填错会阻止缓存启动并给出校验提示。
维度应以官方模型文档和实际接口测试为准，不能仅按模型名猜测。
保存后重载 LivingMemory，检查 `/health` 中的真实模型名与维度。

### 填写示例

| 场景 | AstrBot 提供商类型 | 上游 Base URL | 模型名（两处一致） | 维度（两处一致） | AstrBot 维度参数发送模式 |
| --- | --- | --- | --- | --- | --- |
| 百炼原生 v4 | DashScope Embedding | `https://dashscope.aliyuncs.com/api/v1` | `text-embedding-v4` | 例如当前使用的 1024 | 原生协议，不用设置 |
| OpenAI 官方 | OpenAI Embedding | `https://api.openai.com/v1` | `text-embedding-3-small` | 默认 1536；自定义维度按实际值填写 | `auto`，官方支持 dimensions |
| 硅基流动 BGE-M3 | OpenAI Embedding | `https://api.siliconflow.cn/v1` | `BAAI/bge-m3` | 固定 1024 | `never`，不向上游发送可选 dimensions |
| 本地 OpenAI 兼容服务 | OpenAI Embedding | 该服务的实际 `/v1` 地址 | 服务公布的模型名 | 文档和 API 实测值 | 不支持 dimensions 则选 `never` |

对 OpenAI 官方和 BGE-M3 的示例，缓存 `model_name`、`dimensions` 分别填写表中
模型名和维度。原生 v4 可保留 `model_name=""`、`dimensions=0` 自动读取。
批量大小按账号限制填写；客户端单条最大长度限制不替代上游 Token 限制。

LivingMemory 包装匹配的 AstrBot 提供商实例，因此使用这些实例的插件、知识库
可以共享缓存。每两秒检查新增实例；插件卸载时恢复原方法。

### 3. 新插件如何使用

插件支持选择 AstrBot Embedding Provider 时，直接选择与 LivingMemory 相同
的 Provider ID。插件自行使用 OpenAI SDK 时，改成下面的本机入口：

独立客户端可连接 `http://127.0.0.1:17695/v1`：

- 模型：与缓存实际模型名一致，如 `BAAI/bge-m3`，可通过 `GET /v1/models` 查询
- API Key：插件数据目录 `embedding_gateway.token` 中的本机令牌
- 可选请求头：`X-Embedding-Caller: bilibot`
- 路径：`POST /v1/embeddings`
- 输入：字符串或字符串数组（最多 128 条、单条最多 32768 字符；仍须遵守所选模型的 Token 上限）
- 返回：OpenAI 兼容浮点数组或 base64；维度必须等于配置维度

只监听本机地址。本机其他账号仍需令牌，禁止将令牌写入源码或日志。
上游 API Key 只填在 AstrBot 原始提供商中；本机令牌只填在调用缓存的客户端中。
不要把原始上游提供商的 URL 改成缓存地址，否则会形成循环调用，缓存会拒绝启动。
Docker 客户端不能直接把容器 loopback 当作服务器 loopback。
硬编码上游地址、使用其他提供商实例或其他模型的调用不自动接入缓存。
每个缓存入口当前只提供一个上游模型；它不同时代理所有用户的不同模型。

### 常见填写错误

- **缓存未启动，提示 `fill shared_embedding_cache`**：其他模型漏填了真实模型名或输出维度。
- **提示 `not Provider ID`**：模型名误填为 `openai_embedding_1`。该 ID 放在提供商选择项中，模型名填写如 `BAAI/bge-m3`。
- **提示 `actual output dimensions`**：缓存维度和 AstrBot 提供商不一致；核实实际模型输出，再把两处设为相同值。
- **上游报不支持 `dimensions`**：固定维度模型的 AstrBot「嵌入维度参数发送模式」设为 `never`，保留正确的本地维度。
- **单条可用、批量报错**：把 `upstream_batch_size` 降到服务商支持的数量，不确定先设为 1；文本过长则调整调用插件的分块。
- **缓存接口返回 401**：使用 `embedding_gateway.token` 中的本机令牌，不是云服务密钥或 Dashboard 令牌。
- **更换模型后无法召回**：不同模型即使维度相同也不共享向量空间。先备份，再按插件指纹检查和索引维护流程重建旧向量；仅改 `revision` 不能修复旧索引。

## 语义与计量

同上游地址、账号密钥、协议、模型、维度、请求维度选项、缓存版本及
**完全相同文本**才能复用。
不裁掉时间、空格、标点，不使用模糊语义缓存。BiliBot 与 LivingMemory 对同一
事件生成不同正文时仍分别计算。缓存文本不会自动成为长期记忆。

缓存仅保存输入哈希、向量和时间，不保存请求正文或凭据。保存原始浮点精度，
与记忆库、FAISS、原子及图谱表分离；上限 10000 条，默认有效期 90 天。
另保留最多 256 条内存缓存，避免磁盘暂时写入失败后立即重复计算已付费向量。
`revision` 变化只隔离缓存，不能保证已存向量与真实更换后的模型空间兼容。

并发相同输入合并，未命中项按 `upstream_batch_size` 拆批，DashScope 原生最多
10 条，默认上游并发 2，待计算输入最多 256 条。
HTTP 请求取消不会取消已开始的共享计算；成功批次立即保存，后续批次失败不重算
已成功的批次。上游错误不缓存、不自动重试，调用方遵守退避策略。
缓存冷启动只按需求填充，不自动扫描或重建全部记忆。

`usage.prompt_tokens` 与 `usage.total_tokens` 表示当前请求实际发起的上游
计算消耗，完全命中时为 0；并发等待者不重复计量。这不同于原始输入的理论 tokens。
上游返回了有效向量但未返回 Token 用量时，首次实际调用的这两个字段为 `null`
（未知），不伪造为 0；向量仍缓存，后续命中为 0。依赖严格整数用量字段的客户端
需要能接受未知值，或改用 AstrBot Provider 调用方式。
`X-Embedding-Cache` 提供命中、未命中与并发合并条数。
`GET /metrics` 需要同一令牌，输出启动以来的计量及调用方分组；SQLite
`usage_totals` 另保存成功缓存批次的累计上游请求、输入条数和实际 tokens。
`since_start.unknown_upstream_requests`、调用方的 `unknown_requests` 和
SQLite `usage_totals.unknown_requests` 单独累计未知用量请求，tokens 是已知部分
的合计。旧表自动添加字段，不清空缓存。
客户端取消时以服务端累计计量为准，不把未知失败或网络超时计为免费。

`GET /health` 返回本机健康状态、真实模型名、维度；`GET /v1/models` 需要本机
令牌，只列出该入口配置的真实模型。缓存或接口故障返回错误，保留调用方现有的
检索降级；不自动让所有调用方绕过缓存并放大上游请求。
缓存条目损坏或到期只重算相关输入。模型、账号或地域变化需要重载以建立新命名空间。

## 旧缓存计量表的兼容性

升级自动为 `usage_totals` 添加 `unknown_requests`，已有缓存向量和已知用量
保留。首次开启缓存无需迁移记忆库；更换真实模型仍需按正常索引维护流程处理。

回滚到只有四列 `usage_totals` 的旧缓存代码时，还需处理新增的
`unknown_requests` 列：停止 AstrBot，先用 SQLite backup 备份当前缓存数据库，
再执行 `ALTER TABLE usage_totals DROP COLUMN unknown_requests`，恢复旧代码后启动。
该步骤仅回退计量表结构，保留部署后新增的缓存向量；旧版 SQLite 不支持
`DROP COLUMN` 时，需通过新建四列计量表迁移，不能直接删除缓存或记忆数据库。
