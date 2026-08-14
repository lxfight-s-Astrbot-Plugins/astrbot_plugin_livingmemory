import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "../../pages/dashboard/graph-2d.js"), "utf-8");
const coreSource = readFileSync(join(here, "../../pages/dashboard/graph-layout-core.js"), "utf-8");
const sharedSource = readFileSync(join(here, "../../pages/dashboard/graph-shared.js"), "utf-8");
const rendererSource = readFileSync(join(here, "../../pages/dashboard/graph-renderer.js"), "utf-8");
const interactionSource = readFileSync(join(here, "../../pages/dashboard/graph-interaction.js"), "utf-8");

function makeCtx() {
  const gradient = { addColorStop() {} };
  const ctx = {
    measureText: () => ({ width: 10 }),
    createLinearGradient: () => gradient,
    createRadialGradient: () => gradient,
  };
  return new Proxy(ctx, {
    get(target, prop) {
      if (prop in target) return target[prop];
      return () => {};
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  });
}

function makeCanvas() {
  const parent = {
    getBoundingClientRect: () => ({ width: 800, height: 600 }),
    clientWidth: 800,
    clientHeight: 600,
  };
  return {
    getContext: () => makeCtx(),
    parentElement: parent,
    addEventListener() {},
    style: {},
    width: 800,
    height: 600,
  };
}

function makeContainer() {
  return { innerHTML: "", appendChild() {} };
}

function loadGraph() {
  const rafQueue = [];
  global.window = {
    devicePixelRatio: 1,
    matchMedia: () => ({ matches: true }),
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    MutationObserver: class {
      observe() {}
      disconnect() {}
    },
    addEventListener() {},
  };
  global.document = {
    documentElement: { getAttribute: () => "light" },
    createElement: () => makeCanvas(),
    addEventListener() {},
  };
  global.getComputedStyle = () => ({ getPropertyValue: () => "" });
  global.requestAnimationFrame = (cb) => {
    rafQueue.push(cb);
    return rafQueue.length;
  };
  global.cancelAnimationFrame = () => {};
  class Observer {
    observe() {}
    disconnect() {}
  }
  global.ResizeObserver = Observer;
  global.MutationObserver = Observer;
  (0, eval)(sharedSource);
  global.GraphShared = global.window.GraphShared;
  (0, eval)(coreSource);
  (0, eval)(rendererSource);
  (0, eval)(interactionSource);
  global.GraphRenderer = global.window.GraphRenderer;
  global.GraphInteraction = global.window.GraphInteraction;
  (0, eval)(source);
  return rafQueue;
}

function flushRaf(rafQueue) {
  let guard = 0;
  while (rafQueue.length && guard < 200000) {
    const cb = rafQueue.shift();
    cb(0);
    guard++;
  }
}

/* 渐进式布局现在是 async：需要交替排空微任务与 rAF 队列。 */
async function settle(rafQueue) {
  let guard = 0;
  while (guard < 200000) {
    guard++;
    await Promise.resolve();
    if (!rafQueue.length) break;
    flushRaf(rafQueue);
  }
}

function makePayload(nodeCount, edgeCount) {
  const nodes = [];
  for (let i = 1; i <= nodeCount; i++) {
    nodes.push({ id: i, type: "topic", label: "N" + i, weight: 1 });
  }
  const edges = [];
  for (let e = 0; e < edgeCount && e < nodeCount - 1; e++) {
    edges.push({
      id: e + 1,
      source: e + 1,
      target: e + 2,
      relation_type: "relates",
      memory_id: 1,
      weight: 1,
      confidence: 0.8,
    });
  }
  return { enabled: true, mode: "query", snapshot: { nodes, edges } };
}

test("small graph layout completes synchronously with positions", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(50, 49);
  g.loadData(payload);

  assert.equal(g._nodes.length, 50);
  assert.equal(Object.keys(g.animator._layout.positions).length, 50);
  assert.equal(g.animator._layout._done, true);
  await settle(rafQueue);
  /* 空闲渲染后社区椭圆缓存应已填充。 */
  assert.ok(g.renderer._communityCacheKey != null);
  assert.ok(Array.isArray(g.renderer._communityCache));
});

test("label width cache populates when labels render", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const nodes = [];
  for (let i = 1; i <= 30; i++) {
    nodes.push({ id: i, type: "topic", label: "Prominent-Node-" + i, weight: 3, degree: 6 });
  }
  const edges = [];
  for (let e = 0; e < 29; e++) {
    edges.push({ id: e + 1, source: e + 1, target: e + 2, relation_type: "relates", memory_id: 1, weight: 1, confidence: 0.8 });
  }
  g.loadData({ enabled: true, mode: "query", snapshot: { nodes, edges } });
  await settle(rafQueue);

  assert.ok(Object.keys(g.renderer._labelWidthCache).length > 0, "标签宽度缓存应有条目");
});

test("large graph layout completes via progressive stepping", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(300, 299);
  g.loadData(payload);

  /* Layout completes via progressive rAF chunks. */
  await settle(rafQueue);
  assert.equal(g._nodes.length, 300);
  assert.equal(g.animator._layout._done, true);
  assert.equal(Object.keys(g.animator._layout.positions).length, 300);
});

test("identical graph structure reuses cached layout", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(80, 79);
  g.loadData(payload);
  await settle(rafQueue);
  const firstPositions = Object.assign({}, g.animator._layout.positions);

  /* Load the same structure again — should skip recompute and keep positions. */
  g.loadData(payload);
  assert.equal(g.animator._layout._done, true);
  assert.deepEqual(
    Object.keys(g.animator._layout.positions).sort(),
    Object.keys(firstPositions).sort()
  );
  await settle(rafQueue);
});

test("selecting a node recenters without recomputing the layout", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(60, 59);
  g.loadData(payload);

  /* Record the layout progress marker; recenter must not re-run the simulation. */
  const stepAfterLoad = g.animator._layout._step;
  const doneAfterLoad = g.animator._layout._done;

  g.selectNode(30);
  assert.equal(g.animator._layout._done, doneAfterLoad);
  assert.equal(g.animator._layout._step, stepAfterLoad);
  await settle(rafQueue);
});

test("rapid double load leaves a consistent progressive layout", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  /* 两次快速加载：旧渐进链路应被代数守卫丢弃，新链路完成布局。 */
  const first = makePayload(260, 259);
  const second = makePayload(280, 279);
  g.loadData(first);
  g.loadData(second);
  await settle(rafQueue);

  assert.equal(g._nodes.length, 280);
  assert.equal(g.animator._layout._done, true);
  assert.equal(Object.keys(g.animator._layout.positions).length, 280);
});

/* ── Web Worker 布局测试 ─────────────────────────────────────── */

const workerSource = readFileSync(
  join(here, "../../pages/dashboard/graph-layout-worker.js"), "utf-8"
);

/* 在进程内模拟 Worker：把 worker 脚本的逻辑以假 self 跑起来，消息同步往返。 */
function installFakeWorker() {
  global.Worker = class {
    constructor() {
      const worker = this;
      this.onmessage = null;
      const posts = [];
      const fakeSelf = {
        postMessage(msg) { posts.push(msg); },
        importScripts() {
          /* importScripts 把核心加载进 worker 全局作用域（即 globalThis）。 */
          const saved = global.self;
          global.self = global;
          (0, eval)(coreSource);
          global.self = saved;
        },
      };
      const saved = global.self;
      const savedImportScripts = global.importScripts;
      global.self = fakeSelf;
      global.importScripts = fakeSelf.importScripts;
      (0, eval)(workerSource);
      global.self = saved;
      global.importScripts = savedImportScripts;
      this._dispatch = (msg) => {
        /* 调用 worker 的 onmessage 时 self 应仍指向 worker 全局。 */
        const saved = global.self;
        global.self = fakeSelf;
        fakeSelf.onmessage({ data: msg });
        global.self = saved;
      };
      this._flush = () => {
        while (posts.length) {
          const msg = posts.shift();
          if (worker.onmessage) worker.onmessage({ data: msg });
        }
      };
    }
    postMessage(msg) {
      this._dispatch(msg);
      this._flush();
    }
    terminate() {}
  };
}

test("worker layout completes via fake worker round trip", async () => {
  const rafQueue = loadGraph();
  installFakeWorker();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  assert.equal(g.animator._layout.isWorker, true, "Worker 布局应被启用");

  const payload = makePayload(300, 299);
  g.loadData(payload);
  await settle(rafQueue);

  assert.equal(g._nodes.length, 300);
  assert.equal(g.animator._layout._done, true);
  assert.equal(Object.keys(g.animator._layout.positions).length, 300);
});

test("worker layout falls back to inline when Worker unavailable", async () => {
  const rafQueue = loadGraph();
  const savedWorker = global.Worker;
  global.Worker = undefined;
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  assert.notEqual(g.animator._layout.isWorker, true, "Worker 不可用时回退内联布局");

  const payload = makePayload(120, 119);
  g.loadData(payload);
  await settle(rafQueue);

  assert.equal(g.animator._layout._done, true);
  assert.equal(Object.keys(g.animator._layout.positions).length, 120);
  global.Worker = savedWorker;
});
