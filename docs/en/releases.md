# Releases and Upgrades

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
