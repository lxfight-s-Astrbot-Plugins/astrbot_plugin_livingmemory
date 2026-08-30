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

test("fact node canvas labels strip person name and date prefixes", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const nodes = [
    { id: 1, type: "person", label: "人物A", weight: 10, degree: 30 },
    { id: 2, type: "person", label: "人物B", weight: 10, degree: 30 },
    { id: 3, type: "fact", label: "人物A 2026-08-14 深夜聊天时被催促去睡觉", weight: 1 },
    { id: 4, type: "fact", label: "人物A 人物B 2026年8月15日 一起看了电影", weight: 1 },
    { id: 5, type: "fact", label: "今天下雨了", weight: 1 },
    { id: 6, type: "fact", label: "人物A 喜欢喝咖啡", weight: 1 },
    { id: 7, type: "topic", label: "人物A 的喜好", weight: 1 },
  ];
  g.loadData({ enabled: true, mode: "query", snapshot: { nodes, edges: [] } });

  const byId = {};
  g._nodes.forEach(function(n) { byId[n.id] = n; });
  /* person 节点标签保持不变。 */
  assert.equal(byId[1].label, "人物A");
  /* fact 标签去掉人物名与日期时间前缀（#248）。 */
  assert.equal(byId[3].label, "深夜聊天时被催促去睡觉");
  assert.equal(byId[4].label, "一起看了电影");
  assert.equal(byId[5].label, "下雨了");
  assert.equal(byId[6].label, "喜欢喝咖啡");
  /* 非 fact 节点不做剥离。 */
  assert.equal(byId[7].label, "人物A 的喜好");
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

test("small graph completes with worker layout via progressive steps", async () => {
  const rafQueue = loadGraph();
  installFakeWorker();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  assert.equal(g.animator._layout.isWorker, true, "Worker 布局应被启用");

  /* ≤60 节点的小图在 Worker 布局下也必须真正跑完：此前同步分支只发
     begin 消息，位置永远不回传，导致所有节点堆在原点（#248 演示发现）。 */
  const payload = makePayload(50, 49);
  g.loadData(payload);
  await settle(rafQueue);

  assert.equal(g._nodes.length, 50);
  assert.equal(g.animator._layout._done, true);
  assert.equal(Object.keys(g.animator._layout.positions).length, 50);
});

test("progressive layout does not render unstable intermediate states", async () => {
  const rafQueue = loadGraph();
  installFakeWorker();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  let renders = 0;
  const renderer = g.renderer;
  const orig = renderer.render.bind(renderer);
  renderer.render = function () { renders += 1; return orig.apply(renderer, arguments); };

  g.loadData(makePayload(300, 299));

  /* 渐进期间推几帧：布局未完时不得渲染中间态（此前 _tick 的漂浮/渲染会
     在旧视口里重绘乱动的节点，整幅图表现为抽搐——#248 演示反馈）。 */
  for (let i = 0; i < 8; i++) {
    flushRaf(rafQueue);
    await Promise.resolve();
  }
  assert.equal(g.animator._layout._done, false, "前置：布局尚未完成");
  assert.equal(renders, 0, "渐进期间不应渲染中间态");

  await settle(rafQueue);
  assert.equal(g.animator._layout._done, true);
  assert.ok(renders > 0, "布局完成后应开始渲染");
});

test("switching back to a previously loaded graph reuses cached layout instantly", async () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const small = makePayload(60, 59);
  const large = makePayload(200, 199);

  g.loadData(small);
  await settle(rafQueue);
  assert.equal(g.animator._layout._done, true);

  g.loadData(large);
  await settle(rafQueue);
  assert.equal(g.animator._layout._done, true);

  /* 切回小图：应立即命中多槽布局缓存，无需重新布局（#248 反馈）。 */
  g.loadData(small);
  assert.equal(g.animator._layout._done, true, "切换回已加载的图应直接复用布局");
  assert.equal(Object.keys(g.animator._layout.positions).length, 60);
  await settle(rafQueue);
});

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
