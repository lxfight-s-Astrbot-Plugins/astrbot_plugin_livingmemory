# 版本与升级

## 2.7.1 · 2026-10-22

2.7.1 是 2.7.0 之后的补丁版本，聚焦配置体验与重复记忆：把高级配置从 AstrBot 插件配置页迁移到 Dashboard「设置」页（常用设置 + 5 类折叠分组），并新增写入时近重复合并。不引入新的运行时依赖，也没有强制迁移步骤；稳定版会正常参与 AstrBot 的版本更新提示。

[下载 2.7.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.1)

### 本次更新

- **配置页精简**：AstrBot 插件配置页只保留模型 Provider（含必填 Embedding Provider ID）与回复语言；召回、反思、图谱、整合等高级配置迁移到 Dashboard。
- **Dashboard 设置页**：顶部 8 个常用设置，其余按「记忆生成与生命周期、召回与工具、图谱与原子、访问与隔离、系统与维护」5 类折叠；支持搜索、一键展开/收起与四语字段说明。
- **写入时近重复合并**：同会话/人格内相似度达到阈值（默认 0.92）的总结记忆合并到已有记忆，保留主题、关键事实、原子与来源消息；失败时回退为新增。
- **Embedding Provider 说明修正**：明确必须显式填写；留空只回退到第一个可用 Provider，启动日志会输出 warning，避免向量/图索引初始化失败。

### 验证

- 后端 `pytest` 1,145 项、前端 109 项全部通过；CI 通过。
- 版本一致性与中英文档 parity 守卫通过；Dashboard 构建产物与源码一致。

### 升级说明

1. 无需修改配置，也没有强制迁移步骤。
2. 若尚未填写 Embedding Provider ID，请先在 AstrBot「LLM 配置」中启用后填写，否则向量/图索引可能初始化失败。
3. 保存 Dashboard 设置后重载插件，使初始化阶段读取的配置生效；近重复合并等运行时选项即时生效。
4. 旧记忆需要补图数据时可执行 `/lmem rebuild-graph`；更换 Embedding 模型后执行 `/lmem rebuild-index`。
5. 稳定版为 2.7.1；历史预发布版本仍保留在 Releases 页面。

### 安装与升级

1. 停止 AstrBot，备份插件代码、插件数据目录及配置。
2. 从发布页下载 **Source code (zip)**，解压到 `data/plugins/astrbot_plugin_livingmemory`，保留原数据与配置。
3. 启动或重载 AstrBot，确认插件版本为 `2.7.1`，打开 `插件 → LivingMemory → Pages → dashboard`。
4. 通过 Git 管理插件代码时，可在干净工作目录切换标签后启动：

```bash
git fetch origin --tags
git switch --detach 2.7.1
```

## 2.7.0 · 2026-10-21

2.7.0 稳定版发布，整合 2.7.0-beta.1 至 `beta.4` 的全部改动。管理面板重做、召回质量、注入规范化、记忆生命周期、热路径性能与日语支持均进入稳定通道；稳定版不是预发布版本，会正常参与 AstrBot 的插件版本更新提示。不引入新的运行时依赖，无强制迁移步骤，现有配置可直接沿用。

[下载 2.7.0](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0)

### 本版本包含

- **管理面板**：记忆来源与写入动机、召回路线分项得分、图谱与记忆双向联动、可重试错误状态和四种视觉风格。
- **召回质量**：分数级加权融合、候选扩展、跨路一致性奖励、中文 BM25 字符二元组与原子生命周期过滤。
- **一致性**：注入统一为 add-only 临时片段；WebUI 编辑/重试回滚对账、修订号与游标原子提交、失败补偿删除。
- **生命周期**：总结窗口按天/空闲切分、类型化原子 TTL 与衰减、整合回滚、归档可恢复、迁移前置备份。
- **性能与索引**：召回热路径解除 FAISS 落盘阻塞、写操作 journal 删除（单条写入 8→2 事务）、Dashboard 载荷瘦身、索引维护后台化与影子代际切换。
- **工程化与本地化**：CI 固定宿主版本、测试依赖锁定、版本与文档守卫、完整日语支持（命令回复、配置页、Dashboard）。

### 验证

- 后端 `pytest` 1,132 项与前端 107 项全部通过；CI 在固定宿主版本上运行。
- 中文与英文文档通过 parity 守卫；Dashboard 构建产物与源码一致。

### 升级说明

1. 无需修改配置，新增项均有默认值。
2. 升级后首次启动会自动补齐版本与索引信息；建议执行一次 `/lmem rebuild-index`，让旧记忆获得检索侧优化。
3. 召回默认值变化：`fusion_mode=score`、`fusion_bm25_weight=0.5`、`recall_candidate_multiplier=3`、`fusion_agreement_bonus=0.25`；设置 `fusion_mode=rrf` 可回到旧的排名级融合。
4. 插件最低要求 AstrBot 4.24.2；`@register` 已移除，元数据来自 `metadata.yaml`。
5. Dashboard 偏好（主题、明暗、语言）保存在浏览器本地，升级不受影响。

### 版本脉络

| 版本 | 重点 |
| --- | --- |
| 2.7.0-beta.1 | 管理面板视觉重做、图谱阅读稳定、编辑与导入流程 |
| 2.7.0-beta.2 | 记忆注入规范化（add-only），清理历史格式改写 |
| 2.7.0-beta.3 | 召回来源可见、分数融合与中文二元组、自主回忆 Beta |
| 2.7.0-beta.4 | 热路径性能、一致性加固、架构收敛、日语支持与载荷瘦身 |

### 安装与升级

1. 停止 AstrBot，备份插件代码、插件数据目录及配置。
2. 从发布页下载 **Source code (zip)**，解压到 `data/plugins/astrbot_plugin_livingmemory`，保留原数据与配置。
3. 启动或重载 AstrBot，确认插件版本为 `2.7.0`，打开 `插件 → LivingMemory → Pages → dashboard`。
4. 通过 Git 管理插件代码时，可在干净工作目录切换标签后启动：

```bash
git fetch origin --tags
git switch --detach 2.7.0
```

## 2.7.0-beta.4 · 2026-10-21

本次维护性预发布覆盖 PR 308–332（25 个合并请求）：召回与写入热路径性能、数据一致性加固、架构职责收敛，以及 Dashboard 载荷与日语支持。不引入新的运行时依赖，无强制迁移步骤，现有配置可直接沿用；稳定通道仍为 2.6.1。

[下载 2.7.0-beta.4](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.4)

### 本次更新

- **召回更快**：FAISS 落盘改为读写锁，检索不再被整索引 flush 阻塞；LLM 钩子不再轮询初始化；启动索引读取移出事件循环；图路补齐反向边索引并复用只读连接；访问统计合并写；分词缓存与向量候选复用减少重复计算。
- **写入更省**：删除写操作 journal 与修复链（净减约 930 行），单条记忆写入事务从最多 8 个降为 2 个；批量删除补回提交边界，避免 `database is locked`。
- **数据更稳**：语义整合按 `(session_id, persona_id)` 分组、归档失败回滚；每日衰减只更新变化键、失败不再落日期标记；迁移前置备份并在失败时恢复；原子 FTS 补齐回填。
- **索引更可靠**：重建重试统一指数退避，移除限流字符串猜测；无法删除向量时归档标记 `needs_index_rebuild`，由启动校验重建清理幽灵向量。
- **前端更轻**：lucide 全量库替换为 21 个图标子集（约 -408 KB）；样式与脚本打包压缩，阻塞请求 14 → 5，CSS 85.9 KB → 69.2 KB；CSS 层叠安全去重。
- **架构与工程化**：写侧索引维护拆到 `MemoryIndexWriter`；reflection、`add_memory`、图快照三个大方法拆分为阶段方法；版本表与索引 DDL 单源化；CI 固定已验证宿主提交；测试依赖锁定；移除 `@register`；Dashboard 与配置页支持日语。

### 验证

- 后端 `pytest` 1,132 项全部通过，前端 107 项通过；CI 在固定宿主版本上通过。
- 新增文档中英 parity 守卫与版本一致性守卫，防止后续版本与文档漂移。

### 升级说明

1. 无需修改配置，新增项均有默认值。
2. 升级后首次启动会自动补齐索引与版本信息；索引较旧时可执行一次 `/lmem rebuild-index`。
3. 插件最低要求 AstrBot 4.24.2；`@register` 移除后依赖宿主自动识别继承 `Star` 的插件类。
4. Dashboard 偏好（主题、明暗、语言）保存在浏览器本地，升级不受影响。

### 本次重点 PR

| PR | 改动 | 作者 |
| --- | --- | --- |
| [#308](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/308) | 召回热路径解除 FAISS 落盘阻塞 | [@lxfight](https://github.com/lxfight) |
| [#309](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/309) | 图召回 I/O 优化与计时测试稳定化 | [@lxfight](https://github.com/lxfight) |
| [#310](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/310) | 整合、衰减与迁移可靠性加固 | [@lxfight](https://github.com/lxfight) |
| [#316](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/316) | 删除写操作 journal 与修复/重放链 | [@lxfight](https://github.com/lxfight) |
| [#317](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/317) | lucide 全量库替换为 21 图标子集 | [@lxfight](https://github.com/lxfight) |
| [#319](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/319) | Dashboard 样式与脚本打包压缩 | [@lxfight](https://github.com/lxfight) |
| [#321](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/321) | 移除废弃 `@register` 并固定 CI 宿主 | [@lxfight](https://github.com/lxfight) |
| [#323](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/323) | reflection 343 行主方法拆分 | [@lxfight](https://github.com/lxfight) |
| [#328](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/328) | 后端与配置页日语支持 | [@lxfight](https://github.com/lxfight) |
| [#329](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/329) | Dashboard 日语支持与语言菜单 | [@lxfight](https://github.com/lxfight) |

### 安装与升级

1. 停止 AstrBot，保留现有插件代码、插件数据目录及配置的备份。
2. 从测试版发布页下载 **Source code (zip)**，解压到 `data/plugins/astrbot_plugin_livingmemory`，保留原数据与配置。
3. 启动或重载 AstrBot，确认插件版本为 `2.7.0-beta.4`，打开 `插件 → LivingMemory → Pages → dashboard`。
4. 通过 Git 管理插件代码时，可在干净工作目录切换标签后启动：

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.4
```

## 2.7.0-beta.3 · 2026-10-20

本次预发布覆盖两个方向：管理面板从“展示数据”转为“支持判断”，召回链路的融合方式与索引质量同步提升；另有默认关闭的自主回忆 Beta 可选。本版不引入新的运行时依赖，也没有必须执行的迁移步骤，现有配置可直接沿用；稳定通道仍为 2.6.1。

[下载 2.7.0-beta.3](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.3)

### 本次更新

**管理面板**

- 记忆列表标注每条记忆的写入方式（Agent 主动写入、导入、重新总结、自动总结），摘要质量偏低时单独标记；详情面板补充来源、`memorize_reason`，内容被编辑时显示原记忆 ID。
- 召回测试页按检索路线列出分项得分（文档关键词、文档向量、图关键词、图向量），并在结果头回显本次请求的 `k` 与会话过滤。
- 图谱与记忆列表双向打通：选中节点可列出关联记忆并直接打开，记忆详情提供「在图谱中定位」；节点详情改为非模态，可连续对比多个节点。
- 系统页与提示词页的请求失败改为页面内常驻提示并提供重试，取代一闪而过的 toast；常驻解释性文案已删除，改由控件状态与反馈表达。

**召回质量**

- 文档路融合由按名次等权改为按查询内归一化分数加权（`fusion_mode=score`，`fusion_bm25_weight=0.5`）。融合前每路候选由 `k` 扩大到 `3k`，两路均命中且名次接近的结果叠加一致性奖励（`fusion_agreement_bonus=0.25`）。
- BM25 在 jieba 词元之外索引并查询中文连续段字符二元组，短查询与店名、人名、型号等专有名词不再因索引侧与查询侧切词不一致而漏召回；英文与数字词元行为不变。
- 使用出厂默认参数、经真实检索链路（`MemoryEngine.search_memories()`，`k=10`）实测：中文评测集（1,211 问）MRR `0.86 → 0.95`、Hit@1 `0.79 → 0.93`；英文 LoCoMo（1,536 条带证据问答）MRR `0.66 → 0.73`、Hit@1 `0.53 → 0.62`。中文集为合成数据；LoCoMo 的记忆为原始会话文本，其绝对分数低于生产场景。

**自主回忆（可选 Beta）**

- 开启 `agent_tools.enable_agentic_recall_beta`（需同时开启 `agent_tools.enable_recall_tool`）后，模型可在单次请求内多轮检索：`recall_long_term_memory` 支持按当前发言人查经历、按事件时间范围过滤、排除已看候选与游标分页；新增 `read_memory_evidence`，按记忆 ID 读取有效事实与（整段原文对当前会话可见时）保留原文，事实与原文均以游标返回，截断后可精确续读。
- 单次请求共享三项预算：工具调用次数（`agentic_recall_max_calls`，默认 4）、累计工具耗时（`agentic_recall_time_budget_seconds`，默认 20 秒）与单次返回长度（`agentic_recall_max_result_chars`，默认 12000 字符）。预算耗尽后，模型基于已取得的证据作答。
- 回忆规则提示词以临时片段追加注入，不进入对话历史与插件消息库。关闭该开关时，工具注册、检索与提示词行为与 2.7.0-beta.2 一致。

**同时收录**

- 管理员专用遗忘工具（`agent_tools.enable_forget_tool`，默认关闭）：只做可逆归档，可从 Dashboard 恢复；单次条数与同会话冷却受限。
- `/lmem migrate-scope <target> [apply] [source]`：显式迁移既有记忆作用域，默认仅预览，`apply` 前自动备份数据库。
- `provider_settings.llm_stream_mode`（`auto`/`always`/`never`）：仅接受流式接口的 Provider 现在可用于记忆处理。
- 总结窗口按本地日期与长空闲切分，消息时间改用平台时间戳；会话库与 WebUI 编辑/重试回滚对账。
- FAISS 在非 ASCII 路径下改用纯 ASCII 临时目录写盘；修复多行 JSON 解析与总结提示词前缀缓存。
- 管理面板渲染、触摸、布局与编辑竞态修复；`vue` 3.5.44 与 `source-map-js` 1.2.2 安全更新；补充 zh-CN / ja-JP 插件本地化。

### 升级说明

- 现有配置无需修改，新增参数均有默认值。
- 召回默认值有变化：`fusion_mode=score`、`fusion_agreement_bonus=0.25`、`recall_candidate_multiplier=3`；如需保持旧行为，设置 `fusion_mode: rrf`。
- 如需让既有记忆获得字符二元组召回收益，执行一次 `/lmem rebuild-index`；新写入的记忆自动生效，重建期间召回保持可用。
- 面板偏好（主题、明暗、语言）保存在浏览器本地，升级不受影响。

### 已知限制

- 「全量图谱」模式下节点详情暂不列出关联记忆（该模式的快照不返回 entry 数据；受限概览与检索模式不受影响）。
- 中文评测集由模型生成，用于横向比较检索方案，不代表线上真实数据分布。
- 自主回忆为 Beta：真实模型下的工具调用采纳与端到端表现仍在验证中，开关默认关闭。

### 本次重点 PR

| PR | 改动 | 作者 |
| --- | --- | --- |
| [#274](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/274) | 可选自主多轮回忆 Beta：多轮检索、证据深读与单次请求预算 | [@Sisyphbaous-DT-Project](https://github.com/Sisyphbaous-DT-Project) |
| [#281](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/281) | 修复管理面板渲染、触摸、布局与编辑竞态 | [@lxfight](https://github.com/lxfight) |
| [#299](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/299) | 召回测试页展示检索路线分项得分 | [@lxfight](https://github.com/lxfight) |
| [#300](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/300) | 记忆来源与写入动机可见 | [@lxfight](https://github.com/lxfight) |
| [#301](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/301) | 图谱与记忆双向联动 | [@lxfight](https://github.com/lxfight) |
| [#302](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/302) | 图谱视觉编码自洽与稳定性 | [@lxfight](https://github.com/lxfight) |
| [#303](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/303) | 反馈与状态收敛，修正系统页与提示词页语义 | [@lxfight](https://github.com/lxfight) |
| [#304](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/304) | 清理死 i18n 键、被覆盖样式与装饰性文案 | [@lxfight](https://github.com/lxfight) |
| [#305](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/305) | 召回改为分数级加权融合并加宽候选 | [@lxfight](https://github.com/lxfight) |
| [#306](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/306) | 跨路线一致性奖励 | [@lxfight](https://github.com/lxfight) |
| [#307](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/307) | 中文 BM25 字符二元组 | [@lxfight](https://github.com/lxfight) |

完整变更记录（含此处未列出的修复与依赖更新）见 `CHANGELOG.md`。

### 安装与升级

1. 停止 AstrBot，保留现有插件代码、插件数据目录及配置的备份。
2. 从上方发布页下载 **Source code (zip)**，解压后将代码放到 `data/plugins/astrbot_plugin_livingmemory`；保留原有插件数据与配置，不要覆盖为空目录。
3. 启动或重载 AstrBot，在插件列表确认版本为 `2.7.0-beta.3`，再进入 `插件 → LivingMemory → Pages → dashboard`。
4. 如需让既有记忆获得字符二元组召回收益，发送 `/lmem rebuild-index`（分批执行，期间召回保持可用）。

通过 Git 管理插件代码时，可在干净的插件工作目录中切换到指定标签后启动 AstrBot：

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.3
```

## 2.7.0-beta.2 · 2026-09-16

本次为预发布测试版，覆盖 `2.7.0-beta.1` 之后的重构与行为规范收敛。稳定使用可继续选择 2.6.1。

[下载 2.7.0-beta.2](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.2) · [稳定版 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1) · [完整差异](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/compare/2.7.0-beta.1...2.7.0-beta.2)

### 这次更新了什么

- **记忆注入机制规范化，不再干预宿主对话历史**：为保持与 AstrBot 上游架构的长期兼容、适配其后续版本的技术演进，本版本对插件与 LLM 请求链路的交互方式进行了规范化收敛，收缩并移除了部分超出插件职责边界的非标准操作。记忆注入全面统一为 AstrBot 官方扩展点的追加式（add-only）写入：记忆以 `mark_as_temp` 临时片段追加到当前用户消息末尾，由 AstrBot 在保存对话历史时自动过滤，注入不写入对话历史、不影响前缀缓存。
- **移除历史格式归一化**：插件不再改写历史消息的 content 结构，消息格式保持 AstrBot 写入时的原样。
- **废弃非追加式注入方式**：`user_message_before`、`user_message_after`、`fake_tool_call`、`fake_tool_call_deepseek_v4`（含 `system_prompt`）已废弃，配置后自动回退至 `extra_user_content`，无需手动调整配置。
- **保留遗留残留清理**：`auto_remove_injected` 仅用于从请求上下文移除旧版本注入方式残留在历史中的片段，不触及用户自身的任何历史消息。
- **内部重构 (#268–#272)**：删除无效代码路径、页面 API 与整合管理器收敛至 MemoryEngine 公共 API、JSON 工具去重与 schema DDL 归一、召回作用域回退语义对齐、Mixin 契约改为类注解声明。

### 升级说明

- 旧配置中的注入方式无需手动调整，运行时自动回退；如需清理历史中以文本形式残留的旧注入片段，可执行 `/lmem cleanup`（伪造工具调用消息对会在每次请求时自动从上下文中移除）。

### 完整 PR 与作者

| PR | 改动 | 作者 |
| --- | --- | --- |
| [#268](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/268) | 移除死代码路径与未使用导出 | [@lxfight](https://github.com/lxfight) |
| [#269](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/269) | 页面 API 与整合管理器路由至 MemoryEngine 公共 API | [@lxfight](https://github.com/lxfight) |
| [#270](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/270) | JSON 工具去重，schema DDL 归一到单一来源 | [@lxfight](https://github.com/lxfight) |
| [#271](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/271) | 召回作用域回退与写入侧语义对齐 | [@lxfight](https://github.com/lxfight) |
| [#272](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/272) | Mixin 宿主契约声明为类注解 | [@lxfight](https://github.com/lxfight) |
| [#273](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/273) | 记忆注入规范化为 add-only，不再干预宿主对话历史 | [@lxfight](https://github.com/lxfight) |

### 安装与升级

1. 停止 AstrBot，保留现有插件代码、插件数据目录及配置的备份。
2. 从上方测试版发布页下载 **Source code (zip)**，解压后将代码放到 `data/plugins/astrbot_plugin_livingmemory`；保留原有插件数据及配置，不要将其删除或覆盖为空目录。
3. 启动或重载 AstrBot，在插件列表确认版本为 `2.7.0-beta.2`，再进入 `插件 → LivingMemory → Pages → dashboard`。
4. 旧配置中的注入方式会自动回退，无需手动调整；如需清理历史中以文本形式残留的旧注入片段，执行 `/lmem cleanup`。

通过 Git 管理插件代码时，可在干净的插件工作目录中切换到指定标签，再启动 AstrBot：

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.2
```

测试版在 GitHub 标记为 **Pre-release**，请使用指定标签；`releases/latest` 仍指向稳定版本。

## 2.7.0-beta.1 · 2026-09-07

本次为预发布测试版，覆盖正式版 **2.6.1** 之后的全部合并改动。稳定使用可继续选择 2.6.1；希望体验新界面和召回修复时，安装下面的测试版标签。

[下载 2.7.0-beta.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.1) · [稳定版 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1) · [完整差异](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/compare/2.6.1...2.7.0-beta.1)

### 这次更新了什么

- **四种主题**：Editorial、Studio、Paper、Terminal，各自支持浅色、深色和自动模式，选择保存在当前浏览器。
- **稳定阅读图谱**：放大、悬停或选择节点时暂停环境漂浮，标签优先级与碰撞区域保持稳定，减少密集文字闪烁和静止时的渲染开销。
- **清晰的管理流程**：先筛选、再选择、再操作；导入导出独立分组，导出范围明确，导入提供预检与确认。编辑关闭前检查未保存内容，保存期间防止重复提交和切换对象。
- **更可靠的召回与恢复**：新写入长记忆保留完整正文，作用域过滤先于结果限制，检索路由故障隔离，过期原子在缓存命中后仍会被过滤，图谱重建回滚保留待处理增量。
- **减少重复工作**：同次双路查询共享 Embedding，图路来源文档批量读取，FAISS 搜索在线程中执行；页面合并重复请求与滚动渲染，失败后给出重试入口。

历史已经被截断的正文无法由这次升级自动还原。图谱路使用记忆原子时只召回仍在生命周期内的事实。

### 完整 PR 与作者

以 Git 标签 `2.6.1` 为起点核对主分支提交，并按 GitHub PR 作者署名。

| PR | 改动 | 作者 |
| --- | --- | --- |
| [#259](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/259) | 召回、长正文存储与索引恢复修复；管理页面加载和移动端优化 | [@lxfight](https://github.com/lxfight) |
| [#260](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/260) | 四种可切换的管理界面主题 | [@lxfight](https://github.com/lxfight) |
| [#261](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/261) | 修复图谱放大后的标签闪烁 | [@lxfight](https://github.com/lxfight) |
| [#262](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/262) | 筛选、批量操作、导入和编辑的流程及并发保护 | [@lxfight](https://github.com/lxfight) |
| [#263](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/263) | 测试版发布、完整 PR 署名、README 视觉更新与 VitePress 文档同步 | [@lxfight](https://github.com/lxfight) |

### 安装与升级

1. 停止 AstrBot，保留现有插件代码、插件数据目录及配置的备份。主题设置保存在浏览器，与数据备份分开。
2. 从上方测试版发布页下载 **Source code (zip)**，解压后将代码放到 `data/plugins/astrbot_plugin_livingmemory`；避免在插件目录内再嵌套一层带版本号的目录。保留原有插件数据及配置，不要将其删除或覆盖为空目录。
3. 启动或重载 AstrBot，等待依赖安装和初始化完成。在插件列表确认版本为 `2.7.0-beta.1`，再进入 `插件 → LivingMemory → Pages → dashboard`。
4. 刷新管理页面；若仍看到旧界面，强制刷新浏览器页面。按[快速开始](/guide/getting-started#验证是否工作)检查状态、写入和召回。

通过 Git 管理插件代码时，可在干净的插件工作目录中切换到指定标签，再启动 AstrBot：

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.1
```

测试版在 GitHub 标记为 **Pre-release**，请使用指定标签；`releases/latest` 仍指向稳定版本。Pages 管理界面需要 AstrBot 4.24.2 或更高版本。

### 测试重点与反馈

建议检查四种主题的明暗切换与刷新保持、密集图谱放大阅读、组合筛选、导入预检取消、未保存编辑保护，以及真实 Provider 下跨会话召回与长正文写入。可以在[问题反馈](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/new/choose)中附上插件/AstrBot 版本、主题、浏览器、复现步骤和脱敏日志。

已有 Python 785 项与前端 55 项测试通过，并完成 VitePress 构建及模拟 AstrBot API 的浏览器回归。这些检查不替代真实模型服务与个人数据下的验证。

### 回到稳定版

停止 AstrBot，将插件代码换回 `2.6.1` 标签，再启动。若需要让数据也回到升级前的状态，请在停止 AstrBot 时恢复对应的数据和配置备份；升级之后新产生的记忆不在旧备份中。不要在运行期间直接覆盖数据库文件。

## 历史版本

完整历史见 [CHANGELOG](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/blob/master/CHANGELOG.md) 与 [GitHub Releases](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases)。
