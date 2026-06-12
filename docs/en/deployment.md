# Docs Deployment

This repository now includes VitePress configuration and a GitHub Actions workflow. After pushing to `main` or `master`, GitHub Actions builds the docs and deploys them to GitHub Pages.

## Local preview

```bash
npm install
npm run docs:dev
```

Build static files:

```bash
npm run docs:build
```

The build output is:

```text
docs/.vitepress/dist
```

## GitHub Pages settings

For first-time setup, open:

`Settings -> Pages -> Build and deployment -> Source -> GitHub Actions`

After that, pushes that touch docs, VitePress config, `package.json`, or the deployment workflow will trigger deployment.

## URLs

Default Pages URL:

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/
```

English docs:

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/
```
