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

test("default theme and presets keep tertiary text and focus ring readable", () => {
  const tokens = read("tokens.css");
  const art = read("art-direction.css");
  const colors = (css, name) => [...css.matchAll(new RegExp("--" + name + ":\\s*(#[0-9a-f]{6});", "g"))].map(match => match[1]);
  const [light, dark] = [colors(tokens, "bg-app"), colors(tokens, "text-tertiary"), colors(tokens, "accent")]
    .reduce((acc, list) => [[...acc[0], list[0]], [...acc[1], list[1]]], [[], []]);
  for (const [background, tertiary, focus] of [light, dark]) {
    assert.ok(contrast(tertiary, background) >= 4.5, "tertiary text contrast");
    assert.ok(contrast(focus, background) >= 3, "focus ring contrast");
  }
  assert.match(art, /outline:\s*2px solid var\(--border-focus\)/);

  for (const [name, mode] of [["paper", ""], ["paper", '[data-theme="dark"]'], ["terminal", ""], ["terminal", '[data-theme="dark"]']]) {
    const block = tokens.match(new RegExp(':root\\[data-style="' + name + '"\\]' + mode.replace(/[[\]]/g, "\\$&") + " \\{([\\s\\S]*?)\\n\\}"));
    assert.ok(block, name + mode + " preset block");
    const [tertiary] = colors(block[1], "text-tertiary");
    const [card] = colors(block[1], "bg-card");
    assert.ok(contrast(tertiary, card) >= 4.5, name + mode + " tertiary contrast " + contrast(tertiary, card).toFixed(2));
  }
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
