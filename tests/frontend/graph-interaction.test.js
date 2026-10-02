import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function setup() {
  const frames = new Map();
  let frameId = 0;
  const events = {};
  let reads = 0, nodeTests = 0, edgeTests = 0;
  const clicks = [], hovers = [];
  const canvas = {
    style: {},
    getBoundingClientRect() { reads++; return { left: 10, top: 20 }; },
    addEventListener(name, callback) { events[name] = callback; },
    removeEventListener(name) { delete events[name]; },
  };
  const window = {
    addEventListener(name, callback) { events[`window:${name}`] = callback; },
    removeEventListener(name) { delete events[`window:${name}`]; },
  };
  const context = vm.createContext({
    window,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
  });
  for (const file of ["graph-shared.js", "graph-interaction.js"]) {
    vm.runInContext(readFileSync(new URL(`../../pages/dashboard/${file}`, import.meta.url), "utf8"), context);
  }
  const renderer = {
    viewport: { ox: 0, oy: 0, scale: 1 },
    node: null, edge: null, _nodesMap: { 1: { x: 0, y: 0 } },
    hitTestNode(x, y) { nodeTests++; this.lastHit = { x, y }; return this.node; },
    hitTestEdge() { edgeTests++; return this.edge; },
    screenToWorld(x, y) { return { x: x / this.viewport.scale - this.viewport.ox, y: y / this.viewport.scale - this.viewport.oy }; },
  };
  const interaction = new window.GraphInteraction(null, canvas, renderer, {
    onNodeClick: id => clicks.push(id),
    onBackgroundClick: () => clicks.push("background"),
    onNodeHover: id => hovers.push(id),
  });
  return { interaction, renderer, canvas, events, frames, clicks, hovers,
    counts: () => ({ reads, nodeTests, edgeTests }),
    flush() { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback()); },
  };
}

function touchEvent(points, changed = []) {
  return { touches: points.map(([clientX, clientY]) => ({ clientX, clientY })),
    changedTouches: changed.map(([clientX, clientY]) => ({ clientX, clientY })),
    prevented: false, preventDefault() { this.prevented = true; } };
}

test("a burst of mouse moves performs one hit test using the latest coordinates", () => {
  const { events, frames, flush, counts, renderer } = setup();
  for (let i = 0; i < 100; i++) events.mousemove({ clientX: i + 10, clientY: i + 20 });
  assert.equal(frames.size, 1);
  assert.deepEqual(counts(), { reads: 0, nodeTests: 0, edgeTests: 0 });
  flush();
  assert.deepEqual(counts(), { reads: 1, nodeTests: 1, edgeTests: 1 });
  assert.deepEqual(renderer.lastHit, { x: 99, y: 99 });
});

test("releasing before the next frame still applies the final queued drag", () => {
  const { events, renderer, frames, clicks } = setup();
  renderer.node = { id: 1 };
  events.mousedown({ clientX: 10, clientY: 20, button: 0, preventDefault() {} });
  events.mousemove({ clientX: 90, clientY: 100 });
  events["window:mouseup"]({ clientX: 90, clientY: 100 });
  assert.equal(renderer._nodesMap[1].x, 80);
  assert.equal(renderer._nodesMap[1].y, 80);
  assert.equal(frames.size, 0);
  assert.deepEqual(clicks, []);
});

test("single-finger tap and drag work without a synthetic preventDefault error", () => {
  const { events, renderer, clicks, canvas } = setup();
  renderer.node = { id: 1 };
  const start = touchEvent([[10, 20]]);
  events.touchstart(start);
  assert.equal(start.prevented, true);
  assert.equal(canvas.style.touchAction, "none");
  events.touchend(touchEvent([], [[10, 20]]));
  assert.deepEqual(clicks, [1]);
  events.touchstart(touchEvent([[10, 20]]));
  const move = touchEvent([[60, 70]]);
  events.touchmove(move);
  events.touchend(touchEvent([], [[60, 70]]));
  assert.equal(move.prevented, true);
  assert.equal(renderer._nodesMap[1].x, 50);
  assert.deepEqual(clicks, [1]);
});

test("pinch keeps the midpoint anchored and never turns a remaining finger into a click", () => {
  const { events, renderer, clicks, interaction } = setup();
  events.touchstart(touchEvent([[10, 20]]));
  const start = touchEvent([[10, 20], [110, 20]]);
  events.touchstart(start);
  const move = touchEvent([[10, 20], [210, 20]]);
  events.touchmove(move);
  assert.equal(start.prevented, true);
  assert.equal(move.prevented, true);
  assert.equal(renderer.viewport.scale, 2);
  assert.deepEqual(renderer.screenToWorld(100, 0), { x: 50, y: 0 });
  events.touchend(touchEvent([[10, 20]], [[210, 20]]));
  events.touchmove(touchEvent([[30, 40]]));
  events.touchend(touchEvent([], [[30, 40]]));
  assert.equal(interaction._pinching, false);
  assert.deepEqual(clicks, []);
});

test("leaving or cancelling discards pending gestures without accidental clicks", () => {
  const { events, renderer, frames, clicks, interaction } = setup();
  renderer.node = { id: 1 };
  events.mousedown({ clientX: 10, clientY: 20, button: 0, preventDefault() {} });
  events.mousemove({ clientX: 20, clientY: 30 });
  events.mouseleave({ clientX: 10, clientY: 20 });
  assert.equal(frames.size, 0);
  assert.deepEqual(clicks, []);
  events.touchstart(touchEvent([[10, 20]]));
  events.touchcancel();
  events.touchend(touchEvent([], [[10, 20]]));
  assert.equal(interaction._dragging, false);
  assert.deepEqual(clicks, []);
});

test("node-to-edge hover clears node feedback even when both have the same id", () => {
  const { events, renderer, flush, hovers, interaction } = setup();
  renderer.node = { id: 1 };
  events.mousemove({ clientX: 10, clientY: 20 }); flush();
  renderer.node = null; renderer.edge = { id: 1 };
  events.mousemove({ clientX: 30, clientY: 40 }); flush();
  assert.deepEqual(hovers, [1, null]);
  assert.equal(interaction.getHoverType(), "edge");
});

test("destroy removes listeners and cancels pending pointer work", () => {
  const { events, interaction, frames } = setup();
  events.mousemove({ clientX: 20, clientY: 30 });
  interaction.destroy();
  assert.equal(frames.size, 0);
  assert.deepEqual(Object.keys(events), []);
});
