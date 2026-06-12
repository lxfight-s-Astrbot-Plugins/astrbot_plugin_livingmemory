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

## 访问地址

默认 Pages 地址为：

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/
```

英文文档：

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/
```
