import { forceSimulation, forceManyBody, forceLink, forceCenter, forceX, forceY, forceZ } from "d3-force-3d";

export const SPACE_COLORS = { person: "#e5bc85", topic: "#98bacd", fact: "#d9d1bd", summary: "#b5a7c1", other: "#a7b6b4" };

export function prepareGraph(snapshot) {
  const nodes = snapshot.nodes.map(node => ({ ...node, id: Number(node.id) }));
  const nodeIndex = new Map(nodes.map((node, index) => [node.id, index]));
  const links = [], linkIndex = new Map();
  const adjacency = nodes.map(() => []), memoryNodes = new Map(), memoryLinks = new Map();
  for (const edge of snapshot.edges) {
    const source = nodeIndex.get(Number(edge.source)), target = nodeIndex.get(Number(edge.target));
    if (source === undefined || target === undefined) continue;
    const key = `${source}:${target}:${edge.relation_type || "related"}`;
    let index = linkIndex.get(key);
    if (index === undefined) {
      index = links.length;
      linkIndex.set(key, index);
      links.push({ source, target, weight: 0 });
      adjacency[source].push(index);
      if (source !== target) adjacency[target].push(index);
    }
    links[index].weight += Number(edge.weight || 1);
    const memoryId = Number(edge.memory_id);
    if (!memoryNodes.has(memoryId)) { memoryNodes.set(memoryId, new Set()); memoryLinks.set(memoryId, new Set()); }
    memoryNodes.get(memoryId).add(source).add(target);
    memoryLinks.get(memoryId).add(index);
  }
  for (const entry of [...(snapshot.entries || []), ...(snapshot.memories || [])]) {
    const memoryId = Number(entry.memory_id);
    if (!memoryNodes.has(memoryId)) memoryNodes.set(memoryId, new Set());
    for (const id of entry.node_ids || []) {
      const index = nodeIndex.get(Number(id));
      if (index !== undefined) memoryNodes.get(memoryId).add(index);
    }
  }
  const ranked = links.map((_, index) => index).sort((a, b) => links[b].weight - links[a].weight || a - b);
  const degree = new Uint8Array(nodes.length), overview = [], remaining = [];
  const budget = Math.min(4000, Math.max(160, nodes.length * 2));
  for (const index of ranked) {
    const link = links[index];
    if (overview.length < budget && (degree[link.source] < 2 || degree[link.target] < 2)) {
      overview.push(index);
      if (degree[link.source] < 2) degree[link.source]++;
      if (degree[link.target] < 2) degree[link.target]++;
    } else remaining.push(index);
  }
  const labelOrder = nodes.map((_, index) => index).sort((a, b) =>
    Number(nodes[b].memory_count || 0) - Number(nodes[a].memory_count || 0) ||
    Number(nodes[b].weight || 0) - Number(nodes[a].weight || 0));
  return { nodes, nodeIndex, links, adjacency, memoryNodes, memoryLinks, labelOrder, linkOrder: overview.concat(remaining), overviewCount: overview.length };
}

export function createSimulation(graph) {
  const nodes = graph.nodes.map(node => ({ id: node.id }));
  const links = graph.links.map(link => ({ ...link }));
  const iterations = Math.max(100, 240 - Math.floor(nodes.length / 30));
  const simulation = forceSimulation(nodes, 3).stop()
    .alphaDecay(1 - Math.pow(0.001, 1 / iterations))
    .force("charge", forceManyBody().strength(-42).theta(1.1).distanceMax(600))
    .force("links", forceLink(links).distance(48).strength(link => Math.min(0.22, 0.035 * Math.sqrt(link.weight))))
    .force("center", forceCenter())
    .force("x", forceX().strength(0.004))
    .force("y", forceY().strength(0.004))
    .force("z", forceZ().strength(0.004));
  return { simulation, nodes, iterations };
}
