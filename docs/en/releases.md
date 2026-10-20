# Releases and Upgrades

## 2.7.0-beta.3 · October 20, 2026

This prerelease rebuilds the dashboard from "you can see it" to "you can judge it", and lifts recall quality at the same time. No new dependencies and no change to default runtime behaviour; stay on 2.6.1 for the stable channel.

[Download 2.7.0-beta.3](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.3)

### What changed

**Dashboard**
- Every memory now names its writer (Agent-written, imported, re-summarized, or auto) and flags summaries whose quality looks low, so what the bot recorded on its own is finally distinguishable.
- The recall test page explains itself: each result lists which retrieval routes matched it (document keyword, document vector, graph keyword, graph vector), and the header echoes the `k` and session filter used.
- Graph and memory list are now connected both ways: a node lists its related memories and opens them, and a memory offers **Locate in graph** instead of asking for a memory ID. Node details on the graph page are no longer modal, so several nodes can be compared in a row.
- Failures on the system and prompt pages stay on the page with a retry instead of vanishing with a two-second toast, and the standing explanatory copy was removed in favour of control state and feedback.

**Recall quality**
- Document-route fusion moved from rank-based to normalized-score weighting, each route now contributes `3k` candidates before fusion, and documents found by both routes at close ranks receive a consensus bonus.
- BM25 indexes and queries CJK character bigrams in addition to jieba tokens, so short queries and proper nouns (shop, person, model names) no longer miss because the two sides were segmented differently.
- Measured: Chinese synthetic set (149 topic clusters / 1591 near-duplicate memories / 1211 tiered queries) MRR `0.86 → 0.95`, Hit@1 `0.79 → 0.93`; English LoCoMo (1536 evidence QA) MRR `0.66 → 0.73`. Method and results in #305 / #306 / #307.

### Upgrade notes

- Existing configuration needs no changes; new parameters have defaults and fall back automatically.
- **Run `/lmem rebuild-index` once if you want existing memories to benefit from the bigram tokens**; new writes pick them up automatically. Skipping it is safe, older memories just keep the previous index.
- Dashboard preferences (style, light/dark, language) stay in the browser and survive the upgrade.

### Known limitations

- In **Full Graph** mode the node panel does not list related memories yet (that snapshot does not return entries; limited overview and query modes do).
- The Chinese evaluation set is model-generated and is meant for comparing retrieval designs, not for estimating live traffic distribution.

### Pull requests in this release

| PR | Change | Author |
| --- | --- | --- |
| [#299](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/299) | Show retrieval route scores in the recall test page | [@lxfight](https://github.com/lxfight) |
| [#300](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/300) | Surface memory origin and why it was remembered | [@lxfight](https://github.com/lxfight) |
| [#301](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/301) | Link the graph and the memory list in both directions | [@lxfight](https://github.com/lxfight) |
| [#302](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/302) | Make graph encodings self-consistent and stable | [@lxfight](https://github.com/lxfight) |
| [#303](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/303) | Unify request feedback and fix system/prompt semantics | [@lxfight](https://github.com/lxfight) |
| [#304](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/304) | Remove dead code, overridden styles and decoration | [@lxfight](https://github.com/lxfight) |
| [#305](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/305) | Fuse by normalized scores with wider candidates | [@lxfight](https://github.com/lxfight) |
| [#306](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/306) | Reward cross-route agreement | [@lxfight](https://github.com/lxfight) |
| [#307](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/307) | Index CJK character bigrams in BM25 | [@lxfight](https://github.com/lxfight) |

### Install and upgrade

1. Stop AstrBot and keep a backup of the plugin code, its data directory and configuration.
2. Download **Source code (zip)** from the release above, unpack it into `data/plugins/astrbot_plugin_livingmemory`, and keep the existing data and configuration instead of replacing them with empty folders.
3. Start or reload AstrBot, confirm the version reads `2.7.0-beta.3` in the plugin list, then open `Plugins → LivingMemory → Pages → dashboard`.
4. To let existing memories benefit from the bigram tokens, send `/lmem rebuild-index` (it runs in batches while recall stays available).

With Git-managed plugin code, switch the clean plugin worktree to the tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.3
```

## 2.7.0-beta.2 · September 16, 2026

This prerelease covers the refactors and behavioral standardization since `2.7.0-beta.1`. Stay on 2.6.1 for the stable channel.

[Download 2.7.0-beta.2](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.2) · [Stable 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1) · [Full comparison](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/compare/2.7.0-beta.1...2.7.0-beta.2)

### What's new

- **Standardized memory injection — the plugin no longer touches host conversation history:** to stay compatible with AstrBot's architecture and its future technical direction, this release narrows the plugin's interactions with the LLM request pipeline and removes non-standard operations that exceeded the plugin's responsibilities. Memory injection is now exclusively an add-only write through AstrBot's official extension point: memories are appended to the current user message as a `mark_as_temp` temporary part that AstrBot filters out when saving conversation history. Injection never writes into conversation history and never affects the prefix cache.
- **Removed history format normalization:** the plugin no longer rewrites the content structure of history messages; both content-parts lists and plain strings are preserved exactly as AstrBot stored them.
- **Deprecated non-append injection modes:** `user_message_before`, `user_message_after`, `fake_tool_call` and `fake_tool_call_deepseek_v4` (plus the previously deprecated `system_prompt`) are deprecated; configured values automatically fall back to `extra_user_content` with a logged warning, no manual configuration changes required.
- **Legacy residue cleanup retained:** `auto_remove_injected` only removes injection fragments left in history by legacy injection modes from the request context; it never touches the user's own history messages.
- **Internal refactors (#268–#272):** dead code removal, page API and consolidation routed through the MemoryEngine public API, deduplicated JSON helpers and consolidated schema DDL, recall scope fallback aligned with write-side semantics, and Mixin host contracts declared as class annotations.

### Upgrade notes

- Legacy injection settings need no manual adjustment — they fall back automatically at runtime. To purge text-form injection residue left in stored history, run `/lmem cleanup` (fake tool-call message pairs are removed from context automatically on every request).

### Complete PR list and authors

| PR | Change | Author |
| --- | --- | --- |
| [#268](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/268) | Remove dead code paths and unused exports | [@lxfight](https://github.com/lxfight) |
| [#269](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/269) | Route page API and consolidation through the MemoryEngine public API | [@lxfight](https://github.com/lxfight) |
| [#270](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/270) | Dedupe JSON helpers and consolidate schema DDL into one source | [@lxfight](https://github.com/lxfight) |
| [#271](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/271) | Align recall scope fallback with write-side semantics | [@lxfight](https://github.com/lxfight) |
| [#272](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/272) | Declare mixin host contracts as class annotations | [@lxfight](https://github.com/lxfight) |
| [#273](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/273) | Standardize memory injection as add-only; version 2.7.0-beta.2 | [@lxfight](https://github.com/lxfight) |

## 2.7.0-beta.1 · September 7, 2026

This prerelease includes all merged changes since stable **2.6.1**. Stay on 2.6.1 for the stable channel, or install the exact beta tag to try the new dashboard and recall fixes.

[Download 2.7.0-beta.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.7.0-beta.1) · [Stable 2.6.1](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases/tag/2.6.1) · [Full comparison](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/compare/2.6.1...2.7.0-beta.1)

### What's new

- **Four styles:** Editorial, Studio, Paper and Terminal each support light, dark and automatic modes, with preferences saved in the current browser.
- **Steadier graph reading:** zoom, hover and selection pause ambient movement. Stable label priority, collision bounds and bounded fonts reduce flicker; settled inspection stops idle rendering.
- **Clearer workflows:** filter, select, then act. Transfers have their own section, explicit export scope and import preview/confirmation. Editors protect unsaved changes and prevent duplicate saves or switching targets during a write.
- **More reliable recall and recovery:** newly written long memories retain full text; scope filters run before result limits; route failures are isolated; cached recall still checks atom expiry; graph rebuild rollback preserves pending deltas.
- **Less repeated work:** dual-route queries share an embedding, graph source documents load in batches, and FAISS search runs off the event loop. The dashboard coalesces duplicate requests and scroll renders, with retryable failure states.

Previously truncated text cannot be reconstructed by this upgrade. Atom-backed graph recall includes only facts that are still active in their lifecycle.

### Complete PR list and authors

The range starts at Git tag `2.6.1`. Credits below use each PR's verified GitHub author.

| PR | Change | Author |
| --- | --- | --- |
| [#259](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/259) | Recall, long-text storage and index recovery fixes; dashboard loading and mobile improvements | [@lxfight](https://github.com/lxfight) |
| [#260](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/260) | Four switchable dashboard styles | [@lxfight](https://github.com/lxfight) |
| [#261](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/261) | Stable graph labels during zoomed inspection | [@lxfight](https://github.com/lxfight) |
| [#262](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/262) | Filtering, batch actions, import and editing workflows with concurrency guards | [@lxfight](https://github.com/lxfight) |
| [#263](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/pull/263) | Beta publication, complete PR credits, README redesign and VitePress documentation | [@lxfight](https://github.com/lxfight) |

### Install or upgrade

1. Stop AstrBot and keep a backup of your existing plugin code, plugin data directory and configuration. Appearance preferences are stored separately in the browser.
2. Download **Source code (zip)** from the beta release above. Extract the plugin into `data/plugins/astrbot_plugin_livingmemory`, without nesting another versioned directory inside it. Preserve existing plugin data and configuration.
3. Start or reload AstrBot and wait for dependencies and initialization. Confirm `2.7.0-beta.1` in the plugin list, then open `Plugins → LivingMemory → Pages → dashboard`.
4. Refresh the dashboard, using a hard refresh if the old interface remains. Follow [Verify the setup](/en/guide/getting-started#verify-the-setup) to check status, writes and recall.

For an existing Git installation, switch a clean plugin checkout to the specific tag before starting AstrBot:

```bash
git fetch origin --tags
git switch --detach 2.7.0-beta.1
```

GitHub marks this version **Pre-release**. Use the specific tag: `releases/latest` still points to stable. Plugin Pages requires AstrBot 4.24.2 or later.

### What to test and report

Try theme/mode switching and persistence, zoomed dense graphs, combined filters, cancelling import previews, unsaved draft protection, and real-provider recall across sessions and long-text writes. [Report issues](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/issues/new/choose) with plugin/AstrBot versions, theme, browser, reproduction steps and redacted logs.

The suite passes 785 Python tests and 55 frontend tests, plus VitePress builds and browser checks using mocked AstrBot APIs. These checks do not replace validation with real providers and your data.

### Return to stable

Stop AstrBot, restore the plugin code from tag `2.6.1`, then start AstrBot. To also return data to its pre-upgrade state, restore the matching data and configuration backup while AstrBot is stopped. Memories created after that backup will not be present in it. Do not replace database files while AstrBot is running.

## Earlier releases

See the full [CHANGELOG](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/blob/master/CHANGELOG.md) and [GitHub Releases](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/releases).
