import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const dashboard = join(here, "../../pages/dashboard");
const read = (name) => readFileSync(join(dashboard, name), "utf-8");

function rgb(hex) {
  const value = hex.replace("#", "");
  return [0, 2, 4].map((offset) => parseInt(value.slice(offset, offset + 2), 16));
}

function luminance(hex) {
  const channel = (value) => {
    const normalized = value / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4;
  };
  const [r, g, b] = rgb(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(foreground, background) {
  const [high, low] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (high + 0.05) / (low + 0.05);
}

test("default theme focus ring and tertiary text meet WCAG contrast", () => {
  const art = read("art-direction.css");
  const tertiary = [...art.matchAll(/--text-tertiary:\s*(#[0-9a-f]{6});/g)].map((m) => m[1]);
  assert.ok(tertiary.length >= 2, "应同时定义浅色与深色 tertiary");
  assert.ok(
    contrast(tertiary[0], "#f7faf6") >= 4.5,
    `浅色 tertiary 对比度不足: ${contrast(tertiary[0], "#f7faf6").toFixed(2)}`
  );
  assert.ok(
    contrast(tertiary[1], "#0d0f0c") >= 4.5,
    `深色 tertiary 对比度不足: ${contrast(tertiary[1], "#0d0f0c").toFixed(2)}`
  );
  assert.match(art, /outline:\s*2px solid var\(--border-focus\)/, "focus ring 应使用高对比变量");
  assert.ok(contrast("#245d61", "#f7faf6") >= 3, "浅色 focus ring 对比度不足");

  const themes = read("themes.css");
  const studio = themes.match(/\[data-style="studio"\] \{[\s\S]*?--text-tertiary:\s*(#[0-9a-f]{6});/);
  assert.ok(studio, "studio 主题应覆盖 tertiary");
  assert.ok(
    contrast(studio[1], "#f0f2fa") >= 4.5,
    `studio 浅色 tertiary 对比度不足: ${contrast(studio[1], "#f0f2fa").toFixed(2)}`
  );
});

test("graph canvas, peek panel and action dialog expose accessible semantics", () => {
  const index = read("index.html");
  const peek = read("modules/peek-panel.js");
  const graph = read("graph-ui.js");
  const settings = read("modules/settings-page.js");

  assert.match(index, /id="graph-canvas-state"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(index, /id="peek-panel"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="peek-title"/);
  assert.match(index, /id="graph-node-list-btn"/);
  assert.match(index, /id="graph-node-list-dialog"[^>]*aria-labelledby="graph-node-list-title"/);
  assert.match(index, /id="action-dialog"[^>]*aria-labelledby="action-dialog-title"/);

  assert.match(peek, /_trapFocus\(event\)/);
  assert.match(peek, /_previousFocus/);
  assert.match(peek, /role="dialog" aria-modal="true"/);

  assert.match(graph, /setAttribute\("role", "img"\)/);
  assert.match(graph, /graph\.canvasAria/);
  assert.match(graph, /graph-node-list/);

  assert.match(settings, /type === "text"/);
  assert.match(settings, /<textarea/);
  assert.match(settings, /confirmAction/);
  assert.doesNotMatch(settings, /window\.confirm/);
});

test("dashboard language handling covers ja everywhere", () => {
  const i18n = read("i18n.js");
  const app = read("app.js");
  assert.match(i18n, /lang === "ja" \? "ja"/);
  assert.match(i18n, /function languageTag/);
  assert.match(app, /\["zh", "en", "ru", "ja"\]/);
});
