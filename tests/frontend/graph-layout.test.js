import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "../../pages/dashboard/graph-2d.js"), "utf-8");

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

test("small graph layout completes synchronously with positions", () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(50, 49);
  g.loadData(payload);

  assert.equal(g._nodes.length, 50);
  assert.equal(Object.keys(g.animator._layout.positions).length, 50);
  assert.equal(g.animator._layout._done, true);
  flushRaf(rafQueue);
});

test("large graph layout completes via progressive stepping", () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(300, 299);
  g.loadData(payload);

  /* Layout completes via progressive rAF chunks. */
  flushRaf(rafQueue);
  assert.equal(g._nodes.length, 300);
  assert.equal(g.animator._layout._done, true);
  assert.equal(Object.keys(g.animator._layout.positions).length, 300);
});

test("identical graph structure reuses cached layout", () => {
  const rafQueue = loadGraph();
  const g = global.window.Graph2D;
  g.init(makeContainer(), {});

  const payload = makePayload(80, 79);
  g.loadData(payload);
  const firstPositions = Object.assign({}, g.animator._layout.positions);

  /* Load the same structure again — should skip recompute and keep positions. */
  g.loadData(payload);
  assert.equal(g.animator._layout._done, true);
  assert.deepEqual(
    Object.keys(g.animator._layout.positions).sort(),
    Object.keys(firstPositions).sort()
  );
  flushRaf(rafQueue);
});

test("selecting a node recenters without recomputing the layout", () => {
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
  flushRaf(rafQueue);
});
