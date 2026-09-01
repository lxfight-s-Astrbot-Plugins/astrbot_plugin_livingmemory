import test from "node:test";
import assert from "node:assert/strict";
import { esc } from "../../pages/dashboard/modules/utils.js";

test("esc escapes quotes so interpolated attributes cannot break out", () => {
  const payload = 'FACT" onfocus="alert(document.cookie)" autofocus="';
  const rendered = '<input value="' + esc(payload) + '" />';
  // The double quotes inside the payload must be entity-encoded
  assert.equal(rendered.includes('" onfocus='), false);
  assert.ok(rendered.includes("&quot;"));
});

test("esc escapes single quotes and core HTML metacharacters", () => {
  assert.equal(esc("<img src=x onerror=alert(1)>"), "&lt;img src=x onerror=alert(1)&gt;");
  assert.equal(esc("a&b'c\"d"), "a&amp;b&#39;c&quot;d");
});

test("esc is idempotent-safe for entity round trip in attribute context", () => {
  const payload = '" onmouseover="x';
  const html = '<input value="' + esc(payload) + '" />';
  // no raw double quote from payload may survive inside the attribute value
  assert.equal(html.includes(payload), false);
  assert.equal(html, '<input value="&quot; onmouseover=&quot;x" />');
});
