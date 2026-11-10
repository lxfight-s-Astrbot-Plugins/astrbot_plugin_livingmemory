import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { unwrapResponse } from "../../pages/dashboard/modules/utils.js";

const here = dirname(fileURLToPath(import.meta.url));
const uiSource = readFileSync(join(here, "../../pages/dashboard/graph-ui.js"), "utf-8");
const sharedSource = readFileSync(join(here, "../../pages/dashboard/graph-shared.js"), "utf-8");

/* 轻量 DOM 桩：仅覆盖 graph-ui.js init 用到的元素与事件监听。 */
class FakeElement {
  constructor() {
    this._listeners = {};
    this._attrs = {};
    this.children = [];
    this.parentElement = null;
    this.value = "";
    this.textContent = "";
    this.style = {};
    this.disabled = false;
    this.innerHTML = "";
  }
  addEventListener(type, cb) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(cb);
  }
  trigger(type) {
    (this._listeners[type] || []).forEach((cb) =>
      cb({ preventDefault() {}, stopImmediatePropagation() {}, target: this })
    );
  }
  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const at = this.children.indexOf(child);
    if (at !== -1) this.children.splice(at, 1);
    child.parentElement = null;
    return child;
  }
  remove() {
    if (this.parentElement) this.parentElement.removeChild(this);
  }
  focus() {
    global.document.activeElement = this;
  }
  setAttribute(name, value) {
    this._attrs[name] = value;
  }
  getAttribute(name) {
    return this._attrs[name];
  }
  querySelector() {
    if (!this._span) this._span = new FakeElement();
    return this._span;
  }
}

function loadGraphUi(withGraph2D) {
  const calls = [];
  const posts = [];
  const elements = {};
  const docListeners = {};
  const g2dCalls = [];
  const g2dLifecycle = { init: 0, destroy: 0, setActive: [] };
  let graphHandlers;
  const viewCalls = [];
  let lastHandler = null;

  global.window = {
    t: (key, ...args) => [key, ...args].join(" "),
    AstrBotPluginPage: {
      async apiPost(path, body) {
        posts.push({ path, body: body || {} });
        return {
          status: "ok",
          data: { enabled: true, mode: "focus", summary: {}, snapshot: { nodes: [], edges: [] } },
        };
      },
      async apiGet(path, params) {
        calls.push({ path, params: params || {} });
        return {
          status: "ok",
          data: {
            enabled: true,
            mode: "overview",
            summary: {},
            snapshot: { nodes: [], edges: [] },
          },
        };
      },
    },
    addEventListener() {},
  };
  /* graph.min.js 先加载 graph-shared；app.min.js 提供共享的响应解包。 */
  global.window.lmUnwrapResponse = unwrapResponse;
  (0, eval)(sharedSource);
  if (withGraph2D) {
    global.window.Graph2D = {
      init(container, handlers) {
        g2dLifecycle.init += 1;
        graphHandlers = handlers;
      },
      destroy() { g2dLifecycle.destroy += 1; },
      setActive(active) { g2dLifecycle.setActive.push(active); },
      loadData(payload, options) { g2dCalls.push({ payload, options }); },
      selection: null,
      renderer: { _selection: null },
      resetView() { viewCalls.push("reset"); },
      selectNode(id) { this.selection = { type: "node", id }; },
    };
  }
  global.document = {
    body: new FakeElement(),
    activeElement: null,
    getElementById(id) {
      if (!elements[id]) elements[id] = new FakeElement();
      return elements[id];
    },
    addEventListener(type, cb) {
      if (type === "DOMContentLoaded") lastHandler = cb;
      if (!docListeners[type]) docListeners[type] = [];
      docListeners[type].push(cb);
    },
    removeEventListener(type, cb) {
      docListeners[type] = (docListeners[type] || []).filter((item) => item !== cb);
    },
    trigger(type, event) {
      (docListeners[type] || []).forEach((cb) => cb(event));
    },
    documentElement: { dataset: {}, getAttribute: () => "light" },
    createElement: () => new FakeElement(),
  };

  (0, eval)(uiSource);

  /* 手动触发 DOMContentLoaded，让 graph-ui init() 挂载事件监听。 */
  if (lastHandler) lastHandler();
  return { calls, posts, elements, g2dCalls, g2dLifecycle, viewCalls, graphHandlers };
}

async function flushMicrotasks() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

/* 测试辅助：按 id 在 Fake DOM 中查找动态创建的元素。 */
function findById(root, id) {
  if (!root) return null;
  if (root.id === id) return root;
  for (const child of root.children || []) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

function confirmLayer() {
  return findById(global.document.body, "graph-full-confirm");
}

function openFullConfirm(elements) {
  elements["graph-overview-btn"].trigger("click");
  return confirmLayer();
}

for (const association of ["entries", "memories"]) {
  test(`node details retain memories linked through ${association} without edges`, async () => {
    const { graphHandlers } = loadGraphUi(true);
    const memory = { memory_id: 4, summary: "A standalone fact" };
    const snapshot = {
      nodes: [{ id: 7, type: "fact", label: "Standalone", weight: 1 }],
      edges: [],
      memories: [memory],
    };
    if (association === "entries") snapshot.entries = [{ memory_id: 4, node_ids: [7] }];
    else memory.node_ids = [7];
    window.AstrBotPluginPage.apiGet = async () => ({
      status: "ok", data: { enabled: true, mode: "overview", summary: {}, snapshot },
    });
    let selected;
    window.lmOpenPeekNode = node => { selected = node; };
    window.ensureGraphScene();
    await flushMicrotasks();
    graphHandlers.onNodeClick(7);
    assert.deepEqual(selected.memories, [memory]);
  });
}

test("ensureGraphScene loads the constrained overview without full_graph", async () => {
  const { calls } = loadGraphUi();
  window.ensureGraphScene();
  await flushMicrotasks();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "page/graph/overview");
  assert.equal(calls[0].params.full_graph, undefined, "默认概览不应传 full_graph");
});

test("resetting the view does not request graph data", () => {
  const { calls, elements, viewCalls } = loadGraphUi(true);
  elements["graph-reset-view"].trigger("click");
  assert.deepEqual(viewCalls, ["reset"]);
  assert.equal(calls.length, 0);
});

test("overview button asks for confirmation, then toggles full graph and limited view", async () => {
  const { calls, elements } = loadGraphUi();
  const btn = elements["graph-overview-btn"];

  const overlay = openFullConfirm(elements);
  assert.ok(overlay, "点击全量图谱应出现确认弹层");
  const dialog = overlay.children[0];
  assert.equal(dialog.getAttribute("role"), "dialog");
  assert.equal(dialog.getAttribute("aria-modal"), "true");
  assert.equal(dialog.children[0].textContent, "graph.fullGraphConfirm", "确认层展示本地化说明");

  findById(overlay, "graph-full-confirm-ok").trigger("click");
  await flushMicrotasks();
  assert.equal(confirmLayer(), null, "确认后应关闭弹层");
  assert.equal(calls[0].params.full_graph, "true", "第一次点击应显式请求全量图");
  assert.equal(btn.getAttribute("data-i18n-aria"), "graph.limitedBtn", "全量模式下按钮应显示「受限概览」");

  btn.trigger("click");
  await flushMicrotasks();
  assert.equal(confirmLayer(), null, "受限概览不应再次弹确认");
  assert.equal(calls.length, 2);
  assert.equal(calls[1].params.full_graph, undefined, "再点一次应切回受限概览（不传 full_graph）");
  assert.equal(btn.getAttribute("data-i18n-aria"), "graph.overviewBtn", "受限模式下按钮应显示「全量图谱」");
});

test("cancelling the confirmation skips the request and restores focus", async () => {
  const { calls, elements } = loadGraphUi();
  const btn = elements["graph-overview-btn"];
  global.document.activeElement = btn;

  const overlay = openFullConfirm(elements);
  assert.ok(overlay);
  findById(overlay, "graph-full-confirm-cancel").trigger("click");
  await flushMicrotasks();

  assert.equal(calls.length, 0, "取消后不应发起请求");
  assert.equal(confirmLayer(), null, "取消后应移除弹层");
  assert.equal(btn.disabled, false, "取消后按钮应保持可用");
  assert.equal(global.document.activeElement, btn, "关闭后应把焦点还给触发按钮");
});

test("clicking the overlay or pressing Escape cancels the confirmation", async () => {
  const { calls, elements } = loadGraphUi();

  const first = openFullConfirm(elements);
  assert.ok(first);
  first.trigger("click");
  await flushMicrotasks();
  assert.equal(calls.length, 0, "点遮罩应取消");
  assert.equal(confirmLayer(), null);

  const second = openFullConfirm(elements);
  assert.ok(second);
  global.document.trigger("keydown", {
    key: "Escape",
    preventDefault() {},
    stopImmediatePropagation() {},
  });
  await flushMicrotasks();
  assert.equal(calls.length, 0, "Escape 应取消");
  assert.equal(confirmLayer(), null);
});

test("opening the confirmation twice reuses the single layer", async () => {
  const { calls, elements } = loadGraphUi();
  const first = openFullConfirm(elements);
  const second = openFullConfirm(elements);
  assert.equal(first, second, "重复点击复用同一弹层");
  assert.equal(global.document.body.children.length, 1, "DOM 中只应有一个弹层");

  findById(first, "graph-full-confirm-ok").trigger("click");
  await flushMicrotasks();
  assert.equal(calls.length, 1, "只发出一次全量图请求");
});

test("full graph loading shows elapsed time and cancellation clears it", async () => {
  const { elements } = loadGraphUi(true);
  const stateEl = elements["graph-canvas-state"];
  const realNow = Date.now;
  let finish;
  let requested = null;
  window.lmShowToast = (message) => {
    stateEl.dataset = stateEl.dataset || {};
    stateEl.dataset.toast = message;
  };
  window.AstrBotPluginPage.apiGet = (path, params) => {
    requested = { path, params };
    return new Promise((resolve) => {
      finish = resolve;
    });
  };

  try {
    const overlay = openFullConfirm(elements);
    findById(overlay, "graph-full-confirm-ok").trigger("click");
    await flushMicrotasks();

    assert.equal(requested.path, "page/graph/overview");
    assert.equal(requested.params.full_graph, "true");
    assert.equal(stateEl.textContent, "graph.loadingFullGraph 0", "加载初期显示 0 秒进度");
    assert.equal(elements["graph-overview-btn"].disabled, true, "加载期间按钮禁用");

    /* 模拟已过去 3 秒：500ms 定时器刷新耗时文案 */
    Date.now = () => realNow() + 3000;
    await new Promise((resolve) => setTimeout(resolve, 620));
    assert.equal(stateEl.textContent, "graph.loadingFullGraph 3", "进度文案应持续刷新已耗时");
    Date.now = realNow;

    assert.equal(window.lmCancelGraphLoad(), true, "在途全量请求应可取消");
    assert.equal(window.lmCancelGraphLoad(), false, "没有在途请求时取消返回 false");
    assert.equal(stateEl.textContent, "", "取消后清掉加载文案");
    assert.equal(elements["graph-overview-btn"].disabled, false, "取消后恢复按钮");
    assert.equal(stateEl.dataset.toast, "graph.cancelled", "取消应给出提示");

    /* 迟到的响应不得渲染，也不得改写按钮/画布状态 */
    finish({
      status: "ok",
      data: {
        enabled: true,
        mode: "overview",
        summary: {},
        snapshot: { nodes: [{ id: 1, type: "fact", label: "迟到的节点", weight: 1 }], edges: [] },
      },
    });
    await flushMicrotasks();
    assert.equal(stateEl.textContent, "", "取消后的响应应被丢弃");
    assert.equal(elements["graph-overview-btn"].getAttribute("data-i18n-aria"), "graph.overviewBtn");
  } finally {
    Date.now = realNow;
  }
});

test("cancelling restores the message shown before the full load", async () => {
  const { elements } = loadGraphUi(true);
  const stateEl = elements["graph-canvas-state"];

  /* 先加载空快照，画布停在「无可视化数据」提示 */
  window.ensureGraphScene();
  await flushMicrotasks();
  assert.equal(stateEl.textContent, "graph.canvasEmpty");

  let finish;
  window.AstrBotPluginPage.apiGet = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const overlay = openFullConfirm(elements);
  findById(overlay, "graph-full-confirm-ok").trigger("click");
  await flushMicrotasks();
  assert.equal(stateEl.textContent, "graph.loadingFullGraph 0");

  assert.equal(window.lmCancelGraphLoad(), true);
  assert.equal(stateEl.textContent, "graph.canvasEmpty", "取消后恢复加载前的画布提示");

  finish({ status: "ok", data: { enabled: true, mode: "overview", summary: {}, snapshot: { nodes: [], edges: [] } } });
  await flushMicrotasks();
  assert.equal(stateEl.textContent, "graph.canvasEmpty", "迟到的响应不得覆盖取消后的状态");
});

test("limited overview never opens the confirmation layer", async () => {
  const { calls } = loadGraphUi();
  window.ensureGraphScene();
  await flushMicrotasks();

  assert.equal(confirmLayer(), null, "受限概览不需要确认");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].params.full_graph, undefined);
});

test("layout in progress shows localized loading message until layout done", async () => {
  const { elements, g2dCalls } = loadGraphUi(true);
  /* 注入一个有数据的 payload（直接走 renderPayload 路径：先加载受限概览） */
  const bridge = global.window.AstrBotPluginPage;
  bridge.apiGet = async (path, params) => ({
    status: "ok",
    data: {
      enabled: true,
      mode: "overview",
      summary: {},
      snapshot: {
        nodes: [{ id: 1, type: "fact", label: "测试事实", weight: 1 }],
        edges: [],
      },
    },
  });
  window.ensureGraphScene();
  await flushMicrotasks();

  const stateEl = elements["graph-canvas-state"];
  assert.equal(stateEl.textContent, "graph.layouting", "布局期间应显示本地化布局提示");
  assert.equal(stateEl.style.display, "block");
  assert.equal(g2dCalls.length, 1, "Graph2D.loadData 应被调用");
  assert.equal(typeof g2dCalls[0].options.onLayoutDone, "function", "应注册布局完成回调");

  /* 布局完成 → 回调清除提示 */
  g2dCalls[0].options.onLayoutDone();
  assert.equal(stateEl.textContent, "", "布局完成后应清除提示");
  assert.equal(stateEl.style.display, "none");
});

test("graph search keeps progress visible through layout and reports the result count", async () => {
  const { elements, g2dCalls } = loadGraphUi(true);
  const bridge = window.AstrBotPluginPage;
  const nodes = [{ id: 1, type: "topic", label: "Coffee" }];
  bridge.apiGet = async () => ({ status: "ok", data: { enabled: true, mode: "overview", snapshot: { nodes } } });
  window.ensureGraphScene();
  await flushMicrotasks();

  let complete;
  bridge.apiPost = () => new Promise(resolve => { complete = resolve; });
  elements["graph-query-input"].value = "coffee";
  elements["graph-search-btn"].trigger("click");
  const status = elements["graph-canvas-state"];
  assert.equal(status.getAttribute("data-state"), "loading");
  assert.equal(elements["graph-canvas"].getAttribute("aria-busy"), "true");
  g2dCalls[0].options.onLayoutDone();
  assert.equal(status.getAttribute("data-state"), "loading");

  complete({ status: "ok", data: { enabled: true, mode: "query", snapshot: { nodes, edges: [] } } });
  await flushMicrotasks();
  assert.equal(status.getAttribute("data-state"), "layout");
  assert.equal(elements["graph-query-result"].textContent, "graph.queryResult 1 0");
  g2dCalls[1].options.onLayoutDone();
  assert.equal(elements["graph-canvas"].getAttribute("aria-busy"), "false");
});

test("empty snapshot shows empty message instead of layout message", async () => {
  const { elements, g2dCalls } = loadGraphUi(true);
  window.ensureGraphScene();
  await flushMicrotasks();

  const stateEl = elements["graph-canvas-state"];
  assert.equal(stateEl.textContent, "graph.canvasEmpty", "空图应显示空白提示而非布局提示");
  /* 空图布局完成也不应清掉空提示 */
  g2dCalls[0].options.onLayoutDone();
  assert.equal(stateEl.textContent, "graph.canvasEmpty");
});

test("legend explains node colours without a truncation chip", async () => {
  const { elements } = loadGraphUi(true);
  window.AstrBotPluginPage.apiGet = async () => ({
    status: "ok",
    data: {
      enabled: true,
      mode: "overview",
      summary: {
        node_type_breakdown: { person: 3 },
        relation_breakdown: { co_occurs_with: 5 },
      },
      snapshot: { nodes: [{ id: 1, type: "person", label: "Alice", weight: 1 }], edges: [] },
    },
  });

  window.ensureGraphScene();
  await flushMicrotasks();

  const html = elements["graph-legend"].innerHTML;
  assert.match(html, /graph\.nodePerson/, "图例解释节点颜色");
  assert.doesNotMatch(html, /graph\.visibleNodes/, "全量图不再截断，图例不显示可见/总数");
  assert.doesNotMatch(html, /co_occurs_with/, "关系 chip 已移除：它无法对应画布颜色");
});

test("focusing a memory from another page switches pages and queries by id", async () => {
  const { posts } = loadGraphUi();
  const switched = [];
  window.lmSwitchPage = async name => { switched.push(name); };

  await window.lmFocusMemoryOnGraph("77");

  assert.deepEqual(switched, ["graph"], "应先切到图谱页再定位");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, "page/graph/query");
  assert.equal(posts[0].body.memory_id, 77, "记忆 ID 由调用方传入，不需要手输");
});

test("leaving the graph page destroys the 2D renderer and re-initializes on return", () => {
  const { g2dLifecycle } = loadGraphUi(true);
  assert.equal(g2dLifecycle.init, 1, "图谱页初始化时创建 2D renderer");

  window.lmSetGraphActive(false);
  assert.equal(g2dLifecycle.destroy, 1, "离开图谱页应销毁 2D renderer 与布局 Worker");

  window.lmSetGraphActive(true);
  assert.equal(g2dLifecycle.init, 2, "回到图谱页应按需重建 2D renderer");
});

test("rendering a graph payload does not re-fetch /stats", async () => {
  loadGraphUi(true);
  let statsCalls = 0;
  window.lmFetchGraphStats = () => {
    statsCalls += 1;
  };

  window.ensureGraphScene();
  await flushMicrotasks();

  assert.equal(statsCalls, 0, "图页统计只随页面打开刷新，不应在每次 render 后重拉");
});

test("2D canvas exposes an aria label and the node list mirrors the payload", async () => {
  const { elements } = loadGraphUi(true);
  window.AstrBotPluginPage.apiGet = async () => ({
    status: "ok",
    data: {
      enabled: true,
      mode: "overview",
      summary: {},
      snapshot: {
        nodes: [
          { id: 7, type: "fact", label: "Alice" },
          { id: 8, type: "topic", label: "Coffee" },
        ],
        edges: [],
      },
    },
  });

  window.ensureGraphScene();
  await flushMicrotasks();

  const canvas = elements["graph-view-2d"]._span;
  assert.equal(canvas.getAttribute("role"), "img");
  assert.equal(canvas.getAttribute("aria-label"), "graph.canvasAria 2 0");

  elements["graph-node-list-btn"].trigger("click");
  const listHtml = elements["graph-node-list"].innerHTML;
  assert.match(listHtml, /data-node-id="7"/);
  assert.match(listHtml, /Alice/);
  assert.match(listHtml, /Coffee/);
});
