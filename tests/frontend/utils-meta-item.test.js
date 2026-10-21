import test from "node:test";
import assert from "node:assert/strict";
import { metaItem } from "../../pages/dashboard/modules/utils.js";

test("metaItem escapes plain values by default", () => {
  const html = metaItem("label", '<img src=x onerror="alert(1)">');
  assert.equal(html.includes("<img src=x"), false);
  assert.ok(html.includes("&lt;img src=x"));
});

test("metaItem keeps pre-rendered HTML only when explicitly requested", () => {
  const html = metaItem("label", '<span class="origin-tag">AI</span>', true);
  assert.ok(html.includes('<span class="origin-tag">AI</span>'));
});
