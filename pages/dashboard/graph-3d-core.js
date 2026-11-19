import { forceSimulation, forceManyBody, forceLink, forceCollide, forceX, forceY, forceZ } from "d3-force-3d";

const TYPE_ORDER = { person: 0, topic: 1, summary: 2, fact: 3, other: 4 };
const PARENTS = { person: [], topic: ["person"], summary: ["topic", "person"], fact: ["summary", "topic", "person"], other: ["summary", "topic", "person"] };

export function prepareGraph(snapshot) {
  const typeSizes = { person: 3.6, topic: 1.8, summary: 1.2, fact: 0.65, other: 1 };
  const nodes = snapshot.nodes.map(node => ({
    ...node, id: Number(node.id), pulsar: 0, atomType: node.metadata?.atom_type,
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
    links[index].weight += Number(edge.weight ?? 1);
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
    const content = [...(memoryNodes.get(Number(memory.memory_id)) || [])].filter(index => nodes[index].type === "fact" || nodes[index].type === "summary");
    const index = content.find(index => nodes[index].type === "summary") ?? content[0];
    if (index !== undefined) nodes[index].pulsar = Math.max(nodes[index].pulsar, Math.min(1, importance));
  }
  const labelOrder = nodes.map((_, index) => index).sort((a, b) =>
    Number(nodes[b].memory_count || 0) - Number(nodes[a].memory_count || 0) ||
    Number(nodes[b].weight || 0) - Number(nodes[a].weight || 0));
  return { nodes, nodeIndex, links, adjacency, memoryNodes, memoryLinks, labelOrder, overviewCount: 0 };
}

export function buildMemoryHierarchy(graph, communityIds, hubs) {
  const parents = new Int32Array(graph.nodes.length).fill(-1), roots = new Set(hubs);
  for (let i = 0; i < graph.nodes.length; i++) {
    if (roots.has(i)) continue;
    const node = graph.nodes[i], community = communityIds[i];
    const preferences = node.type === "fact" && (node.atomType === "relational" || node.atomType === "preference")
      ? ["person", "summary", "topic"] : (PARENTS[node.type] || PARENTS.other);
    let parent = hubs[community], bestRank = Infinity, bestWeight = -Infinity;
    for (const edgeIndex of graph.adjacency[i]) {
      const link = graph.links[edgeIndex], other = link.source === i ? link.target : link.source;
      if (other === i || communityIds[other] !== community) continue;
      const rank = preferences.indexOf(graph.nodes[other].type);
      if (rank >= 0 && (rank < bestRank || (rank === bestRank && link.weight > bestWeight))) {
        parent = other; bestRank = rank; bestWeight = link.weight;
      }
    }
    parents[i] = parent;
  }
  const order = new Uint32Array([...hubs, ...graph.nodes.map((_, i) => i).filter(i => !roots.has(i)).sort((a, b) =>
    (TYPE_ORDER[graph.nodes[a].type] ?? TYPE_ORDER.other) - (TYPE_ORDER[graph.nodes[b].type] ?? TYPE_ORDER.other) || a - b)]);
  const childCounts = new Uint32Array(graph.nodes.length);
  for (const parent of parents) if (parent >= 0) childCounts[parent]++;
  return { parents, order, childCounts };
}

const MAX_ARMS = 5;
const GALAXY_CYCLE = [1, 2, 3, 4, 5, 1, 2, 3, 4, 2, 5];

export function createSimulation(graph) {
  const groups = new Map();
  graph.nodes.forEach((node, index) => {
    const id = node.community;
    if (!groups.has(id)) groups.set(id, { id, members: [] });
    groups.get(id).members.push(index);
  });
  const communities = [...groups.values()].sort((a, b) => a.id - b.id);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  communities.forEach((group, index) => {
    group.radius = 48 + Math.sqrt(group.members.length) * 22;
    const height = 2 * ((index * Math.SQRT2) % 1) - 1;
    const circle = Math.sqrt(1 - height * height), angle = index * goldenAngle;
    const distance = group.radius * 1.35 * Math.cbrt(index);
    group.x = Math.cos(angle) * circle * distance;
    group.y = Math.sin(angle) * circle * distance;
    group.z = height * distance;
    if (index === 0) Object.assign(group, { fx: 0, fy: 0, fz: 0 });
    group.members.sort((a, b) => {
      const aStar = graph.nodes[a].type === "person" ? 1 : 0, bStar = graph.nodes[b].type === "person" ? 1 : 0;
      return bStar - aStar || Number(graph.nodes[b].weight || 0) - Number(graph.nodes[a].weight || 0) || a - b;
    });
    group.hub = group.members[0];
    group.spiral = group.members.length >= 80 && group.members.some(i => graph.adjacency[i].some(edgeIndex => {
      const edge = graph.links[edgeIndex];
      return edge.source !== edge.target && edge.weight > 0;
    }));
    // Assign diverse astronomical morphologies: 0=cluster, 1=spiral, 2=barred, 3=flocculent, 4=ring, 5=elliptical.
    if (!group.spiral) {
      group.galaxyType = 0;
      group.arms = 1;
    } else {
      group.galaxyType = GALAXY_CYCLE[group.id % GALAXY_CYCLE.length];
      group.arms = group.galaxyType === 3 ? (group.members.length >= 450 ? 5 : 4)
        : group.galaxyType === 1 ? (group.members.length >= 400 ? 3 : 2)
        : group.galaxyType === 2 ? 2
        : 1;
    }
  });
  const communityLinkMap = new Map();
  for (const link of graph.links) {
    const a = graph.nodes[link.source].community, b = graph.nodes[link.target].community;
    if (a === b || link.weight === 0) continue;
    const source = Math.min(a, b), target = Math.max(a, b), key = source + ":" + target;
    if (!communityLinkMap.has(key)) communityLinkMap.set(key, { source, target, weight: 0 });
    communityLinkMap.get(key).weight += link.weight;
  }
  const communityLinks = [...communityLinkMap.values()];
  if (communities.length > 1) {
    forceSimulation(communities, 3).stop()
      .force("collision", forceCollide(group => group.radius + 16).iterations(2))
      .force("links", forceLink(communityLinks).id(group => group.id)
        .distance(link => link.source.radius + link.target.radius + 28 + 200 / (1 + Math.sqrt(link.weight)))
        .strength(link => Math.min(0.45, 0.1 + Math.log1p(link.weight) * 0.045)))
      .force("x", forceX().strength(0.022))
      .force("y", forceY().strength(0.022))
      .force("z", forceZ().strength(0.022))
      .tick(100);
  }
  const communityIds = new Uint16Array(graph.nodes.map(node => node.community));
  const hubs = new Uint32Array(communities.map(group => group.hub));
  const hierarchy = buildMemoryHierarchy(graph, communityIds, hubs);
  const { parents, order, childCounts } = hierarchy;
  const leafCounts = new Uint32Array(graph.nodes.length), heights = new Uint8Array(graph.nodes.length);
  for (let index = order.length - 1; index >= 0; index--) {
    const i = order[index], parent = parents[i];
    if (!childCounts[i]) leafCounts[i] = 1;
    if (parent >= 0) { leafCounts[parent] += leafCounts[i]; heights[parent] = Math.max(heights[parent], heights[i] + 1); }
  }
  const armIds = new Int8Array(graph.nodes.length).fill(-1), childRanks = new Uint32Array(graph.nodes.length);
  const armLeaves = new Uint32Array(communities.length * MAX_ARMS), armCursor = new Uint32Array(communities.length * MAX_ARMS);
  for (const i of order) {
    const parent = parents[i], group = communities[communityIds[i]];
    if (parent < 0) continue;
    armIds[i] = armIds[parent] >= 0 ? armIds[parent] : childCounts[parent] > 1
      ? childRanks[parent]++ % group.arms : childCounts[i] ? -1 : 0;
    if (!childCounts[i]) armLeaves[group.id * MAX_ARMS + armIds[i]]++;
  }
  const frames = new Float32Array(communities.length * 9);
  for (const group of communities) {
    const yaw = group.id * goldenAngle + 0.35;
    // Keep the central galaxy at a clear three-quarter tilt while giving others varied astronomical inclinations.
    const tilt = group.id === 0 ? 0.52 : 0.36 + ((group.id * 0.61803398875) % 1) * 1.04;
    const c = Math.cos(yaw), s = Math.sin(yaw), ct = Math.cos(tilt), st = Math.sin(tilt);
    frames.set([c,s,0, -s*ct,c*ct,st, s*st,-c*st,ct], group.id * 9);
  }
  const nodes = new Array(graph.nodes.length), low = new Float64Array(graph.nodes.length);
  const cursor = new Uint32Array(graph.nodes.length), radial = new Float64Array(graph.nodes.length);
  for (const index of order) {
    const raw = graph.nodes[index], group = communities[raw.community], hub = group.hub, parent = parents[index];
    const arm = armIds[index], armIndex = group.id * MAX_ARMS + arm;
    const total = arm >= 0 ? armLeaves[armIndex] : leafCounts[hub];
    if (parent >= 0 && arm >= 0) {
      if (armIds[parent] < 0) { low[index] = armCursor[armIndex] / total; armCursor[armIndex] += leafCounts[index]; }
      else { low[index] = low[parent] + cursor[parent] / total; cursor[parent] += leafCounts[index]; }
    }
    const fraction = low[index] + leafCounts[index] * 0.5 / total;
    const firstLeaf = low[index] + 0.5 / total;
    const gType = group.galaxyType;
    const radialCurve = f => {
      if (gType === 0) return 0.22 + 0.64 * Math.sqrt(f);
      if (gType === 4) return 0.12 + 0.78 * (0.28 * f + 0.72 * Math.sin(f * Math.PI * 0.5) ** 0.68);
      if (gType === 5) return 0.08 + 0.80 * Math.pow(f, 0.78);
      return 0.10 + 0.80 * Math.pow(f, 0.56);
    };
    const endRadius = group.radius * radialCurve(firstLeaf);
    radial[index] = parent < 0 ? 0 : childCounts[index]
      ? radial[parent] + (endRadius - radial[parent]) / (heights[index] + 1)
      : group.radius * radialCurve(fraction);
    const r = radial[index] / group.radius;
    const u1 = ((index * 0.61803398875) % 1) * 2 - 1;
    const u2 = ((index * 0.41421356237 + 0.37) % 1) * 2 - 1;
    const u3 = ((index * 0.73205080757 + 0.71) % 1) * 2 - 1;
    const bellZ = (u1 + u2 + u3) / 3;
    const v1 = ((index * 0.30901699437 + 0.13) % 1) * 2 - 1;
    const v2 = ((index * 0.86602540378 + 0.53) % 1) * 2 - 1;
    const v3 = ((index * 0.22474487139 + 0.89) % 1) * 2 - 1;
    const bellAngle = (v1 + v2 + v3) / 3;
    let angle, height;
    if (gType === 0) {
      angle = fraction * Math.PI * 2 + index * goldenAngle * 0.3;
      height = 1 - 2 * ((index * Math.SQRT2) % 1);
    } else if (gType === 2) {
      // Barred spiral: inner bar stays aligned along 0/PI before sweeping into trailing spiral arms.
      const barWind = r < 0.30 ? 0.35 * (r / 0.30) ** 2 : 0.35 + 3.4 * Math.log(1 + 4.0 * (r - 0.30));
      const barScatter = r < 0.30 ? 0.14 + 0.12 * (1 - r / 0.30) : 0.20 + 0.32 * (r - 0.30);
      angle = Math.max(0, arm) * Math.PI - barWind + bellAngle * barScatter;
      height = bellZ * (0.42 * Math.exp(-r * r * 11) + 0.035 * (1 - 0.25 * r));
    } else if (gType === 3) {
      // Flocculent multi-arm spiral: tighter winding with softer wispy arm dispersion.
      angle = Math.max(0, arm) * Math.PI * 2 / group.arms - 4.2 * Math.log(1 + 4.6 * r) + bellAngle * (0.30 + 0.42 * r);
      height = bellZ * (0.36 * Math.exp(-r * r * 12) + 0.040 * (1 - 0.2 * r));
    } else if (gType === 4) {
      // Ring / lenticular galaxy: prominent central lens and thin luminous outer star-forming ring.
      angle = fraction * Math.PI * 2 + index * goldenAngle * 0.15 + bellAngle * 0.35;
      height = bellZ * (0.46 * Math.exp(-r * r * 15) + 0.028);
    } else if (gType === 5) {
      // Elliptical galaxy: smooth 3D spheroidal stellar distribution without arms.
      angle = fraction * Math.PI * 2 + index * goldenAngle * 0.5;
      height = bellZ * (0.58 + 0.16 * (1 - r));
    } else {
      // Grand-design trailing logarithmic spiral with compact central bulge and thin disk.
      angle = Math.max(0, arm) * Math.PI * 2 / group.arms - 3.2 * Math.log(1 + 4.4 * r) + bellAngle * (0.24 + 0.32 * r);
      height = bellZ * (0.40 * Math.exp(-r * r * 12) + 0.036 * (1 - 0.25 * r));
    }
    const planar = gType === 0 ? Math.sqrt(1 - height * height) : 1 / Math.hypot(1, height);
    const ex = gType === 5 ? 1.12 : 1, ey = gType === 5 ? 0.88 : 1;
    const x = Math.cos(angle) * planar * radial[index] * ex, y = Math.sin(angle) * planar * radial[index] * ey;
    const z = (gType === 0 ? height : height * planar) * radial[index], frame = group.id * 9;
    const homeX = group.x + frames[frame]*x + frames[frame+3]*y + frames[frame+6]*z;
    const homeY = group.y + frames[frame+1]*x + frames[frame+4]*y + frames[frame+7]*z;
    const homeZ = group.z + frames[frame+2]*x + frames[frame+5]*y + frames[frame+8]*z;
    nodes[index] = { id: raw.id, type: raw.type, community: group.id, radius: raw.size * 1.5,
      x: homeX, y: homeY, z: homeZ, homeX, homeY, homeZ };
    if (parent < 0) Object.assign(nodes[index], { fx: homeX, fy: homeY, fz: homeZ });
  }
  const links = graph.links.map(link => ({ ...link }));
  const iterations = Math.max(60, 150 - Math.floor(nodes.length / 30));
  const simulation = forceSimulation(nodes, 3).stop()
    .alphaDecay(1 - Math.pow(0.001, 1 / iterations))
    .force("charge", forceManyBody().strength(-6).theta(1.1).distanceMax(80))
    .force("links", forceLink(links).distance(38).strength(link => {
      const degree = Math.min(graph.adjacency[link.source.index].length, graph.adjacency[link.target.index].length);
      return link.source.community === link.target.community ? Math.min(0.035, 0.025 * Math.sqrt(link.weight) / degree)
        : 0.0002 * Math.min(1, link.weight);
    }))
    .force("collision", forceCollide(node => node.radius + 2))
    .force("x", forceX(node => node.homeX).strength(0.55))
    .force("y", forceY(node => node.homeY).strength(0.55))
    .force("z", forceZ(node => node.homeZ).strength(0.55));
  return { simulation, nodes, iterations, communities, communityLinks, hierarchy, frames };
}

export function communityRelations(graph, nodes, communityCount, parents) {
  const local = Array.from({ length: communityCount }, () => []), remaining = [];
  const degree = new Uint8Array(nodes.length), sizes = new Uint32Array(communityCount);
  for (const node of nodes) sizes[node.community]++;
  const budgets = Uint8Array.from(sizes, size => Math.min(120, Math.max(12, Math.floor(Math.sqrt(size) * 5))));
  const tree = link => parents[link.source] === link.target || parents[link.target] === link.source;
  const ranked = graph.links.map((_, index) => index).sort((a, b) =>
    Number(tree(graph.links[b])) - Number(tree(graph.links[a])) || graph.links[b].weight - graph.links[a].weight || a - b);
  for (const index of ranked) {
    const link = graph.links[index];
    const source = nodes[link.source].community, target = nodes[link.target].community;
    if (source === target && link.source !== link.target && link.weight > 0 &&
        local[source].length < budgets[source] && degree[link.source] < 4 && degree[link.target] < 4) {
      local[source].push(index);
      degree[link.source]++; degree[link.target]++;
    } else remaining.push(index);
  }
  const overview = local.flat(), ranges = new Uint32Array(communityCount * 2);
  let start = 0;
  local.forEach((indices, community) => { ranges.set([start, indices.length], community * 2); start += indices.length; });
  const indices = new Uint32Array(graph.links.length * 2);
  overview.concat(remaining).forEach((index, position) => {
    const link = graph.links[index];
    indices[position * 2] = link.source;
    indices[position * 2 + 1] = link.target;
  });
  return { indices, overviewCount: overview.length, ranges };
}
