# Features

LivingMemory is not just a chat log. It transforms conversations into searchable, decay-aware long-term memories that agents can also use directly.

<div class="feature-grid">
  <div>
    <h3>Automatic reflection</h3>
    <p>After the configured number of rounds, the plugin asks the LLM to summarize recent conversation into structured memory.</p>
  </div>
  <div>
    <h3>Dual-channel summaries</h3>
    <p><code>canonical_summary</code> is optimized for factual retrieval. <code>persona_summary</code> is optimized for prompt injection and persona expression.</p>
  </div>
  <div>
    <h3>Agent memory tools</h3>
    <p>Agents can call <code>recall_long_term_memory</code> to search old memory or <code>memorize_long_term_memory</code> to store durable facts.</p>
  </div>
  <div>
    <h3>Memory atomization</h3>
    <p>Important facts become independent atoms with type, TTL, importance, access count, and decay state.</p>
  </div>
</div>

## Where memories come from

| Path | Trigger | Best for |
| --- | --- | --- |
| Automatic reflection | Conversation reaches the configured summary rounds | Long-term preferences, project context, relationships, durable facts |
| Agent write tool | The model calls `memorize_long_term_memory` | Explicit "remember this" requests, important agreements, long-running tasks |

## How recall works

Before the LLM request is sent, LivingMemory retrieves relevant memories. Results can be appended to the user message, placed before or after it, or injected as simulated tool-call context.

<img class="diagram" src="/images/retrieval-flow.svg" alt="Dual route retrieval flow">

Ranking combines:

| Factor | What it does |
| --- | --- |
| Keyword match | BM25 and graph keyword retrieval quickly find concrete entities and phrases |
| Semantic similarity | Vector retrieval handles different wording with similar meaning |
| Graph relationships | Entities and cross-memory edges add structure across facts |
| Importance | Durable preferences and agreements are favored |
| Time decay | Old memories gradually lose weight unless repeatedly accessed or reinforced |

## Lifecycle behavior

<img class="diagram" src="/images/lifecycle.svg" alt="Memory lifecycle">

| Mechanism | Purpose |
| --- | --- |
| Importance decay | Reduces the weight of old low-value memories |
| Access reinforcement | Frequently recalled memories are more likely to stay relevant |
| Atom TTL | Different fact types can age differently |
| Automatic cleanup | Removes expired or low-value memory to control database size |
| Safe backups | Creates backups before version updates and migrations |
