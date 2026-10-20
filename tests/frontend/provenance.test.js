import assert from "node:assert/strict";
import test from "node:test";

import { MemoryPage } from "../../pages/dashboard/modules/memory-page.js";
import { PeekPanel } from "../../pages/dashboard/modules/peek-panel.js";

// window.t 返回 key 本身，断言即"用了哪个文案键"
function env(t) {
  globalThis.window = { t: (key, ...args) => [key, ...args].join(" "), requestAnimationFrame() {} };
  t.after(() => { delete globalThis.window; delete globalThis.document; });
}

function state() {
  return { memory: { items: [], total: 0, page: 1, pageSize: 20, selectedIds: new Set() } };
}

function rowElement(html) {
  return { style: {}, set innerHTML(value) { html.value = value; }, get innerHTML() { return html.value; } };
}

function renderRows(t, items) {
  env(t);
  const html = { value: "" };
  const tbody = rowElement(html);
  const scroll = { scrollTop: 0, clientHeight: 400, addEventListener() {} };
  globalThis.document = { getElementById: id => id === "memories-body" ? tbody : id === "memories-scroll" ? scroll : null };
  const memoryState = state();
  memoryState.memory.items = items;
  const page = new MemoryPage(memoryState, {}, {});
  page.updateSelectionControls = () => {};
  page.renderVirtual();
  return html.value;
}

function peekElement() {
  const elements = new Map();
  return id => {
    if (!elements.has(id)) {
      elements.set(id, {
        innerHTML: "", textContent: "", value: "", scrollTop: 0,
        addEventListener() {}, removeEventListener() {},
        setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
      });
    }
    return elements.get(id);
  };
}

test("each row states which writer produced the memory", t => {
  const html = renderRows(t, [
    { memory_id: 1, summary: "agent", importance: 5, status: "active", updated_at: "--", origin: "agent_memorize_tool", summary_quality: "normal" },
    { memory_id: 2, summary: "import", importance: 5, status: "active", updated_at: "--", origin: "memory_import", summary_quality: "normal" },
    { memory_id: 3, summary: "auto", importance: 5, status: "active", updated_at: "--", origin: "", summary_quality: "normal" },
  ]);
  assert.match(html, /origin\.agent/);
  assert.match(html, /origin\.import/);
  assert.match(html, /origin\.auto/);
  assert.doesNotMatch(html, /origin-tag is-low/);
});

test("a low-quality summary is marked on the row", t => {
  const html = renderRows(t, [
    { memory_id: 1, summary: "vague", importance: 5, status: "active", updated_at: "--", origin: "", summary_quality: "low" },
  ]);
  assert.match(html, /origin-tag is-low/);
  assert.match(html, /title="detail\.qualityLow"/);
});

test("batch delete reports the ids the backend failed on", async t => {
  env(t);
  globalThis.document = { getElementById: peekElement() };
  const memoryState = state();
  memoryState.memory.selectedIds = new Set([4, 5]);
  const reported = [];
  const page = new MemoryPage(memoryState, {
    post: async () => ({ deleted_count: 1, failed_count: 1, failed_ids: [5] }),
  }, { open() {}, close() {}, showConfirmDialog: async () => true });
  page.updateFeedback = () => {};
  page.updateSelectionControls = () => {};
  page.fetch = async () => {};
  page.showToast = message => reported.push(message);

  await page.deleteSelected();

  assert.equal(reported.length, 1);
  assert.match(reported[0], / 5$/);
  assert.doesNotMatch(reported[0], /--$/);
});

function detail(overrides) {
  return {
    memory_id: 7, summary: "summary", text: "summary", memory_type: "FACT", status: "active",
    importance: 0.5, importance_scale: "display", created_at: "--", updated_at: "--",
    session_id: "sess", persona_id: "persona", topics: [], key_facts: [], update_history: [],
    source_messages: [], metadata: {}, graph_context: null,
    ...overrides,
  };
}

test("the detail view shows origin, why it was remembered and the previous id", t => {
  env(t);
  globalThis.document = { getElementById: peekElement() };
  const panel = new PeekPanel({ isEditing: false }, {});
  panel.renderDetailView(detail({
    metadata: { memory_origin: "agent_memorize_tool", memorize_reason: "用户明确要求记住", previous_id: 3 },
  }));

  const html = globalThis.document.getElementById("peek-body").innerHTML;
  assert.match(html, /detail\.source/);
  assert.match(html, /origin\.agent/);
  assert.match(html, /detail\.memorizeReason/);
  assert.match(html, /用户明确要求记住/);
  assert.match(html, /detail\.previousId/);
});

test("the detail metadata grid does not repeat status, type and importance", t => {
  env(t);
  globalThis.document = { getElementById: peekElement() };
  const panel = new PeekPanel({ isEditing: false }, {});
  panel.renderDetailView(detail({}));

  const html = globalThis.document.getElementById("peek-body").innerHTML;
  assert.equal((html.match(/detail\.importance/g) || []).length, 1);
  assert.doesNotMatch(html, /detail\.status/);
  assert.doesNotMatch(html, /detail\.type/);
});

test("source messages are collapsed and mark the bot turns", t => {
  env(t);
  globalThis.document = { getElementById: peekElement() };
  const panel = new PeekPanel({ isEditing: false }, {});
  panel.renderDetailView(detail({
    source_messages: [
      { sender_name: "Alice", content: "hello", role: "user", timestamp: 1700000000 },
      { sender_name: "Bot", content: "hi there", role: "assistant", timestamp: 1700000001 },
    ],
  }));

  const html = globalThis.document.getElementById("peek-body").innerHTML;
  assert.match(html, /<details class="peek-details">/);
  assert.equal((html.match(/detail\.botMessage/g) || []).length, 1);
});
