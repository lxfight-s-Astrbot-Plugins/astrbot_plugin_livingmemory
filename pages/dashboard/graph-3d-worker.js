import { createSimulation, communityRelations } from "./graph-3d-core.js";

let timer;
self.onmessage = ({ data }) => {
  clearTimeout(timer);
  if (!data.nodes.length) return;
  const started = performance.now();
  const { simulation, nodes, iterations, communities, communityLinks, hierarchy, frames } = createSimulation(data);
  const { parents, order } = hierarchy;
  const { indices, overviewCount, ranges } = communityRelations(data, nodes, communities.length, parents);
  const bundles = communityLinks.slice().sort((a, b) => b.weight - a.weight).slice(0, Math.min(80, communities.length * 2));
  const bundlePairs = new Uint16Array(bundles.flatMap(link => [link.source.id, link.target.id]));
  const bundleWeights = new Float32Array(bundles.map(link => link.weight));
  let remaining = iterations;
  function step() {
    const count = Math.min(8, remaining);
    simulation.tick(count);
    remaining -= count;
    if (remaining) { timer = setTimeout(step, 0); return; }
    const positions = new Float32Array(nodes.length * 3);
    const communityIds = new Uint16Array(nodes.length);
    const hubs = new Uint32Array(communities.length), communityRadii = new Float32Array(communities.length);
    nodes.forEach((node, index) => {
      positions.set([node.x, node.y, node.z], index * 3);
      communityIds[index] = node.community;
    });
    communities.forEach(group => { hubs[group.id] = group.hub; communityRadii[group.id] = group.radius; });
    self.postMessage({ generation: data.generation, positions, indices, overviewCount,
      hubs, communityIds, communityRadii, parents, order, frames, ranges, bundlePairs, bundleWeights, layoutMs: performance.now() - started },
    [positions.buffer, indices.buffer, hubs.buffer, communityIds.buffer, communityRadii.buffer, parents.buffer, order.buffer,
      frames.buffer, ranges.buffer, bundlePairs.buffer, bundleWeights.buffer]);
  }
  step();
};
