# 版本与升级

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
