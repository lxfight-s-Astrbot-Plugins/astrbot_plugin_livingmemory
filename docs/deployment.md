# 部署文档站

本仓库已经包含 VitePress 配置和 GitHub Actions workflow。推送到 `main` 或 `master` 后，GitHub Actions 会自动构建并部署到 GitHub Pages。

## 本地预览

```bash
npm install
npm run docs:dev
```

构建静态文件：

```bash
npm run docs:build
```

构建产物位于：

```text
docs/.vitepress/dist
```

## GitHub Pages 设置

首次使用时，在仓库设置中打开：

`Settings -> Pages -> Build and deployment -> Source -> GitHub Actions`

之后每次推送文档、VitePress 配置、`package.json` 或部署 workflow，都会触发部署。

## 版本发布与文档同步

发布时同步更新 `metadata.yaml`、`main.py` 注册版本、备份管理器 `PLUGIN_VERSION`、`package.json` 和 lockfile，并在 `CHANGELOG.md` 添加对应版本的章节。章节应覆盖上一个发布标签以来的所有合并 PR，列出 PR 链接及 GitHub 作者；[版本与升级](/releases)的中英文页面同步维护。

版本变更合入主分支后，`Auto Release on Version Update` 从 `CHANGELOG.md` 的对应章节生成发布说明，标签指向触发 workflow 的确切提交。带 `beta`、`alpha`、`rc` 或 `pre` 的版本标记为预发布。文档部署由 `Deploy VitePress Docs` 独立完成，发布后应同时确认 Release 和 Pages 两个结果。

## 访问地址

默认 Pages 地址为：

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/
```

英文文档：

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/
```
