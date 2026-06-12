# 功能说明

LivingMemory 的目标不是简单保存聊天记录，而是把对话转化为可以检索、可以衰减、可以被 Agent 主动使用的长期记忆。

<div class="feature-grid">
  <div>
    <h3>自动总结</h3>
    <p>达到触发轮次后，插件会让 LLM 将最近对话总结为结构化记忆，并写入长期记忆库。</p>
  </div>
  <div>
    <h3>双通道摘要</h3>
    <p><code>canonical_summary</code> 面向事实检索，<code>persona_summary</code> 面向提示词注入和人格表达。</p>
  </div>
  <div>
    <h3>主动记忆工具</h3>
    <p>Agent 可以主动调用 <code>recall_long_term_memory</code> 搜索旧记忆，或调用 <code>memorize_long_term_memory</code> 写入稳定事实。</p>
  </div>
  <div>
    <h3>记忆原子化</h3>
    <p>关键事实会拆成独立原子，每个原子都有类型、TTL、重要性、访问次数和衰减状态。</p>
  </div>
</div>

## 记忆从哪里来？

插件有两种写入路径：

| 路径 | 触发方式 | 适合内容 |
| --- | --- | --- |
| 自动反思 | 对话达到配置的总结轮次 | 长期偏好、项目上下文、关系、稳定事实 |
| Agent 主动写入 | 模型调用 `memorize_long_term_memory` | 用户明确要求“记住”、关键约定、长期任务 |

## 记忆如何被召回？

用户发送消息后，插件会在 LLM 请求前执行检索。检索结果可以追加到用户消息、放到消息前后，或模拟工具调用注入到上下文。

<img class="diagram" src="/images/retrieval-flow.svg" alt="Dual route retrieval flow">

召回结果会综合这些因素排序：

| 因素 | 说明 |
| --- | --- |
| 关键词命中 | BM25 和图谱关键词能快速找到明确实体或短语 |
| 语义相似 | 向量检索适合找表达不同但含义接近的记忆 |
| 图谱关系 | 实体、关系和跨记忆边能补上“人、事、物”的结构关联 |
| 重要性 | 用户偏好、长期约定等高重要性内容更容易被保留和召回 |
| 时间衰减 | 旧记忆不会永久占据高权重，除非被反复访问或强化 |

## 生命周期如何工作？

<img class="diagram" src="/images/lifecycle.svg" alt="Memory lifecycle">

LivingMemory 会定期处理长期记忆：

| 机制 | 作用 |
| --- | --- |
| 重要性衰减 | 让过旧、低价值记忆逐渐降低权重 |
| 访问强化 | 被反复召回的记忆会获得更高保留价值 |
| 原子 TTL | 不同类型事实拥有不同生命周期 |
| 自动清理 | 删除过期或低价值记忆，控制数据库规模 |
| 安全备份 | 版本更新和迁移前自动备份，降低升级风险 |
