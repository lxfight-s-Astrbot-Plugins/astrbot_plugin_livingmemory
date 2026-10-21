#!/usr/bin/env node
/**
 * Build the dashboard's static bundles.
 *
 * The dashboard is served as plain files by AstrBot Pages, so there is no
 * bundler at runtime. This script only concatenates and lightly minifies:
 *   - four stylesheets -> assets/styles.min.css
 *   - appearance.js + i18n.js -> assets/base.min.js (loaded in <head>)
 *   - six graph scripts -> assets/graph.min.js (loaded at end of <body>)
 *
 * Source files stay editable; run `node scripts/build_dashboard.mjs` after
 * changing them or `node scripts/build_dashboard.mjs --check` in CI.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DASHBOARD = join(ROOT, "pages", "dashboard");
const OUTPUT = join(DASHBOARD, "assets");
const CHECK = process.argv.includes("--check");

const CSS_SOURCES = ["styles.css", "art-direction.css", "themes.css", "workflows.css"];
const BASE_SOURCES = ["appearance.js", "i18n.js"];
const GRAPH_SOURCES = [
  "graph-shared.js",
  "graph-layout-core.js",
  "graph-renderer.js",
  "graph-interaction.js",
  "graph-2d.js",
  "graph-ui.js",
];

function minifyCss(text) {
  // Conservative CSS minification: drop comments, collapse runs of whitespace
  // and remove whitespace that is never significant around braces/commas/semicolons.
  // Whitespace before `(` is preserved so at-rules like `@media (…)` stay valid.
  return (
    text
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\s+/g, " ")
      .replace(/\s*([{};,>])\s*/g, "$1")
      .replace(/;}/g, "}")
      .trim() + "\n"
  );
}

function readSource(name) {
  // Normalize checkout line endings so the bundle is byte-identical on
  // Windows (core.autocrlf) and Linux, and matches the committed artifact.
  return readFileSync(join(DASHBOARD, name), "utf8").replace(/\r\n/g, "\n");
}

function joinScripts(header, sources) {
  const parts = [`/* ${header} (generated; edit the source files and rebuild) */`];
  for (const name of sources) {
    const text = readSource(name);
    parts.push(`\n/* ---- ${name} ---- */\n${text.trimEnd()}\n;`);
  }
  return parts.join("\n") + "\n";
}

const artifacts = new Map([
  ["styles.min.css", minifyCss(CSS_SOURCES.map(readSource).join("\n"))],
  ["base.min.js", joinScripts("LivingMemory dashboard base bundle", BASE_SOURCES)],
  ["graph.min.js", joinScripts("LivingMemory dashboard graph bundle", GRAPH_SOURCES)],
]);

mkdirSync(OUTPUT, { recursive: true });

let stale = false;
for (const [name, content] of artifacts) {
  const path = join(OUTPUT, name);
  let current = "";
  try {
    current = readFileSync(path, "utf8").replace(/\r\n/g, "\n");
  } catch {
    current = "";
  }
  if (current !== content) {
    stale = true;
    if (CHECK) {
      console.error(`stale dashboard bundle: assets/${name}`);
      continue;
    }
    writeFileSync(path, content, "utf8");
    console.log(`wrote assets/${name} (${Buffer.byteLength(content)} bytes)`);
  }
}

if (CHECK) {
  if (stale) {
    console.error("dashboard 构建产物不是最新，请运行 node scripts/build_dashboard.mjs");
    process.exit(1);
  }
  console.log("dashboard bundles are up to date");
}
