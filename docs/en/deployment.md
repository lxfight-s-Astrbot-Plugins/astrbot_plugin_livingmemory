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

## Release and documentation updates

Keep `metadata.yaml`, the registration version in `main.py`, the backup manager's `PLUGIN_VERSION`, `package.json` and the lockfile in sync. Add the matching version section to `CHANGELOG.md`, covering every merged PR since the previous release tag with PR links and GitHub authors. Update both languages of [Releases and Upgrades](/en/releases).

After the version change merges to the default branch, `Auto Release on Version Update` reads that changelog section and tags the exact workflow-triggering commit. Versions containing `beta`, `alpha`, `rc` or `pre` are marked as prereleases. `Deploy VitePress Docs` publishes the site separately; verify both the release and Pages deployment after publishing.

## URLs

Default Pages URL:

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/
```

English docs:

```text
https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/en/
```
