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

function loadGraphUi() {
  const calls = [];
  const elements = {};
  const docListeners = {};
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
  return { calls, elements };
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
