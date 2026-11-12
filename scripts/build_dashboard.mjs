#!/usr/bin/env node
/**
 * Build the dashboard's static bundles.
 *
 * The dashboard is served as plain files by AstrBot Pages, so there is no
 * bundler at runtime. This script produces:
 *   - stylesheets -> assets/styles.min.css
 *   - appearance.js + i18n.js -> assets/base.min.js (loaded in <head>, minified)
 *   - graph scripts -> assets/graph.min.js (loaded at end of <body>, minified)
 *   - graph-topology + graph-layout-core + graph-layout-worker ->
 *     assets/graph-layout-worker.min.js (single classic Worker file)
 *   - app.js + modules/*.js -> assets/app.min.js (ES module entry)
 *   - independent WebGL ESM bundle with its layout worker embedded
 *
 * `--check` also enforces the first-load gzip budget (index.html + the three
 * above-the-fold bundles + the lucide subset ≤ 80 KiB) so the dashboard cannot
 * silently grow again.
 *
 * Source files stay editable; run `node scripts/build_dashboard.mjs` after
 * changing them or `node scripts/build_dashboard.mjs --check` in CI.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { build, transform } from "esbuild";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DASHBOARD = join(ROOT, "pages", "dashboard");
const OUTPUT = join(DASHBOARD, "assets");
const CHECK = process.argv.includes("--check");

/* 首屏预算按浏览器实际下载的静态资源计：CSS + 3 个 bundle + lucide 子集。
   index.html 由宿主注入 bridge/站点元数据后才会下发，源文件字节数不等于
   线上大小，因此单独打印、不纳入预算口径。 */
const FIRST_LOAD_GZIP_BUDGET = 80 * 1024;
const FIRST_LOAD_ASSETS = ["styles.min.css", "base.min.js", "graph.min.js", "app.min.js"];

const CSS_SOURCES = ["styles.css", "workflows.css", "graph-3d.css", "tokens.css", "art-direction.css"];
const BASE_SOURCES = ["appearance.js", "i18n.js"];
const GRAPH_SOURCES = [
  "graph-shared.js",
  "graph-topology.js",
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

async function minifyScriptBundle(header, sources) {
  const result = await transform(joinScripts(header, sources), {
    minify: true,
    charset: "utf8",
    target: "es2022",
    legalComments: "none",
  });
  return `/* ${header} (generated; edit the source files and rebuild) */\n${result.code.trimEnd()}\n`;
}

function joinLayoutWorkerSource() {
  /* 2D 布局 Worker 原先在运行时 importScripts 两个源文件；构建期合并为单文件。 */
  const worker = readSource("graph-layout-worker.js").replace(
    /^importScripts\([^)]*\);\s*$/m,
    ""
  );
  return [
    readSource("graph-topology.js"),
    readSource("graph-layout-core.js"),
    worker,
  ].join("\n;\n");
}

function gzipBytes(content) {
  return gzipSync(Buffer.from(content, "utf8"), { level: 9 }).length;
}

function firstLoadGzipBytes(artifacts) {
  const contents = [
    ...FIRST_LOAD_ASSETS.map((name) => artifacts.get(name)),
    readSource("vendor/lucide-subset.js"),
  ];
  return contents.reduce((total, content) => total + gzipBytes(content), 0);
}

const artifacts = new Map();
artifacts.set("styles.min.css", minifyCss(CSS_SOURCES.map(readSource).join("\n")));
artifacts.set(
  "base.min.js",
  await minifyScriptBundle("LivingMemory dashboard base bundle", BASE_SOURCES)
);
artifacts.set(
  "graph.min.js",
  await minifyScriptBundle("LivingMemory dashboard graph bundle", GRAPH_SOURCES)
);
const layoutWorker = await transform(joinLayoutWorkerSource(), {
  minify: true,
  charset: "utf8",
  target: "es2022",
  legalComments: "none",
});
artifacts.set(
  "graph-layout-worker.min.js",
  "/* LivingMemory 2D layout worker (generated; edit the source files and rebuild) */\n" +
    layoutWorker.code.trimEnd() +
    "\n"
);

const worker = await build({
  entryPoints: [join(DASHBOARD, "graph-3d-worker.js")], bundle: true, minify: true,
  format: "iife", target: "es2022", write: false, legalComments: "inline",
});
const app = await build({
  entryPoints: [join(DASHBOARD, "app.js")], bundle: true, minify: true,
  format: "esm", target: "es2022", write: false, legalComments: "inline",
});
artifacts.set("app.min.js", app.outputFiles[0].text);
/* 用户档案页按需加载，不进入首屏 bundle。 */
const profile = await build({
  entryPoints: [join(DASHBOARD, "modules", "profile-page.js")], bundle: true, minify: true,
  format: "esm", target: "es2022", write: false, legalComments: "inline",
});
artifacts.set("profile.min.js", profile.outputFiles[0].text);

/* 外观面板样式首次打开时加载。 */
artifacts.set("appearance.min.css", minifyCss(readSource("appearance.css")));
/* 档案页样式同样按需加载，不进入首屏 styles.min.css。 */
artifacts.set("profile.min.css", minifyCss(readSource("profile.css")));

/* 诊断页同样懒加载，包含 4 个 tab 所需的逻辑与样式。 */
const diagnostics = await build({
  entryPoints: [join(DASHBOARD, "modules", "diagnostics-page.js")], bundle: true, minify: true,
  format: "esm", target: "es2022", write: false, legalComments: "inline",
});
artifacts.set("diagnostics.min.js", diagnostics.outputFiles[0].text);
artifacts.set("diagnostics.min.css", minifyCss(readSource("diagnostics.css")));
const scene = await build({
  entryPoints: [join(DASHBOARD, "graph-3d.js")], bundle: true, minify: true,
  format: "esm", target: "es2022", write: false, legalComments: "inline",
  supported: { "template-literal": false },
  define: { __GRAPH_3D_WORKER_SOURCE__: JSON.stringify(worker.outputFiles[0].text) },
});
artifacts.set("graph-3d.min.js", scene.outputFiles[0].text);
const packages = ["three", "d3-force-3d", "d3-binarytree", "d3-octree", "d3-quadtree", "d3-dispatch", "d3-timer"];
artifacts.set("graph-3d.LICENSE.txt", packages.map(name =>
  name + "\n" + readFileSync(join(ROOT, "node_modules", name, "LICENSE"), "utf8").replace(/\r\n/g, "\n")
).join("\n"));

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

const firstLoadGzip = firstLoadGzipBytes(artifacts);
const includeHtmlGzip =
  firstLoadGzip + gzipBytes(readSource("index.html"));
const usage = ((100 * firstLoadGzip) / FIRST_LOAD_GZIP_BUDGET).toFixed(1);
console.log(
  `首屏资源 gzip ${firstLoadGzip} B / 预算 ${FIRST_LOAD_GZIP_BUDGET} B (${usage}%)；含 index.html 约 ${includeHtmlGzip} B`
);

if (CHECK) {
  if (stale) {
    console.error("dashboard 构建产物不是最新，请运行 node scripts/build_dashboard.mjs");
    process.exit(1);
  }
  if (firstLoadGzip > FIRST_LOAD_GZIP_BUDGET) {
    console.error(
      `首屏 gzip 体积 ${firstLoadGzip} B 超出预算 ${FIRST_LOAD_GZIP_BUDGET} B`
    );
    process.exit(1);
  }
  console.log("dashboard bundles are up to date");
}
