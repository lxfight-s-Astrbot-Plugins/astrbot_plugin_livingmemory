# Changelog

所有重要的更改都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [2.6.0-beta.3] - 2026-08-14

### 性能
- **图谱渲染优化**: 选中节点聚焦只平移视口，不再重算整轮力布局；节点/边结构未变时复用布局缓存；空间网格改用数值键并跨迭代复用桶数组；中等及以上图（>60 节点）改为渐进式布局（分片迭代、边算边显示，不阻塞主线程）；空闲环境漂浮动画按图规模降帧渲染；边命中测试加 AABB 预过滤；背景点阵/网格缓存到离屏画布（平移只改 blit 偏移）；标签碰撞检测由 O(L²) 改为空间网格 O(L)；标签宽度 measureText 缓存；空闲时社区椭圆几何缓存（布局/视口变化自动失效）；缩小视图时边按 LOD 抽样绘制（高亮边始终完整）
- **力布局移入 Web Worker**: 布局迭代在主线程外运行（消息协议分片回传位置），超大图不再占用主线程 CPU；Worker 不可用时自动回退主线程内联布局。力布局核心抽取为 `graph-layout-core.js` 共享模块（主线程与 Worker 单一数据源）

### 重构
- **图谱前端模块化**: `graph-2d.js` 拆分——常量/工具（`graph-shared.js`）、Canvas 渲染器（`graph-renderer.js`）、交互（`graph-interaction.js`）、布局/动画/入口（`graph-2d.js`），各文件按需加载，便于维护
- **WebUI 布局重构**: 修复组件相互遮挡——记忆页表格由硬编码 `calc(100vh - Npx)` 改为弹性布局（筛选栏固定、表格自动填满剩余高度并内部滚动，分页不再被挤出视口）；页头高度自适应（消除 112px 固定高度与大标题的溢出冲突）；图谱页由 `overflow: hidden` 改为可滚动，短窗口下工具栏/图例不再被裁剪或重叠；画布移除 `min-height: 400px` 与容器尺寸错位；图谱统计列改为紧凑并防溢出
- **WebUI 死代码清理**: 移除约 45 个未使用的 CSS 类（`graph-tooltip`、旧评分明细 `result-scores`/`result-score-row-*`、旧类名 `session-id`/`backup-version`、`modal-*`、`skeleton`+shimmer 动画、`page-size-control`、`peek-*` 遗留类、`node-badge` 旧系列、`graph-node-*`、`card`/`card-title`、`flex-1`/`gap-*` 等工具类），以及从未被调用的 `renderBarChartItem`/`formatFileSize` 死方法；清理后全量校验无活动类遗漏

### 修复
- **渐进式布局并发守卫**: 快速连续加载两张图时，旧布局链路会被代数守卫丢弃，避免双倍步进或提前结束布局
- **渐进布局过期边**: tier>0 渐进过程中清空上一张图的边子集与社区束，布局完成后统一重建
- **移除 faiss-cpu 依赖固定**: 插件不再在 `requirements.txt` 中声明 `faiss-cpu`（由 AstrBot 核心提供），修复 2.6.0-beta.1 以来与 AstrBot 固定版本（如 `faiss-cpu==1.14.2`）的依赖冲突导致的升级失败 (#247)

### 测试
- 新增图谱布局前端测试（同步/渐进式/Worker/回退/布局缓存/聚焦不重算/快速双加载/标签宽度缓存/社区缓存，共 9 项）；完整 Python 测试集 743 项通过，前端测试 15 项通过

## [2.6.0-beta.2] - 2026-08-13

### 新增
- **记忆库定期整合**: 新增 `memory_consolidation` 配置节，从源头控制记忆库规模——把零散的低价值记忆聚合、整理、总结为更精炼的一条，替代注入时的硬截断方案。支持按同一会话（`granularity=session`）或跨会话语义聚类（`granularity=semantic`）聚合，整合后旧记忆可归档（`keep_original=archive`）或删除（`keep_original=delete`），触发方式可选每日定时（`trigger=daily`）或每次反思时顺带执行（`trigger=reflection`，带 6 小时冷却）。语义聚类直接复用索引内已存向量做批量 Faiss 搜索（分块、无 Embedding API 重复调用），可扩展到上万条记忆；整合结果由 LLM 无损合并生成，写入新记忆并保留 `consolidated_from` 溯源
- **WebUI 整合展示**: 系统概览页新增「记忆整合」面板（配置状态 + 已整合/已归档统计 + 立即整合按钮）；记忆列表为整合产生的记忆显示「整合 N」徽标，详情面板展示来源记忆列表

### 修复
- **会话重置信号兼容**: 适配 AstrBot 将 `/reset`、`/new` 的会话清理信号从 `_clean_ltm_session` 改名为 `_clean_group_context_session`，两个信号均识别 (#244)

### 移除
- **记忆注入预算控制**: 移除 2.6.0-beta.1 引入的注入预算控制（`recall_engine.injection_budget_chars` / `injection_min_chars_per_memory` / `injection_max_chars_per_memory` 及配套截断逻辑）。硬截断会丢失语义连贯性与结尾信息，改为在记忆库层面定期整理聚合

### 测试
- 新增记忆整合分组 / 语义聚类 / 合并解析 / 归档删除 / 向量批量相似对 / Page API 整合状态等 20 项测试；完整 Python 测试集 743 项通过，前端测试 7 项通过

## [2.6.0-beta.1] - 2026-08-13

### 修复
- **图存储外键级联**: 运行时连接此前未开启 `PRAGMA foreign_keys`，导致 `ON DELETE CASCADE` 在运行期失效；现在每个连接都显式开启，删除节点时关联边会被正确级联清理
- **图边写入竞态**: `_add_edge` 改用 `INSERT ... ON CONFLICT(edge_key)` 原子写入，避免并发下撞唯一约束导致回滚；语义合并的权重累积改为按 `edge.weight` 缩放而非固定 +0.15
- **图 FTS 检索兜底**: `search_entries_by_bm25` 增加异常兜底，非法 FTS 查询不再中断整条图路由检索
- **删除崩溃恢复补全**: 删除操作的写日志重放现在会先补删 documents 表与向量/BM25 索引，避免崩溃后记忆仍可检索、图/原子却已删除的不一致
- **访问时间原子化**: 召回路径的访问时间/访问计数更新合并为单条原子 SQL（`json_set`），消除并发下的读改写丢失更新，并批量提交降低写放大
- **会话/原子并发安全**: `create_session` 幂等化（`ON CONFLICT DO NOTHING`）；`trim_session_messages` 的读改写整体纳入写锁；`reinforce` 用 `BEGIN IMMEDIATE` 抢占写锁避免计数丢失
- **初始化可重试**: 完整初始化失败不再永久禁用插件——清理半初始化资源后转交后台重试（带上限），瞬态错误（如数据库锁、索引重建失败）可在进程内恢复
- **迁移与启动修复**: 全新数据库（空版本表）不再误判为 v1 跑完整迁移链；v7→v8 只回填缺失的 `access_count` 字段；重试耗尽后的写操作日志正确进入终态 `failed`

### 性能
- 召回热路径批量更新访问时间（单次 commit 替代逐条 commit）
- 每日衰减的逐行 JSON 计算卸载到线程池
- `get_statistics`/`cleanup_old_memories` 由 O(N²) OFFSET 分页改为主键 keyset 流式分页
- `get_session_memories` 改为单条 SQL 过滤+排序+分页
- 旧数据会话迁移、索引重建、图入口分组遍历均复用连接并批量写入
- 一致性检查的 BM25 缺失计数改为 SQL 层计算，避免加载全量 ID 集合

### 代码清理
- 移除废弃的 `cleanup_injected_memories` 与不可达的 `_try_restore_from_backup` 死代码

### 测试
- 新增针对外键级联、图边幂等、会话幂等、访问计数并发、原子强化并发、初始化重试、会话记忆排序、语义合并权重等 11 项回归测试；完整 Python 测试集 751 项通过

## [2.5.7] - 2026-08-04

### 修复
- **提示词页国际化补全**: WebUI 提示词页此前 i18n 不完整——名称始终中英并排、描述与使用说明仅有中文、切换语言后已渲染内容不刷新。现后端为提示词与分类补充英文描述/说明字段，前端按当前语言选择中/英文案（中文界面保留英文副标题），并在切换语言时重渲染提示词页与已打开编辑器的标题

### 测试
- 新增提示词注册表双语字段回归断言与提示词页中/英/回退渲染的前端行为测试；完整 Python 测试集 712 项通过

## [2.5.6] - 2026-08-04

### 新增
- **WebUI 批量编辑**: 记忆列表勾选记忆后可通过「批量编辑」按钮统一修改重要性 / 状态 / 类型，补齐批量更新 API 的前端入口（含中/英/俄三语）

### 修复
- **总结质量恢复**: 移除内置提示词中的 `canonical_summary` 双摘要要求，模型一次只生成一份人格化 `summary`，消除竞争输出导致的质量下降；检索内容改由代码从 `summary` + `key_facts` 组装，修复 2.4.0 以来检索语料与 Agent 主动召回内容变薄的问题；自定义提示词输出的 `canonical_summary` 仍被兼容保留（供图抽取等消费方使用）

### 升级说明
- 本次修复仅对更新后新产生的记忆生效；历史记忆已存储的检索内容保持不变，元数据结构（v2）完全兼容

## [2.5.5] - 2026-08-03

### 修复
- **FAISS 核心依赖兼容性**: 将 `faiss-cpu` 最低版本从 `1.12.0` 提升到 `1.14.3`，与 AstrBot 4.27.1 的项目元数据和锁定版本保持一致；最低版本已自然排除存在已知绑定与指令集问题的 1.14.2

### 升级说明
- 如果升级后仍提示核心依赖与 `faiss-cpu 1.14.2` 冲突，说明 AstrBot Desktop 的实际依赖锁或安装缓存仍是旧版本；请更新或修复 Desktop 环境，而不是让插件覆盖 AstrBot 的核心依赖

### 测试
- 更新 FAISS 依赖基线回归断言，并验证 AstrBot 与插件约束在 Windows / Python 3.12 目标上共同解析为 `faiss-cpu 1.14.3`

## [2.5.4] - 2026-08-02

### 修复
- **私聊 Bot 身份归属**: 助手消息统一使用 Bot 昵称与平台 `self_id`，不再继承触发消息的用户身份或用户别名；群聊、私聊和缺失 `self_id` 场景使用一致的安全回退 (#232)
- **Dashboard 筛选一致性**: 记忆筛选只接受最新请求的响应，避免较慢的旧请求覆盖当前查询并导致列表与输入条件不一致
- **Dashboard 大分页滚动**: 虚拟列表按当前数据动态计算可见窗口，并使用表格占位行保持完整滚动高度；从 20 条切换至 50 或 100 条后仍可访问全部记忆
- **Dashboard 键盘导航**: 移除无入口的旧编辑弹窗，关闭状态的详情侧栏退出键盘焦点顺序与无障碍树

### 文档
- 仓库默认 README 改为中文首页，英文说明迁移至 `README_en.md`，并保留旧 `README_zh.md` 链接兼容

### 测试
- GitHub Actions 新增 Node 前端行为测试，覆盖乱序筛选响应和虚拟滚动数据更新；完整 Python 测试集 711 项通过

## [2.5.3] - 2026-08-01

### 修复
- **FAISS 绑定不匹配诊断**: 识别 `SuperKMeans` 等 Python 封装与二进制扩展不一致错误，停止无效的 generic 指令集重试，并明确提示这不是 Embedding Provider 配置问题 (#198)
- **规避已知异常依赖**: `faiss-cpu` 基线与 AstrBot 对齐到 1.12.0，并排除已知异常的 1.14.2；真实的 CPU 指令集或动态库加载失败仍保留 generic 回退

### 升级说明
- 已安装且损坏的 AstrBot Desktop 内置环境不会被插件自动修改；请升级或修复 Desktop 环境，并在同一 Python 环境中干净安装兼容的 FAISS 版本（建议 1.14.3 或更高版本）

### 测试
- 新增 FAISS 绑定不匹配、依赖版本约束和 generic 回退回归测试；完整测试集 707 项通过

## [2.5.2] - 2026-08-01

### 修复
- **大规模索引维护不再阻塞插件启动**: 启动阶段只调度后台一致性检查与修复，并通过 `/lmem status` 暴露检查、重建、部分完成、失败和取消状态
- **文档索引安全重建**: BM25 使用影子表原子切换，FAISS 使用影子索引和断点续跑；Embedding Provider 即使维度不变但模型发生变化，也会触发完整向量代际重建
- **图谱索引安全重建**: active 记忆按主键流式分批构建到影子图谱，完成后事务切换；失败保留线上代际，重建期间的新增、更新和删除会在切换前回放
- **归档记忆索引一致性**: 一致性检查和全部重建路径只处理 active 记忆，避免归档数据在重启后被重新加入 BM25、向量或图谱索引
- **并发写入收尾补偿**: 后台维护完成后再次检查一致性，并对维护期间进入的新文档执行一次有上限的补偿修复

### 测试
- 新增 Provider 指纹、全量向量断点续跑、BM25/图谱失败回滚、并发图谱变更回放、active 流式分批和后台维护状态回归测试；完整测试集 705 项通过

## [2.5.1] - 2026-07-31

### 修复
- **稳定人物身份**: 知识图谱使用平台与发送者 ID 组合作为人物节点标识，昵称变化时复用同一节点并保留别名历史；原子图谱不再把已识别参与者重复生成为主题节点 (#224)
- **人格化记忆展示**: Dashboard 列表与详情优先展示第一人称 `persona_summary`，事实型 `canonical_summary` 继续仅用于检索；私聊与群聊提示词进一步区分主观回忆和客观检索摘要 (#223)
- **图谱工具栏响应式布局**: 中等桌面宽度下将图例和工具栏分行，窄桌面下输入框与操作按钮自适应排列，避免 1080p / 125% 缩放和 1280×720 视口发生遮挡 (#222)
- **归档恢复兼容性**: 当前 AstrBot embedding provider 未提供可选重试接口时，回退到标准接口重建归档记忆向量

### 测试
- 新增最新 issue 回归测试，并在 Pull Request 与 `master` 推送时通过 GitHub Actions 运行完整测试集

### 实现边界
- 稳定人物身份只影响升级后新生成或重新构建的图谱数据；已有重复人物节点不会自动合并
- 旧记忆缺少 `persona_summary` 时继续回退显示事实摘要或原始内容

## [2.5.0] - 2026-07-29

### 新增
- **召回策略**: 支持最低向量相似度、近期记忆保留槽位和事件型记忆过滤 (#64, #99, #139)
- **确定性时间标签**: 从原始消息时间写入来源日期范围，不依赖大模型推断 (#175)
- **可恢复自动归档**: 旧低重要性记忆可移出检索索引但保留原始文档，并在恢复时重建全部派生索引 (#155)
- **重要记忆保护**: 达到可配置重要性阈值的记忆不再参与每日衰减 (#139)
- **记忆白名单**: 统一限制自动捕获、总结、召回、手动总结和 Agent 记忆工具，可按用户、群组或完整会话授权 (#61)
- **可配置记忆作用域**: 支持兼容旧配置、按会话、按用户和全局四种模式，并允许指定会话强制隔离 (#92, #187)
- **用户身份别名**: 在写入会话历史和生成总结前，将平台用户 ID 或用户名映射为统一显示名称 (#112)
- **重要记忆原文保留**: 达到可配置重要性阈值时，将结构化原始消息独立保存于 SQLite，不写入检索与图索引；Dashboard 可核验并重新总结，Agent 可按需回溯原文 (#129, #167)
- **指定条数手动总结**: `/lmem summarize [message_count]` 可忽略既有总结进度，重新总结当前会话最近 N 条消息 (#144)
- **WebUI 记忆迁移**: 支持原生 JSON 完整往返、CSV 和常见外部 JSON 字段；导入前预检并按内容与作用域去重，仅含多轮原文时可调用 LLM 生成摘要 (#23, #129)

### 实现边界
- 记忆作用域和身份别名仅影响升级后新写入的数据；不会自动迁移旧记忆、重新总结原始对话或重新生成向量
- `user` 作用域按平台和规范化身份组合，同一平台内跨群聊与私聊共享；跨平台共享应使用 `global` 作用域并结合白名单
- 原文保留增加 SQLite 磁盘占用；重新总结会产生一次 LLM 调用，并重新生成该记忆的 Embedding 与派生索引
- 导入不会复用外部向量或图索引；每条成功导入的记忆都会重新生成 Embedding，并按当前启用配置与数据条件生成图、原子等派生索引；仅含原始对话的导入项还会各产生一次 LLM 调用

## [2.4.1] - 2026-07-29

### 变更
- **超大图谱分级渲染**: 高密度概览以社区连接束和内部骨架替代逐帧绘制全部关系，选中节点或记忆时展开精确邻接边；大图稳定后停止动画循环，并使用空间索引加速命中测试
- **图向量记忆级压缩**: 图 SQLite 继续保存全部节点、关系和关键词条目，独立图向量索引改为每条来源记忆一个聚合语义向量；索引规模由 graph entry 数量收敛到来源记忆数量 (#197)
- **整库图索引批量重建**: `/lmem rebuild-graph` 先统一清空图向量，再一次性嵌入并写入全部记忆，完整重建固定为一次清空保存和一次批量保存

### 修复
- **旧版双通道摘要注入去重**: 对可精确识别的 v2 `persona_summary | key_facts` 旧记录移除正文中的事实后缀，避免与 `Key facts` 元数据重复注入；普通旧记录保持原文 (#202)

### 实现边界
- 日常新增或删除一条来源记忆仍会保存一次完整图 FAISS 索引，但该索引现在只包含每条来源记忆一个向量，不再按每个 graph entry 膨胀
- 旧记录的检索正文与既有向量不会在后台自动重新生成，避免升级时产生不可控的 LLM 和 Embedding 成本；新记忆继续使用独立 factual `canonical_summary` 检索

## [2.4.0] - 2026-07-28

### 新增
- **全量知识图谱**: Dashboard 概览支持按当前 persona/session 作用域加载全部图节点、关系和轻量记忆预览，不再受检索子图的 80 节点 / 120 关系上限约束
- **大规模图谱分区**: 基于拓扑社区进行确定性布局，独立收纳未连接节点；千节点场景使用空间网格斥力、社区锚定、曲线关系和粒子预算保持可读性
- **召回最低重要性过滤**: 新增可选的最低重要性阈值，在统一检索出口过滤低重要性记忆 (#116)
- **扩展上下文时间限制**: 召回查询仅拼接配置时间范围内的历史消息，避免跨话题时间间隔造成干扰 (#182)
- **Dashboard 批量删除记忆**: 支持在当前记忆页多选并确认删除；一次批量请求内合并文档和图向量索引保存 (#192)
- 支持在 WebUI 中联合编辑记忆内容、主题与关键事实，并重建对应的检索、原子和图数据。
- **WebUI 提示词管理页面**: 在 Dashboard 中集中管理插件所有可自定义的 prompt 模板，支持按分类浏览、编辑、保存、恢复默认，含 JSON 格式警告标识和 i18n 多语言支持
- **PromptManager**: 提示词注册表 + 文件持久化引擎，自定义内容保存至 `data/prompts/`，内置默认不受影响；原有硬编码 prompt（system_prompt_base、system_prompt_with_persona、injection_header/footer）提取为独立模板文件
- **提示词 API**: Page API 新增 `prompts`、`prompts/detail`、`prompts/update`、`prompts/reset`、`prompts/default` 五个路由

### 变更
- **Dashboard 浅色视觉重构**: 使用雾白、冷灰绿与克制的多色信号重建五个管理页面，补充响应式移动导航、主题适配、动态反馈与减弱动效支持
- **统一 Lucide 图标**: 本地 vendoring Lucide 资源并覆盖静态、提示词和详情面板的动态图标；移除界面表情符号和手写 SVG 图标
- `MemoryProcessor._load_prompts()` 和 `_build_system_prompt_with_persona()` 改为通过 PromptManager 加载模板，保留后备降级路径
- `format_memories_for_injection()` 的记忆注入头部/尾部文本改为从 PromptManager 读取

### 修复
- **双通道摘要与注入**: 新生成的记忆独立请求事实型 `canonical_summary` 用于检索，注入优先使用 `persona_summary`；旧记录保持兼容回退，本次不迁移既有向量 (#202)
- **FAISS 加载兼容性**: 移除插件模块加载阶段的 FAISS 导入，并在优化扩展不可用时尝试 generic 指令集模式；不替代损坏或不兼容安装的环境修复 (#153, #198)
- **图记忆磁盘写放大**: 以单条来源记忆为边界批量新增和删除图向量，避免每条 graph entry 分别保存索引；非空批次仍会写入完整 FAISS 快照 (#197)

### 测试
- 新增 Dashboard 资源契约、全量图谱快照与 Page API 路由测试；完整测试集 621 项通过

## [2.3.6] - 2026-06-28

### 修复
- **WebUI 重要性 1.0 保存成 10.0**: 详情编辑提交显示值标记，后端兼容 0-1/0-10 两种重要性输入语义 (#189)
- **图记忆长文本召回 SQLite 表达式树过深**: 图节点 token 查询去重并分批执行，避免长中文输入生成过多 OR 条件 (#176)
- **extra_user_content 注入上下文残留**: 仅清理 LivingMemory 自己临时注入的记忆片段，并归一化纯文本历史 content parts，避免污染长期上下文 (#185)

## [2.3.5] - 2026-06-09

### 重构
- **EventHandler 模块化**: 将事件处理器拆分为 `GroupCapture`、`MemoryRecall`、`MemoryReflection` 三个子模块，提高代码可维护性 (#172)
- **Page API 模块化**: 将 `page_api.py` 的处理逻辑提取到 `page_api_modules/` 目录下独立模块，职责分离更清晰 (#173)
- **WebUI Dashboard 页面模块化**: 将前端页面逻辑拆分到 `modules/` 目录（memory-page.js、recall-page.js、graph-page.js、system-page.js）

### 测试
- 测试覆盖率从 73% 提升至 76% (#171)

### 修复
- **群聊全量捕获误唤醒 AstrBot**: 修复 `PassiveGroupCaptureFilter` 未正确屏蔽群消息导致触发 LLM 响应的问题 (#170)
- **inspect-stack 崩溃**: 传递 `plugin_name` 给 `StarTools.get_data_dir()` 避免堆栈检查失败 (#169)
- **WebUI Page API 过滤器规范化**: 统一前端 API 请求的参数处理逻辑

## [2.3.4] - 2026-06-02

### 修复
- **#166 排查确认**: TextPart 序列化崩溃非 LivingMemory 导致，而是其他插件（如 llmperception）注入 TextPart 引起。`mark_as_temp()` 标记的 TextPart 在 `dump_messages_with_checkpoints()` 中被过滤不落地，不会进入上下文压缩器。保持原有 `extra_user_content_parts` + `TextPart.mark_as_temp()` 注入方式不变。
- **系统概览页重要性分布图始终为空**: `get_statistics()` 遍历了全部文档却未对重要性分桶，现在在批次处理循环中按 0-10 分 10 档统计
- **系统概览页原子计数始终为 0**: `AtomStore` 缺少 `count_atoms()` 方法导致 `AttributeError` 被静默吞掉，现已新增该方法
- **系统概览页原子类型分布图始终为空**: 新增 `AtomStore.count_by_type()` 方法（SQL GROUP BY atom_type），修复 per-type 统计缺失
- **系统概览页 atom_breakdown 已接入**: `page_api.get_stats` 现在正确调用 `atom_store.count_by_type()` 填充类型分布数据
- **WebUI 记忆列表创建时间列始终显示 "--"**: 当 `metadata.create_time` 缺失时前端会忽略 SQL 层的 `created_at` 列，现增加 fallback
- **WebUI 记忆编辑静默失败**: 状态/类型/重要性的编辑操作未检查 API 响应是否成功，失败时仍弹成功 toast，现已用 `unwrapApiData()` 包装错误检测
- **知识图谱页 Graph2D 未初始化时崩溃**: `renderPayload` 中 `window.Graph2D.selectNode/selectMemory` 缺少 `state.isGraphReady` 守卫
- **召回测试结果点击无效**: 当召回的記憶不在当前记忆列表分页中时，点击无任何反馈，现已添加 API 回退直接拉取記憶详情
- **知识图谱节点详情面板类型字段丢失**: 图谱記憶对象使用 `memory_type` 字段名，前端错误使用了 `memory.type`
- **备份管理器版本号不匹配**: `PLUGIN_VERSION` 为 2.3.1 但 `metadata.yaml` 为 2.3.3，导致每次启动错误触发版本变更备份
- **`datetime.utcnow()` 弃用警告**: 迁移 `db_migration.py` 中 3 处调用为 `datetime.now(timezone.utc)`
- **记忆详情 fallback SQL 查询缺少列**: `_get_memory_record` 回退查询未选取 `doc_id`、`created_at`、`updated_at`
- **配置项 `enable_full_group_capture` 缺失**: `_conf_schema.json` 中未暴露该字段，用户无法在 WebUI 配置

### 变更
- `page_api.update_memory` 统一使用 `self._ok()` 返回格式
- 更新测试文件以匹配注入行为变更

## [2.3.3] - 2026-06-02

### 修复
- **WebUI 删除功能无效**: 修复因 AstrBot Dashboard iframe sandbox 缺少 `allow-modals` 导致 `window.confirm()` 被浏览器静默阻止、删除操作无法执行的问题
  - 用自定义 DOM 确认对话框（渲染在 peek 面板内）替代浏览器原生 `window.confirm()`
  - 支持确定/取消/ESC/遮罩点击关闭，取消时自动恢复记忆详情视图

## [2.3.2] - 2026-06-02

### 新增
- **知识图谱力导向布局优化**: 重构图谱可视化布局算法，实现更自然的节点分布
  - 优化斥力参数（6000→1800），节点分布更平滑
  - 增加边距离（80→120）和弹簧强度，改善节点间距
  - 实现基于距离的斥力衰减曲线，替代硬性截断
  - 自适应弹簧强度，长边使用更弱的拉力
  - 质量缩放的中心引力，重要节点更居中
  - 增加迭代次数，布局收敛更稳定
- **Peek 面板迷你图谱力导向布局**: 预览面板中的小型图谱也采用力导向算法，与主视图保持视觉一致性

### 优化
- 移除固定的中心节点锁定，所有节点自由受力运动
- 简化布局类命名：`CenteredForceLayout` → `ForceDirectedLayout`
- 清理废弃的 BFS 环形布局参数（`FORCE_LINK_DEPTH_GAP`、`FORCE_CENTER_PULL`、`FORCE_BRANCH_SPREAD`）

## [2.3.1] - 2026-05-30

### 新增
- **记忆注入方式 `extra_user_content`**: 将记忆追加到用户消息末尾（`mark_as_temp` 不污染对话历史），不影响前缀缓存，推荐作为默认方式
- **system_prompt 注入方式废弃**: 配置为 `system_prompt` 时自动回退至 `extra_user_content`（`InjectionAdapter` 废弃模式降级），保留配置项但标注 ⚠️已废弃
- **Agent 主动记忆写入工具** (`memorize_long_term_memory`): Agent 可主动调用写入长期记忆，通过 `agent_tools.enable_memorize_tool` 配置开关控制（默认关闭）
- **Agent 工具配置组** (`agent_tools`): 新增 `enable_recall_tool` 和 `enable_memorize_tool` 两个独立开关
- **两步确认删除**: dashboard 删除选中记忆改为两步确认（点击→按钮变为「确认删除 X 条?」→再次点击执行），替代被 AstrBot 插件页面拦截的 `window.confirm`

### 修复
- 修复 `MemoryProcessor.__init__` 参数名 `llm_provider_id` → `llm_provider`，兼容传入 provider 实例和 ID 字符串两种调用方式
- 修复 `test_tokenize_removes_common_stopwords` 在 jieba 未安装时的不稳定行为

### 优化
- **7 项异步性能优化**:
  - 记忆注入清理正则提到模块级常量（避免每次调用 `re.compile`）
  - 去重缓存改为惰性过期 + 超限逐条淘汰（消除 `sorted()` 排序开销）
  - 版本备份延迟到异步初始化阶段（通过 `asyncio.to_thread` 避免 `__init__` 中同步 I/O 阻塞）
  - jieba 分词通过 `tokenize_async()` 卸载到线程池
  - `hybrid_retriever` MMR 和 weighting 卸载到线程池
  - `memory_engine` 批处理 `json.loads` 通过 `_normalize_batch_metadata` + 线程池批量规范化
  - `_remove_fake_tool_call_from_context` 两轮扫描合并为单轮
- `InjectionAdapter` 新增 `_DEPRECATED_MODES` 映射，废弃模式统一降级

### 文档
- 更新 CHANGELOG v2.3.1 条目
- 更新所有文档版本号为 v2.3.1（API、ARCHITECTURE、DEVELOPMENT 中/英/俄）

### 测试
- 新增 17 个测试: hybrid_retriever 元数据多样性 + 删除回滚 (6)、memory_engine 更新回滚 + 分批 + 批量删除 (5)、event_handler 上下文扩展 + 重试逻辑 (4)、text_processor add_custom_words (3)
- 测试总数: 298 → 332，覆盖率: 69% → 70%
- 注入方式相关测试: 新增 `extra_user_content` 和 `system_prompt` 自动回退测试
- 修复 24 个因参数签名不匹配导致的测试失败

## [2.3.0] - 2026-05-29

### 新增
- **记忆原子化系统**: 将 LLM 输出的 `key_facts` 提升为独立检索单元 (`MemoryAtom`)，每条原子拥有独立的存活时间 (TTL) 和衰减曲线
  - 五种原子类型: `EPISODIC`(事件型, 7天)、`PLANNED`(计划型, 到期骤降)、`FACTUAL`(事实型, 180天)、`RELATIONAL`(关系型, 90天)、`PREFERENCE`(偏好型, 60天)
  - 三种衰减函数: `EXPONENTIAL`(指数)、`LINEAR`(线性)、`STEP`(阶梯)
  - TTL 动态修正: `ttl = base_ttl × (0.5+importance) × (1.0+0.1×reinforcement_count)`
  - 规则基分类器，零新增 LLM 调用
- **图谱时间感知增强**: 边置信度跨记忆动态更新 (EMA)、跨记忆语义边合并 (`semantic_edge_key`)、检索评分增加时间衰减乘子
- **原子生命周期管理器**: 后台周期维护 (过期/遗忘/强化检测)，基于 Jaccard + CJK bigram 的跨记忆原子强化
- **版本更新自动备份**: 插件启动时检测版本变更，自动将所有数据文件备份到 `backups/v{旧版本}/`，记录 `backup_info.json` 便于数据恢复
- **备份列表 API**: `GET /page/backups` 端点，支持前端查看完整备份历史

### 修复
- 修复图路由权重归一化未生效时双路融合数值不稳定的问题
- 修复 `page_api` 内容更新异常时新旧记忆并存的数据泄漏
- 修复 `memory_engine` 中 fire-and-forget 后台任务未跟踪，`close()` 时可能静默取消
- 修复 `event_handler` 记忆存储后元数据更新失败导致同一段消息被重复总结
- 修复 `command_handler` 中硬编码中文 `"无"` 未走 i18n

### 优化
- 图谱边存储增加 `semantic_edge_key`，跨记忆合并相同语义关系，避免重复边膨胀
- 边置信度采用 EMA 动态更新 (`new = old×0.7 + new×0.3`)，weight 累积证据计数
- `graph_extractor` 支持原子级提取路径 (`_extract_from_atoms`)，原子置信度传播到图谱边
- `atom_store` FTS5 搜索自动回退 LIKE 查询，兼容低版本 SQLite 的 CJK 分词缺陷

### 文档
- 更新 README（中/英/俄）: 补充记忆原子化、版本备份、架构模块说明

### 测试
- 新增 53 个原子系统测试: TTL 计算 (9)、衰减函数 (6)、分类器 (9)、AtomStore (9)、AtomLifecycleManager (3)、AtomRetriever (4)、图谱原子提取 (4)、边合并 (2)、向后兼容 (3)、其他 (4)
- 新增 22 个备份管理器测试: 版本检测、通配符备份、OSError 容错、多版本排序、损坏 JSON 回退、metadata 版本一致性校验
- 新增 1 个实际 `metadata.yaml` 版本号与 `PLUGIN_VERSION` 常量的一致性断言

## [2.2.12] - 2026-05-12

### 新增
- 新增配置 UI 与后端命令响应的英/俄双语国际化支持，适配 AstrBot 原生插件页面与命令输出。

### 修复
- 修复 MemoryProcessor 持有过期 LLM provider 引用时出现 `Cannot send a request, as the client has been closed` 的问题。
- 修复 WebUI 生命周期与历史消息批量清理逻辑，避免重载和端口占用引发的异常。

## [2.2.11] - 2026-05-06

### 新增
- 新增 AstrBot 官方插件 Pages 管理界面支持：可在 AstrBot WebUI 的插件详情页直接进入 `dashboard` 页面，无需额外登录插件独立 WebUI。
- 新增官方插件 Page 原生后端接口适配层，支持记忆统计、记忆列表、批量删除、记忆编辑、召回测试、知识图谱概览与图谱检索。

### 兼容性
- 官方插件 Pages 入口依赖 AstrBot 插件 Page / Bridge 能力，要求 AstrBot 版本 `>= 4.24.2`。
- 保留旧版独立 WebUI 兼容入口；当宿主环境不支持官方插件 Pages 或仍需独立访问时，可继续使用 `/lmem webui` 提供的旧入口。

### 优化
- `/lmem webui` 命令输出改为优先引导用户进入 AstrBot 官方插件页，同时保留旧独立 WebUI 兼容说明。
- 官方插件页面前端改为复用 AstrBot 登录态，并适配 sandbox iframe 环境下的 Bridge 请求、主题读取与页面初始化流程。

### 修复
- 修复 AstrBot 4.23.2 中 `documents_fts` 同名表冲突导致总结记忆存储失败的问题。
- 将插件自有 FTS 表统一迁移为 `livingmemory_memories_fts` 与 `livingmemory_graph_entries_fts`，避免再次污染宿主数据库命名空间。
- 新增 v6 数据库迁移：复制旧 `memories_fts` / `graph_entries_fts` 数据到前缀表，删除插件废弃 `documents_fts(search_text)`，保留 AstrBot 同名表。
- 修复 `/lmem webui` 在旧版独立 WebUI 未启用时缺少兼容提示的问题，并恢复兼容入口访问地址文案。
- 修复 `top_k=0` 私聊场景下消息存储优先级错误，避免错误写入事件默认文本。
- 调整记忆注入格式，同时兼容英文提示模板与既有中文记忆条目标识。

### 测试
- 补充 issue #102 回归测试，覆盖宿主 `documents_fts` 存在时 BM25 写入、旧 FTS 表前缀迁移、废弃插件冲突表清理。
- 补充 `/lmem webui` 兼容提示、`top_k=0` 私聊存储、记忆注入格式兼容回归测试。

### 移除
- 移除废弃的 `sparse_retriever` 实现与配置入口，统一使用文档路 BM25/向量检索、图路检索和 RRF 融合链路。
- 移除废弃的 `reflection_engine.save_original_conversation` 配置项和向量查询预处理开关，减少无效配置面。

### 文档
- 更新 README 与架构文档中的检索层描述，避免继续引用旧的单路混合检索表述。

## [2.2.3] - 2026-02-21

### 修复
- 统一清理插件运行时日志与命令返回中的 emoji 字符，避免日志检索和终端显示噪音
- 优化初始化、命令执行、索引重建、历史清理等失败路径的用户提示：错误信息包含失败动作、错误详情与建议排查步骤
- 修正插件帮助信息与注册元数据中的仓库地址，统一为 `https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory`

### 测试
- 补充 `CommandHandler` 与 `PluginInitializer` 单元测试，覆盖未初始化组件提示、异常提示可操作性、索引重建失败提示、Provider 超时错误信息
- 补充 real-db 功能测试，覆盖命令输入校验、状态异常提示、WebUI 启用/禁用提示分支、cleanup 预演与执行路径
- 新增插件主生命周期集成测试，覆盖初始化状态消息、`_ensure_plugin_ready` 失败分支、命令处理器未就绪提示、WebUI 启停联动与 `terminate` 资源清理

## [2.2.2] - 2026-02-21

### 新增
- 新增 `/lmem summarize` 命令：允许管理员手动立即触发当前会话的记忆总结，无需等待自动触发阈值
- 新增向量检索 token 超限保护：查询文本超过 2000 字符时自动截断，写入内容超过 4000 字符时自动截断，避免 embedding API 报错

### 修复
- 修复群聊记忆中发送者昵称丢失的问题（#59）：助手消息写入时正确标记 `is_bot_message=True`，`format_for_llm` 同时检查 `metadata` 标记和 `role` 字段，确保 Bot 消息以 `[Bot: 昵称]` 格式呈现给 LLM

### 优化
- 总结时自动注入当前日期时间（#74）：在 system_prompt 和提示词模板中注入 `{current_date}`，LLM 可将对话中的相对时间（"今天"、"明天"、"下周"等）转换为具体日期后写入记忆，避免记忆内容随时间推移失去时间参考意义

## [2.2.1] - 2026-02-21

### 修复
- 修复 tool 循环产生的最终总结被错误存入记忆的问题：在 `handle_memory_reflection` 中检测 `tools_call_name` 和 `tools_call_extra_content`，有工具调用上下文时直接跳过，避免 tool loop 的内部总结污染记忆
- 修复 `/reset` 或 `/new` 后插件仍读取旧对话内容进行总结的问题：新增 `after_message_sent` 钩子监听 AstrBot 的 `_clean_ltm_session` 信号，触发时同步调用 `conversation_manager.clear_session()` 清空消息历史和总结计数器
- 修复私聊场景下用户消息写入后未执行消息数量上限控制的问题：`handle_memory_recall` 写入用户消息后补充调用 `_enforce_message_limit`；`handle_memory_reflection` 写入助手消息后同样执行上限控制

## [2.2.0] - 2026-02-21

### 新增
- 新增定期自动备份功能：每日衰减后自动备份记忆数据库，可配置保留天数（默认 7 天），超期备份自动清理
- 新增图片转述内容存入记忆：读取 AstrBot 已完成的图片转述（`extra_user_content_parts`），按消息组件原始顺序正确映射，无转述时降级为 `[图片]` 占位

### 优化
- 所有辅助方法改为 `async def`，消除同步文件 IO 阻塞：`stopwords_manager`、`decay_scheduler`、`text_processor` 均改用 `aiofiles`
- 消息内容提取（`_extract_message_content`）按组件原始顺序拼接，文字与图片相对位置正确保留，不再重复提取转述内容
- `DecayScheduler` 状态管理（`_load_state`、`_save_state`、`_get_last_decay_date`、`_set_last_decay_date`、`_calculate_missed_days`）全部改为异步，避免阻塞事件循环

## [2.1.9] - 2026-02-21

### 修复
- 修复 `memory_engine.delete_memory()` 重复删除 `documents` 表的问题：`hybrid_retriever.delete_memory()` 内部已按顺序删除 BM25 → 向量索引 → documents，上层再次删除会造成连接竞争
- 修复 `update_memory()` 内容更新时旧记忆删除失败静默返回 `True` 的问题：现在改为回滚（删除刚创建的新记忆）并返回 `False`，避免新旧记录并存
- 修复 `status` 命令缺少 `@permission_type(PermissionType.ADMIN)` 装饰器，任意用户均可查看系统状态
- 修复 `help` 命令中仓库链接错误（指向了旧地址）

### 优化
- 数据库迁移（`DBMigration.migrate()`）执行前自动调用 `create_backup()` 创建完整备份，备份失败仅警告不中断迁移，迁移结果中附带 `backup_path`

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
