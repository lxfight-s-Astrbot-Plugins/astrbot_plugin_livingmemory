import { forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY, forceZ } from "d3-force-3d";
import "./graph-topology.js";

export const SPACE_COLORS = { person: "#e5bc85", topic: "#98bacd", fact: "#d9d1bd", summary: "#b5a7c1", other: "#a7b6b4" };

export function prepareGraph(snapshot) {
  const typeSizes = { person: 3.6, topic: 1.8, summary: 1.2, fact: 0.65, other: 1 };
  const nodes = snapshot.nodes.map(node => ({
    ...node, id: Number(node.id), pulsar: 0,
    size: (typeSizes[node.type] || typeSizes.other) *
      (1 + Math.min(0.3, Math.log1p(Number(node.memory_count || 0) + Number(node.degree || 0)) * 0.04)),
  }));
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
  // Value cues belong to memory content, not to frequently mentioned entities.
  for (const memory of snapshot.memories || []) {
    const importance = Number(memory.importance || 0) / (memory.importance_scale === "display" ? 10 : 1);
    if (importance < 0.8) continue;
    for (const index of memoryNodes.get(Number(memory.memory_id)) || []) {
      const node = nodes[index];
      if (node.type === "fact" || node.type === "summary") node.pulsar = Math.max(node.pulsar, Math.min(1, importance));
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
  const topologyNodes = graph.nodes.map((node, index) => ({ ...node, id: index }));
  const topology = globalThis.GraphTopology.buildTopologySeed(topologyNodes, graph.links);
  const groups = new Map();
  topologyNodes.forEach((node, index) => {
    const id = topology.assignment[index] || 0;
    if (!groups.has(id)) groups.set(id, { id, members: [] });
    groups.get(id).members.push(index);
  });
  const communities = [...groups.values()].sort((a, b) => a.id - b.id);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  communities.forEach((group, index) => {
    group.radius = 60 + Math.sqrt(group.members.length) * 28;
    const height = 2 * ((index * Math.SQRT2) % 1) - 1;
    const circle = Math.sqrt(1 - height * height), angle = index * goldenAngle;
    const distance = group.radius * 2 * Math.cbrt(index);
    group.x = Math.cos(angle) * circle * distance;
    group.y = Math.sin(angle) * circle * distance;
    group.z = height * distance;
    if (index === 0) Object.assign(group, { fx: 0, fy: 0, fz: 0 });
    group.members.sort((a, b) => {
      const aStar = graph.nodes[a].type === "person" ? 1 : 0, bStar = graph.nodes[b].type === "person" ? 1 : 0;
      return bStar - aStar || Number(graph.nodes[b].weight || 0) - Number(graph.nodes[a].weight || 0) || a - b;
    });
    group.hub = group.members[0];
  });
  if (communities.length > 1) {
    forceSimulation(communities, 3).stop()
      .force("collision", forceCollide(group => group.radius + 40).iterations(2))
      .force("x", forceX().strength(0.025))
      .force("y", forceY().strength(0.025))
      .force("z", forceZ().strength(0.025))
      .tick(100);
  }
  const nodes = new Array(graph.nodes.length);
  const orbitScale = { person: 0.4, topic: 0.65, summary: 0.8, fact: 1, other: 0.8 };
  for (const group of communities) {
    const tilt = Math.sin(group.id * 1.3) * 0.55;
    const counts = {}, ranks = {};
    group.members.forEach(index => { const type = graph.nodes[index].type; counts[type] = (counts[type] || 0) + 1; });
    group.members.forEach((index, rank) => {
      const raw = graph.nodes[index];
      const typeRank = ranks[raw.type] || 0;
      ranks[raw.type] = typeRank + 1;
      const radius = rank === 0 ? 0 : group.radius * (orbitScale[raw.type] || orbitScale.other) *
        (0.25 + 0.65 * Math.sqrt(typeRank / Math.max(1, counts[raw.type] - 1)));
      const angle = rank * goldenAngle;
      const height = 1 - 2 * (typeRank + 0.5) / counts[raw.type];
      const circle = Math.sqrt(1 - height * height);
      const x = Math.cos(angle) * circle * radius, y = Math.sin(angle) * circle * radius;
      const z = height * radius;
      const homeX = group.x + x * Math.cos(tilt) + z * Math.sin(tilt);
      const homeY = group.y + y;
      const homeZ = group.z + z * Math.cos(tilt) - x * Math.sin(tilt);
      nodes[index] = { id: raw.id, type: raw.type, community: group.id, radius: raw.size * 2.2,
        x: homeX, y: homeY, z: homeZ, homeX, homeY, homeZ };
      if (rank === 0) Object.assign(nodes[index], { fx: homeX, fy: homeY, fz: homeZ });
    });
  }
  const links = graph.links.map(link => ({ ...link }));
  const iterations = Math.max(60, 150 - Math.floor(nodes.length / 30));
  const simulation = forceSimulation(nodes, 3).stop()
    .alphaDecay(1 - Math.pow(0.001, 1 / iterations))
    .force("charge", forceManyBody().strength(-18).theta(1.1).distanceMax(160))
    .force("links", forceLink(links).distance(38).strength(link =>
      link.source.community === link.target.community ? Math.min(0.08, 0.02 * Math.sqrt(link.weight)) : 0.001))
    .force("collision", forceCollide(node => node.radius + 2))
    .force("x", forceX(node => node.homeX).strength(0.18))
    .force("y", forceY(node => node.homeY).strength(0.18))
    .force("z", forceZ(node => node.homeZ).strength(0.18));
  return { simulation, nodes, iterations, communities };
}

export function communityRelations(graph, nodes) {
  const overview = [], remaining = [], bridges = new Set();
  for (const index of graph.linkOrder) {
    const link = graph.links[index];
    const source = nodes[link.source].community, target = nodes[link.target].community;
    const pair = source < target ? source + ":" + target : target + ":" + source;
    if ((source === target && overview.length < 4000) || (source !== target && !bridges.has(pair))) {
      overview.push(index);
      if (source !== target) bridges.add(pair);
    } else remaining.push(index);
  }
  const indices = new Uint32Array(graph.links.length * 2);
  overview.concat(remaining).forEach((index, position) => {
    const link = graph.links[index];
    indices[position * 2] = link.source;
    indices[position * 2 + 1] = link.target;
  });
  return { indices, overviewCount: overview.length };
}
