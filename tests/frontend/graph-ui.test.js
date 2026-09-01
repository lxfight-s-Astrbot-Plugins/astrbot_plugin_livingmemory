import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const uiSource = readFileSync(join(here, "../../pages/dashboard/graph-ui.js"), "utf-8");

/* 轻量 DOM 桩：仅覆盖 graph-ui.js init 用到的元素与事件监听。 */
class FakeElement {
  constructor() {
    this._listeners = {};
    this._attrs = {};
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
    (this._listeners[type] || []).forEach((cb) => cb({ preventDefault() {} }));
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
  const elements = {};
  const docListeners = {};
  const g2dCalls = [];
  let lastHandler = null;

  global.window = {
    t: (key) => key,
    AstrBotPluginPage: {
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
  if (withGraph2D) {
    global.window.Graph2D = {
      init() {},
      loadData(payload, options) { g2dCalls.push({ payload, options }); },
      selection: null,
      renderer: { _selection: null },
    };
  }
  global.document = {
    getElementById(id) {
      if (!elements[id]) elements[id] = new FakeElement();
      return elements[id];
    },
    addEventListener(type, cb) {
      if (type === "DOMContentLoaded") lastHandler = cb;
      if (!docListeners[type]) docListeners[type] = [];
      docListeners[type].push(cb);
    },
    documentElement: { getAttribute: () => "light" },
    createElement: () => new FakeElement(),
  };

  (0, eval)(uiSource);

  /* 手动触发 DOMContentLoaded，让 graph-ui init() 挂载事件监听。 */
  if (lastHandler) lastHandler();
  return { calls, elements, g2dCalls };
}

async function flushMicrotasks() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

test("ensureGraphScene loads the constrained overview without full_graph", async () => {
  const { calls } = loadGraphUi();
  window.ensureGraphScene();
  await flushMicrotasks();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "page/graph/overview");
  assert.equal(calls[0].params.full_graph, undefined, "默认概览不应传 full_graph");
});

test("overview button toggles between full graph and limited view", async () => {
  const { calls, elements } = loadGraphUi();
  const btn = elements["graph-overview-btn"];

  btn.trigger("click");
  await flushMicrotasks();
  assert.equal(calls[0].params.full_graph, "true", "第一次点击应显式请求全量图");
  assert.equal(btn.getAttribute("data-i18n-aria"), "graph.limitedBtn", "全量模式下按钮应显示「受限概览」");

  btn.trigger("click");
  await flushMicrotasks();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].params.full_graph, undefined, "再点一次应切回受限概览（不传 full_graph）");
  assert.equal(btn.getAttribute("data-i18n-aria"), "graph.overviewBtn", "受限模式下按钮应显示「全量图谱」");
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
