import assert from "node:assert/strict";
import test from "node:test";

import { MemoryPage } from "../../pages/dashboard/modules/memory-page.js";


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createState() {
  return {
    memory: {
      items: [],
      total: 0,
      page: 1,
      pageSize: 20,
      hasMore: false,
      keyword: "",
      session: "",
      status: "all",
      type: "all",
      sort: "created_desc",
      selectedIds: new Set(),
    },
  };
}

function apiMemory(id, summary) {
  return {
    id,
    text: summary,
    metadata: {
      persona_summary: summary,
      memory_type: "GENERAL",
      importance: 0.5,
      status: "active",
    },
  };
}

test("failed refresh preserves rows and exposes an error until a successful retry", async () => {
  const state = createState();
  state.memory.items = [{ memory_id: 9, summary: "Existing memory" }];
  const page = new MemoryPage(state, { get: async () => { throw new Error("offline"); } }, {});
  page.updateFeedback = () => {};
  page.showToast = () => {};
  page.renderVirtual = () => {};
  page.updatePagination = () => {};
  await page.fetch();
  assert.equal(state.memory.items[0].memory_id, 9);
  assert.equal(state.memory.error, "offline");
  assert.equal(state.memory.loading, false);
  page.api.get = async () => ({ items: [apiMemory(10, "Recovered")], total: 1 });
  await page.fetch();
  assert.equal(state.memory.error, "");
  assert.equal(state.memory.items[0].memory_id, 10);
});

test("scroll bursts schedule one render and unchanged windows preserve the DOM", () => {
  const state = createState();
  state.memory.items = [{ memory_id: 1, summary: "coffee", importance: 5, status: "active" }];
  let html = "";
  let writes = 0;
  let callback;
  let frames = 0;
  const tbody = { style: {}, set innerHTML(value) { html = value; writes++; }, get innerHTML() { return html; } };
  const scroll = { scrollTop: 0, clientHeight: 400, addEventListener(_name, listener) { this.listener = listener; } };
  globalThis.window = { t: key => key, requestAnimationFrame(fn) { frames++; callback = fn; } };
  globalThis.document = { getElementById: id => id === "memories-body" ? tbody : id === "memories-scroll" ? scroll : null };
  try {
    const page = new MemoryPage(state, {}, {});
    page.updateSelectionControls = () => {};
    page.renderVirtual();
    const initialWrites = writes;
    scroll.listener();
    scroll.listener();
    scroll.listener();
    assert.equal(frames, 1);
    callback();
    assert.equal(writes, initialWrites);
    assert.match(html, /class="memory-open/);
    state.memory.items = Array.from({ length: 100 }, (_, i) => ({ memory_id: i + 1, summary: "coffee", importance: 5, status: "active" }));
    window.matchMedia = () => ({ matches: true });
    page.renderVirtual();
    assert.match(html, /colspan="4"/);
    assert.doesNotMatch(html, /colspan="7"/);
  } finally {
    delete globalThis.window;
    delete globalThis.document;
  }
});

test("the latest memory fetch wins when responses arrive out of order", async () => {
  const slow = deferred();
  const fast = deferred();
  const state = createState();
  const api = {
    get(_path, params) {
      return params.keyword === "slow" ? slow.promise : fast.promise;
    },
  };
  const page = new MemoryPage(state, api, {});
  page.renderVirtual = () => {};
  page.updatePagination = () => {};

  state.memory.keyword = "slow";
  const slowFetch = page.fetch();
  state.memory.keyword = "fast";
  const fastFetch = page.fetch();

  fast.resolve({ items: [apiMemory(2, "FAST RESULT")], total: 1, has_more: false });
  await fastFetch;
  slow.resolve({ items: [apiMemory(1, "SLOW RESULT")], total: 1, has_more: false });
  await slowFetch;

  assert.equal(state.memory.items.length, 1);
  assert.equal(state.memory.items[0].memory_id, 2);
  assert.equal(state.memory.items[0].summary, "FAST RESULT");
});

test("the bound scroll listener uses the current item count", () => {
  const state = createState();
  const tbody = { innerHTML: "", style: {} };
  const scrollEl = {
    scrollTop: 0,
    clientHeight: 460,
    listener: null,
    addEventListener(_type, listener) {
      this.listener = listener;
    },
  };
  globalThis.window = {
    requestAnimationFrame(callback) {
      callback();
    },
    t(key, ...args) {
      return [key, ...args].join(" ");
    },
  };
  globalThis.document = {
    createElement() {
      let text = "";
      return {
        set textContent(value) {
          text = String(value);
        },
        get innerHTML() {
          return text;
        },
      };
    },
    getElementById(id) {
      if (id === "memories-body") return tbody;
      if (id === "memories-scroll") return scrollEl;
      return null;
    },
  };

  try {
    state.memory.items = Array.from({ length: 20 }, (_, index) => ({
      memory_id: index + 1,
      summary: `Memory ${index + 1}`,
      memory_type: "GENERAL",
      importance: 5,
      status: "active",
      created_at: "--",
      updated_at: "--",
    }));
    const page = new MemoryPage(state, {}, {});
    page.updateSelectionControls = () => {};
    page.renderVirtual();
    assert.equal(typeof scrollEl.listener, "function");

    state.memory.items = Array.from({ length: 100 }, (_, index) => ({
      memory_id: index + 1,
      summary: `Memory ${index + 1}`,
      memory_type: "GENERAL",
      importance: 5,
      status: "active",
      created_at: "--",
      updated_at: "--",
    }));
    page.renderVirtual();
    scrollEl.scrollTop = 1095;
    scrollEl.listener();

    assert.match(tbody.innerHTML, /data-key="m:5"/);
    assert.match(tbody.innerHTML, /data-key="m:43"/);
    assert.doesNotMatch(tbody.innerHTML, /data-key="m:44"/);
    assert.match(tbody.innerHTML, /height:3192px/);
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("batchEdit posts the dialog result to memories/batch-update", async () => {
  const state = createState();
  state.memory.selectedIds = new Set([11, 12]);

  const posts = [];
  const api = {
    post(path, payload) {
      posts.push({ path, payload });
      return Promise.resolve({ updated_count: 2, failed_count: 0, total: 2 });
    },
  };
  const peek = {
    open() {},
    close() {},
    showBatchEditDialog(count) {
      assert.equal(count, 2);
      return Promise.resolve({ field: "importance", value: 7, value_scale: "display" });
    },
  };
  const page = new MemoryPage(state, api, peek);
  let fetched = false;
  page.fetch = async () => { fetched = true; };
  page.showToast = () => {};
  globalThis.window = {
    t(key, ...args) {
      return [key, ...args].join(" ");
    },
  };
  globalThis.document = {
    getElementById() {
      return { disabled: false };
    },
  };

  try {
    await page.batchEdit();
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }

  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, "memories/batch-update");
  assert.deepEqual(posts[0].payload, {
    memory_ids: [11, 12],
    field: "importance",
    value: 7,
    value_scale: "display",
  });
  assert.equal(state.memory.selectedIds.size, 0);
  assert.equal(fetched, true);
});

test("batchEdit aborts without posting when the dialog is cancelled", async () => {
  const state = createState();
  state.memory.selectedIds = new Set([11]);

  let posted = false;
  let closed = false;
  const api = {
    post() {
      posted = true;
      return Promise.resolve({});
    },
  };
  const peek = {
    open() {},
    close() { closed = true; },
    showBatchEditDialog() {
      return Promise.resolve(null);
    },
  };
  const page = new MemoryPage(state, api, peek);
  page.fetch = async () => {};

  await page.batchEdit();

  assert.equal(posted, false);
  assert.equal(closed, true);
  assert.equal(state.memory.selectedIds.size, 1);
});
