import assert from "node:assert/strict";
import test from "node:test";
import { MemoryPage } from "../../pages/dashboard/modules/memory-page.js";
import { PromptPage } from "../../pages/dashboard/modules/prompt-page.js";
import { PeekPanel } from "../../pages/dashboard/modules/peek-panel.js";
import { RecallPage } from "../../pages/dashboard/modules/recall-page.js";
import { confirmDiscardChanges } from "../../pages/dashboard/modules/utils.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function environment(t) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        value: "", disabled: false, hidden: false, innerHTML: "", events: {},
        classList: { add: (...names) => names.forEach(n => classes.add(n)), remove: (...names) => names.forEach(n => classes.delete(n)),
          contains: name => classes.has(name), toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name) },
        addEventListener(name, callback) { this.events[name] = callback; },
        removeEventListener(name) { delete this.events[name]; },
        setAttribute(name, value) { this[name] = value; }, removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.innerHTML = ""; }, focus() {}, scrollIntoView() {},
        cloneNode() { return this; }, parentNode: { replaceChild() {} },
        showModal() { this.open = true; },
        close(value) { this.returnValue = value; this.open = false; this.events.close?.(); },
      });
    }
    return elements.get(id);
  }
  globalThis.document = { getElementById: element, querySelectorAll: () => [element("edit-value")] };
  globalThis.window = { t: (key, ...args) => [key, ...args].join(" ") };
  t.after(() => { delete globalThis.document; delete globalThis.window; });
  return element;
}

function memoryState() {
  return { memory: { items: [], total: 0, page: 1, pageSize: 20, selectedIds: new Set(), status: "all", type: "all" } };
}

test("filter changes capture one complete snapshot and immediate apply cancels pending debounce", async t => {
  const el = environment(t);
  const state = memoryState();
  const page = new MemoryPage(state, {}, {});
  page.updateFeedback = () => {};
  const snapshots = [];
  page.fetch = async () => { snapshots.push({ ...state.memory }); };
  el("mem-keyword").value = "coffee";
  el("mem-session").value = "session-a";
  el("mem-page-size").value = "50";
  page.applyFilters({ defer: true });
  el("mem-status").value = "active";
  await page.applyFilters();
  await new Promise(resolve => setTimeout(resolve, 350));
  assert.equal(snapshots.length, 1);
  assert.equal(snapshots[0].keyword, "coffee");
  assert.equal(snapshots[0].session, "session-a");
  assert.equal(snapshots[0].status, "active");
  assert.equal(snapshots[0].pageSize, 50);
  await page.applyFilters({ reset: true });
  assert.equal(state.memory.keyword, "");
  assert.equal(state.memory.status, "all");
  assert.equal(state.memory.sort, "created_desc");
  assert.equal(state.memory.pageSize, 50);
});

test("a response for old filters cannot replace rows during the next debounce window", async t => {
  const el = environment(t);
  const old = deferred();
  const state = memoryState();
  state.memory.items = [{ memory_id: 9 }];
  const page = new MemoryPage(state, { get: () => old.promise }, {});
  page.updateFeedback = () => {};
  page.renderVirtual = () => {};
  page.updatePagination = () => {};
  const pending = page.fetch();
  el("mem-keyword").value = "new";
  page.applyFilters({ defer: true });
  old.resolve({ items: [{ id: 1 }], total: 1 });
  await pending;
  clearTimeout(page._filterTimer);
  assert.equal(state.memory.items[0].memory_id, 9);
  assert.equal(state.memory.loading, true);
});

test("import locks before file reading, previews once and never commits after cancellation", async t => {
  const el = environment(t);
  el("mem-import-duplicates").value = "skip";
  const reading = deferred();
  const posts = [];
  let reads = 0;
  const page = new MemoryPage(memoryState(), { post: async (path, payload) => {
    posts.push(payload); return { planned_import_count: 1 };
  } }, { open() {}, close() {}, showConfirmDialog: async () => false });
  page.updateFeedback = () => {};
  const file = { name: "memory.json", size: 2, text: () => { reads++; return reading.promise; } };
  const pending = page.importFile(file);
  await page.importFile(file);
  assert.equal(reads, 1);
  assert.equal(page._transferBusy, true);
  reading.resolve("[]");
  await pending;
  assert.equal(posts.length, 1);
  assert.equal(posts[0].dry_run, true);
  assert.equal(page._transferBusy, false);
  assert.match(el("transfer-feedback").textContent, /importCancelled/);
});

test("empty previews and file read errors never submit an import and leave controls usable", async t => {
  const el = environment(t);
  let posts = 0;
  const page = new MemoryPage(memoryState(), { post: async () => {
    posts++; return { planned_import_count: 0, duplicate_count: 1 };
  } }, { close() {}, open() { assert.fail("Empty previews must not ask to import"); } });
  page.updateFeedback = () => {};
  await page.importFile({ name: "memory.json", size: 2, text: async () => "[]" });
  assert.equal(posts, 1);
  assert.match(el("transfer-feedback").textContent, /nothingToImport/);
  await page.importFile({ name: "memory.json", size: 2, text: async () => { throw new Error("read failed"); } });
  assert.equal(posts, 1);
  assert.equal(page._transferBusy, false);
  assert.equal(el("transfer-feedback").textContent, "read failed");
});

test("discard cancellation retains a prompt draft, while explicit discard closes it", async t => {
  const el = environment(t);
  const page = new PromptPage({}, {});
  page.editingId = "a";
  page.editContent = "original";
  el("prompt-editor-textarea").value = "draft";
  let pending = page.closeEditor();
  el("discard-dialog").close("cancel");
  assert.equal(await pending, false);
  assert.equal(page.editingId, "a");
  assert.equal(el("prompt-editor-textarea").value, "draft");
  pending = page.closeEditor();
  el("discard-dialog").close("discard");
  assert.equal(await pending, true);
  assert.equal(page.editingId, null);
});

test("a new discard action after native dismissal waits for a fresh decision", async t => {
  const el = environment(t);
  const first = confirmDiscardChanges();
  const dialog = el("discard-dialog");
  dialog.open = false;
  dialog.returnValue = "cancel";
  const next = confirmDiscardChanges();
  dialog.events.close();
  assert.equal(await first, false);
  await Promise.resolve();
  assert.equal(dialog.open, true);
  dialog.close("discard");
  assert.equal(await next, true);
});

test("memory editor close preserves its draft on cancel and refuses to leave during save", async t => {
  const el = environment(t);
  const page = new PeekPanel({ isEditing: true }, {});
  page._editSnapshot = JSON.stringify(["original"]);
  el("edit-value").value = "draft";
  let closed = 0;
  page.close = () => { closed++; };
  const pending = page.requestClose();
  el("discard-dialog").close("cancel");
  assert.equal(await pending, false);
  assert.equal(closed, 0);
  page._saving = true;
  assert.equal(await page.requestClose(), false);
  assert.equal(closed, 0);
});

test("out-of-order prompt detail responses cannot overwrite the latest editor", async t => {
  const el = environment(t);
  const slow = deferred(), fast = deferred();
  const page = new PromptPage({}, { get: (_path, params) => params.id === "a" ? slow.promise : fast.promise });
  page.prompts = [{ id: "a" }, { id: "b" }];
  const first = page.openEditor("a");
  await Promise.resolve();
  const second = page.openEditor("b");
  fast.resolve({ content: "LATEST" });
  await second;
  slow.resolve({ content: "OLD" });
  await first;
  assert.equal(page.editingId, "b");
  assert.equal(el("prompt-editor-textarea").value, "LATEST");
  assert.equal(el("prompt-editor").inert, false);
});

test("prompt saves keep the original id and content, reject duplicate submits and block leaving", async t => {
  const el = environment(t);
  const saving = deferred();
  const posts = [];
  const page = new PromptPage({}, { post: async (path, body) => { posts.push({ path, body }); return saving.promise; } });
  page.editingId = "a"; page.editContent = "original";
  el("prompt-editor-textarea").value = "draft";
  page.fetch = async () => {};
  const pending = page.savePrompt();
  await page.savePrompt();
  assert.equal(await page.canLeave(), false);
  assert.equal(el("prompt-editor").inert, true);
  saving.resolve({});
  await pending;
  assert.deepEqual(posts, [{ path: "prompts/update", body: { id: "a", content: "draft" } }]);
  assert.equal(page.editingId, null);
  assert.equal(el("prompt-editor").inert, false);
});

test("loading defaults changes the draft only until Save is explicitly invoked", async t => {
  const el = environment(t);
  const posts = [];
  const page = new PromptPage({}, { get: async () => ({ content: "default" }), post: async (path, body) => { posts.push({ path, body }); } });
  page.editingId = "a"; page.editContent = "original";
  el("prompt-editor-textarea").value = "original";
  page.fetch = async () => {};
  await page.resetPrompt();
  assert.equal(posts.length, 0);
  assert.equal(el("prompt-editor-textarea").value, "default");
  await page.savePrompt();
  assert.deepEqual(posts, [{ path: "prompts/reset", body: { id: "a" } }]);
});

test("starting a new recall clears old results and failure restores input without stale cards", async t => {
  const el = environment(t);
  const pending = deferred();
  const state = { _recallCache: { data: "old" } };
  const page = new RecallPage(state, { post: () => pending.promise }, {});
  el("recall-query").value = "new query";
  el("recall-results").innerHTML = "old results";
  const running = page.runRecall();
  assert.equal(state._recallCache, null);
  assert.equal(el("recall-results").innerHTML, "");
  assert.equal(el("recall-query").readOnly, true);
  pending.reject(new Error("offline"));
  await running;
  assert.equal(el("recall-results").innerHTML, "");
  assert.equal(el("recall-query").readOnly, false);
  assert.equal(el("recall-search-btn").disabled, false);
  assert.equal(el("recall-feedback").textContent, "offline");
});

test("switching memory or node details preserves a dirty draft when discard is cancelled", async t => {
  const el = environment(t);
  const original = { memory_id: 1 };
  const state = { selectedMemory: original, isEditing: true, _detailCache: original };
  let requests = 0;
  const panel = new PeekPanel(state, { get: async () => { requests++; return {}; } });
  panel._editSnapshot = JSON.stringify(["original"]);
  el("edit-value").value = "draft";
  panel.renderDetailView = () => { throw new Error("Draft must not be replaced"); };
  let opening = panel.renderMemory({ memory_id: 2 });
  assert.equal(el("discard-dialog").open, true);
  el("discard-dialog").close("cancel"); await opening;
  assert.equal(state.selectedMemory, original);
  assert.equal(state._detailCache, original);
  assert.equal(state.isEditing, true);
  opening = panel.renderNode({ id: 3 });
  el("discard-dialog").close("cancel"); await opening;
  assert.equal(state._detailCache, original);
  assert.equal(el("edit-value").value, "draft");
  assert.equal(requests, 0);
});

test("only the latest detail click proceeds after a shared discard decision", async t => {
  const el = environment(t);
  const state = { selectedMemory: { memory_id: 1 }, isEditing: true };
  const requests = [], rendered = [];
  const panel = new PeekPanel(state, { get: async (_path, params) => {
    requests.push(params.memory_id); return { memory_id: params.memory_id, importance: 0.5 };
  } });
  panel._editSnapshot = JSON.stringify(["original"]);
  el("edit-value").value = "draft";
  panel.renderDetailView = detail => { rendered.push(detail.memory_id); };
  panel.open = () => {};
  const first = panel.renderMemory({ memory_id: 2 });
  const second = panel.renderMemory({ memory_id: 3 });
  el("discard-dialog").close("discard");
  await Promise.all([first, second]);
  assert.deepEqual(requests, [3]);
  assert.deepEqual(rendered, [3]);
});

test("node selection invalidates pending memory details instead of reopening the old memory", async t => {
  environment(t);
  const request = deferred();
  const state = {};
  const panel = new PeekPanel(state, { get: () => request.promise });
  panel.open = () => {};
  let memoriesRendered = 0;
  panel.renderDetailView = () => { memoriesRendered++; };
  const opening = panel.renderMemory({ memory_id: 1 });
  await panel.renderNode({ id: 2, label: "node", type: "fact" });
  request.resolve({ memory_id: 1, importance: 0.5 });
  await opening;
  assert.equal(memoriesRendered, 0);
  assert.equal(state._nodeDetailCache.id, 2);
  assert.equal(state.selectedMemory, null);
});

test("shared API details retain raw importance across repeated opens", async t => {
  environment(t);
  const detail = { memory_id: 1, importance: 0.1 };
  const state = {};
  const panel = new PeekPanel(state, { get: async () => detail });
  const values = [];
  panel.renderDetailView = item => values.push(item.importance);
  panel.open = () => {};
  await panel.renderMemory({ memory_id: 1 });
  await panel.renderMemory({ memory_id: 1 });
  assert.equal(detail.importance, 0.1);
  assert.deepEqual(values, [1, 1]);
});

test("low importance stays on the same scale through detail, edit and unchanged save", async t => {
  const el = environment(t);
  const state = {};
  const posts = [];
  const panel = new PeekPanel(state, {
    get: async () => ({ memory_id: 1, summary: "original", importance: 0.1, status: "active", memory_type: "GENERAL", topics: [], key_facts: [] }),
    post: async (_path, body) => { posts.push(body); },
  });
  await panel.renderMemory({ memory_id: 1 });
  assert.match(el("peek-body").innerHTML, /detail.importance: 1\.0\/10/);
  const detail = state._detailCache;
  panel.renderEditView(detail);
  assert.match(el("peek-body").innerHTML, /id="edit-importance"[^>]*value="1\.0"/);
  el("edit-content-area").value = "original";
  el("edit-topics-area").value = "";
  el("edit-key-facts-area").value = "";
  el("edit-status").value = "active";
  el("edit-type").value = "GENERAL";
  el("edit-importance").value = "1.0";
  await panel.saveEdit(detail);
  assert.deepEqual(posts, [], "An unchanged low-importance memory must not be rewritten");
});

test("offline list details preserve an explicitly scaled importance of one", async t => {
  const el = environment(t);
  const panel = new PeekPanel({}, { get: async () => { throw new Error("offline"); } });
  await panel.renderMemory({ memory_id: 1, summary: "fallback", importance: 1, importance_scale: "display" });
  assert.match(el("peek-body").innerHTML, /detail.importance: 1\.0\/10/);
});

test("failed detail fetch retains a usable fallback cache and closing invalidates late replies", async t => {
  environment(t);
  const state = {};
  const panel = new PeekPanel(state, { get: async () => { throw new Error("offline"); } });
  panel.renderDetailView = () => {};
  panel.open = () => {};
  await panel.renderMemory({ memory_id: 1, summary: "fallback" });
  assert.equal(state._detailCache.summary, "fallback");
  const request = deferred();
  panel.api.get = () => request.promise;
  let opens = 0;
  panel.open = () => { opens++; };
  const opening = panel.renderMemory({ memory_id: 2 });
  panel.close();
  request.resolve({ memory_id: 2 });
  await opening;
  assert.equal(opens, 0);
  assert.equal(state._detailCache, null);
});
