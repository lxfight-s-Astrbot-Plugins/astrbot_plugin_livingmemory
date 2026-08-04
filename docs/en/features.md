# Features

LivingMemory is not just a chat log. It transforms conversations into searchable, decay-aware long-term memories that agents can also use directly.

<div class="feature-grid">
  <div>
    <h3>Automatic reflection</h3>
    <p>After the configured number of rounds, the plugin asks the LLM to summarize recent conversation into structured memory.</p>
  </div>
  <div>
    <h3>Rich retrieval content</h3>
    <p>Retrieval content is assembled from <code>summary</code> + <code>key_facts</code> to preserve information density. <code>persona_summary</code> powers memory injection, dashboard display, and persona expression.</p>
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
| Manual administrator summary | `/lmem summarize [message_count]` | Save the current context immediately or re-summarize the latest N messages |

Summarization produces a persona-styled `summary` used for both memory injection and dashboard display (`persona_summary`), while retrieval content is assembled by code from `summary` and `key_facts` so a single compressed summary cannot drain information density. Custom prompts may still emit a factual `canonical_summary` for consumers such as graph extraction. Memories above the source-retention threshold also store their source messages separately in SQLite. Those messages are not indexed for normal recall, but remain available for Dashboard review, re-summarization, and deep source retrieval by the Agent recall tool.

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
| Retrieval boundaries | Candidates can be filtered by minimum importance, vector similarity, and memory type |
| Recent-memory reserve | Dedicated slots keep recent in-scope memories from being displaced by similarity ranking alone |
| Recent context | Recall can include recent conversation within a configured age limit to reduce topic drift |

## Who can access which memories?

The same boundaries apply to both memory writes and recall:

| Capability | Behavior |
| --- | --- |
| Memory scopes | `session` isolates each conversation, `user` shares across private and group chats for one platform user, `global` shares broadly, and `legacy` preserves earlier behavior |
| Forced isolation | Selected conversations always keep an independent scope, even when a shared mode is active |
| Memory allowlist | Limits capture, summarization, recall, and Agent memory tools to selected users, groups, or complete session IDs |
| Identity aliases | Maps platform identities to stable names before summarization; graph participants also reuse stable identities instead of splitting on nickname changes |

Scope changes affect newly written memories only. Existing data is not migrated automatically.

## Lifecycle behavior

<img class="diagram" src="/images/lifecycle.svg" alt="Memory lifecycle">

| Mechanism | Purpose |
| --- | --- |
| Importance decay | Reduces the weight of old low-value memories |
| Access reinforcement | Frequently recalled memories are more likely to stay relevant |
| Atom TTL | Different fact types can age differently |
| Cleanup or archiving | Low-value memory can be deleted permanently or removed from recall indexes while retained as a recoverable archive |
| Important-memory protection | Memories above a configured threshold are excluded from daily importance decay |
| Source retention | High-importance memories can retain source messages for audit and re-summarization |
| Safe backups | Creates backups before version updates and migrations |

Archived memories do not participate in normal recall. Restoring one from the Dashboard regenerates its embedding and rebuilds BM25, graph, atom, and other derived indexes.

## How large index maintenance stays available

After startup, LivingMemory checks document, vector, BM25, and graph indexes in the background instead of blocking plugin readiness when extensive repairs are required:

| Mechanism | Purpose |
| --- | --- |
| Provider fingerprint | A changed embedding provider or model triggers a full vector generation rebuild, even when dimensions are unchanged |
| Batching and checkpoints | Read size, embedding batches, concurrency, and delays are bounded; full vector rebuilds can continue from checkpoints |
| Shadow indexes | BM25, FAISS, and graph data are built in a separate generation and switched only after success; failures leave the live generation intact |
| Concurrent-write reconciliation | A final consistency pass repairs data written or changed while maintenance was running |
| Visible status | `/lmem status` reports checking, rebuilding, partial, failed, and cancelled states with progress |

See [WebUI management](/en/webui) for data operations and [Configuration](/en/configuration) for all related settings.
