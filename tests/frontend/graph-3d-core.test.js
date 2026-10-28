import assert from "node:assert/strict";
import test from "node:test";
import { prepareGraph, createSimulation, communityRelations } from "../../pages/dashboard/graph-3d-core.js";

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

test("node types have distinct sizes without turning high-degree facts into stars", () => {
  const graph = prepareGraph({ nodes: [
    { id: 1, type: "person" }, { id: 2, type: "topic" },
    { id: 3, type: "summary" }, { id: 4, type: "fact", degree: 10000, memory_count: 10000 },
  ], edges: [] });
  const [person, topic, summary, fact] = graph.nodes;
  assert.ok(person.size > topic.size);
  assert.ok(topic.size > summary.size);
  assert.ok(summary.size > fact.size);
});

function clusteredGraph() {
  const nodes = Array.from({ length: 54 }, (_, id) => ({ id, type: id % 18 === 0 ? "person" : "fact", weight: 1 }));
  const edges = [];
  for (let group = 0; group < 3; group++) {
    const hub = group * 18;
    for (let i = 1; i < 18; i++) {
      edges.push({ source: hub, target: hub + i, weight: 4 });
      if (i < 17) edges.push({ source: hub + i, target: hub + i + 1, weight: 1 });
    }
  }
  edges.push({ source: 1, target: 19, weight: 1 }, { source: 20, target: 37, weight: 1 });
  return prepareGraph({ nodes, edges });
}

test("communities stay separated while their representative nodes anchor local systems", () => {
  const result = createSimulation(clusteredGraph());
  result.simulation.tick(result.iterations);
  assert.ok(result.communities.length >= 3);
  for (const community of result.communities) {
    const members = result.nodes.filter(node => node.community === community.id);
    const hub = result.nodes[community.hub];
    assert.equal(hub.type, "person");
    assert.ok(Math.hypot(hub.x - community.x, hub.y - community.y, hub.z - community.z) < 1);
    const radius = Math.max(...members.map(node => Math.hypot(node.x - hub.x, node.y - hub.y, node.z - hub.z)));
    for (const other of result.communities) {
      if (other.id === community.id) continue;
      assert.ok(Math.hypot(community.x - other.x, community.y - other.y, community.z - other.z) > radius * 1.5);
    }
  }
});

test("community layout keeps disconnected nodes finite and separate from the connected system", () => {
  const graph = prepareGraph({
    nodes: Array.from({ length: 12 }, (_, id) => ({ id, type: id === 0 ? "person" : "fact" })),
    edges: Array.from({ length: 7 }, (_, i) => ({ source: 0, target: i + 1 })),
  });
  const result = createSimulation(graph);
  result.simulation.tick(result.iterations);
  assert.equal(result.nodes.length, 12);
  assert.ok(result.nodes.every(node => [node.x, node.y, node.z].every(Number.isFinite)));
  assert.notEqual(result.nodes[0].community, result.nodes[11].community);
});

test("community overview keeps one bridge per community pair and retains all relations", () => {
  const graph = prepareGraph({
    nodes: Array.from({ length: 8 }, (_, id) => ({ id })),
    edges: Array.from({ length: 16 }, (_, i) => ({ source: i % 4, target: 4 + Math.floor(i / 4) })),
  });
  const nodes = graph.nodes.map((node, i) => ({ ...node, community: i < 4 ? 0 : 1 }));
  const result = communityRelations(graph, nodes);
  assert.equal(result.overviewCount, 1);
  assert.equal(result.indices.length, 32);
  const pairs = Array.from({ length: 16 }, (_, i) => result.indices[i * 2] + ":" + result.indices[i * 2 + 1]);
  assert.equal(new Set(pairs).size, 16);
});
