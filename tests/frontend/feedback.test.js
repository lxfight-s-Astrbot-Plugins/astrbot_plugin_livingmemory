import assert from "node:assert/strict";
import test from "node:test";

import { MemoryPage } from "../../pages/dashboard/modules/memory-page.js";
import { PromptPage } from "../../pages/dashboard/modules/prompt-page.js";
import { SystemPage } from "../../pages/dashboard/modules/system-page.js";

function environment(t) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        innerHTML: "", textContent: "", value: "", disabled: false, hidden: false,
        classList: {
          add: (...names) => names.forEach(n => classes.add(n)),
          remove: (...names) => names.forEach(n => classes.delete(n)),
          contains: name => classes.has(name),
          toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)),
        },
        addEventListener() {}, removeEventListener() {},
        setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.innerHTML = ""; },
        querySelectorAll: () => [],
      });
    }
    return elements.get(id);
  }
  globalThis.document = { getElementById: element, querySelectorAll: () => [] };
  globalThis.window = { t: (key, ...args) => [key, ...args].join(" ") };
  t.after(() => { delete globalThis.document; delete globalThis.window; });
  return element;
}

test("a failed stats request reports inline with a retry instead of a toast", async t => {
  const el = environment(t);
  const page = new SystemPage({}, { get: async () => { throw new Error("offline"); } });

  await page.fetch();

  assert.equal(el("system-feedback").hidden, false);
  assert.equal(el("system-feedback-message").textContent, "offline");
  assert.equal(el("system-retry").hidden, false);
});

test("a successful stats request clears the feedback banner", async t => {
  const el = environment(t);
  el("system-feedback").hidden = false;
  const page = new SystemPage({}, {
    get: async path => (path === "stats"
      ? { total_memories: 1, status_breakdown: {}, recent_sessions: [] }
      : path === "backups" ? { backups: [] } : { config: { enabled: true } }),
  });

  await page.fetch();

  assert.equal(el("system-feedback").hidden, true);
  assert.equal(el("system-feedback-message").textContent, "");
});

test("the session panel counts memories, which is what the backend sends", t => {
  const el = environment(t);
  const page = new SystemPage({}, {});

  page.renderSessionList([{ session_id: "s1", message_count: 4 }]);

  const html = el("session-list").innerHTML;
  assert.match(html, /4 system\.memories/);
  assert.doesNotMatch(html, /last_active|system\.lastActive/, "后端不返回 last_active，不该渲染该行");
});

test("backups are ordered by time and count the files actually copied", t => {
  const el = environment(t);
  const page = new SystemPage({}, {});

  page.renderBackupList([
    { name: "v2.9", backup_unix_time: 1000, files_copied: 2, file_count: 3 },
    { name: "v2.10", backup_unix_time: 2000, files_copied: 5, file_count: 6 },
  ]);

  const html = el("backup-list").innerHTML;
  assert.ok(html.indexOf("v2.10") < html.indexOf("v2.9"), "新备份应排在前，目录名字典序会把 v2.9 排到 v2.10 前");
  assert.match(html, /5 system\.files/, "file_count 含 backup_info.json，应使用 files_copied");
});

test("running consolidation keeps the button disabled when the feature is off", async t => {
  const el = environment(t);
  const page = new SystemPage({}, {
    get: async () => ({ config: { enabled: false }, consolidated_count: 0, archived_count: 0 }),
    post: async () => ({ candidates: 0, groups: 0, merged: 0 }),
  });

  await page.fetchConsolidation();
  assert.equal(el("cons-run-btn").disabled, true, "未启用时控件应直接表明不可用");

  await page.runConsolidation();
  assert.equal(el("cons-run-result").textContent, "system.consNoCandidates");
  assert.equal(el("cons-run-btn").disabled, true, "运行结束后按配置恢复，而不是无条件解禁");
});

test("a failed prompt list request reports inline instead of only toasting", async t => {
  const el = environment(t);
  const page = new PromptPage({}, { get: async () => { throw new Error("offline"); } });

  await page.fetch();

  assert.equal(el("prompt-feedback").hidden, false);
  assert.equal(el("prompt-feedback-message").textContent, "offline");
  assert.equal(el("prompt-retry").hidden, false);
});

test("a failed memory list request keeps the inline banner and drops the toast", async t => {
  const el = environment(t);
  const state = { memory: { items: [], total: 0, page: 1, pageSize: 20, selectedIds: new Set() } };
  const page = new MemoryPage(state, { get: async () => { throw new Error("offline"); } }, {});
  page.updateFeedback = () => {};
  page.renderVirtual = () => {};
  page.updatePagination = () => {};
  let toasts = 0;
  page.showToast = () => { toasts += 1; };

  await page.fetch();

  assert.equal(toasts, 0, "同一个错误不应同时出现在 toast 和常驻条");
  assert.equal(state.memory.error, "offline");
});
