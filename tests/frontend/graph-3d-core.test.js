import assert from "node:assert/strict";
import test from "node:test";
import { prepareGraph, createSimulation } from "../../pages/dashboard/graph-3d-core.js";

test("parallel relations share geometry while retaining every memory source", () => {
  const snapshot = {
    nodes: [{ id: 10, type: "person", label: "A" }, { id: 20, type: "topic", label: "B" }],
    edges: [
      { source: 10, target: 20, relation_type: "likes", memory_id: 1, weight: 1 },
      { source: 10, target: 20, relation_type: "likes", memory_id: 2, weight: 2 },
      { source: 20, target: 10, relation_type: "likes", memory_id: 3, weight: 1 },
    ],
  };
  const graph = prepareGraph(snapshot);
  assert.equal(graph.links.length, 2);
  assert.equal(graph.links[0].weight, 3);
  assert.deepEqual([...graph.memoryNodes.get(2)], [0, 1]);
  assert.deepEqual([...graph.memoryLinks.get(2)], [0]);
  assert.deepEqual(graph.adjacency[0], [0, 1]);
  assert.equal(snapshot.edges[0].source, 10);
});

test("memory entries locate nodes even when the memory has no edge", () => {
  const graph = prepareGraph({
    nodes: [{ id: 7, label: "A" }], edges: [],
    entries: [{ memory_id: 4, node_ids: [7] }],
  });
  assert.deepEqual([...graph.memoryNodes.get(4)], [0]);
});

test("overview keeps a readable structural subset and full order contains every relation", () => {
  const nodes = Array.from({ length: 100 }, (_, id) => ({ id }));
  const edges = [];
  for (let a = 0; a < 100; a++) for (let b = a + 1; b < 100; b++) edges.push({ source: a, target: b, weight: 1 });
  const graph = prepareGraph({ nodes, edges });
  assert.ok(graph.overviewCount < graph.links.length);
  assert.equal(graph.linkOrder.length, graph.links.length);
  assert.equal(new Set(graph.linkOrder).size, graph.links.length);
});

test("three-dimensional simulation produces stable spatial coordinates", () => {
  const graph = prepareGraph({
    nodes: Array.from({ length: 20 }, (_, id) => ({ id })),
    edges: Array.from({ length: 19 }, (_, i) => ({ source: i, target: i + 1 })),
  });
  const first = createSimulation(graph);
  first.simulation.tick(first.iterations);
  const second = createSimulation(graph);
  second.simulation.tick(second.iterations);
  assert.ok(new Set(first.nodes.map(n => Math.round(n.z))).size > 2);
  assert.deepEqual(first.nodes.map(n => [n.x, n.y, n.z]), second.nodes.map(n => [n.x, n.y, n.z]));
});
