import { createSimulation, communityRelations } from "./graph-3d-core.js";

let timer;
self.onmessage = ({ data }) => {
  clearTimeout(timer);
  if (!data.nodes.length) return;
  const started = performance.now();
  const { simulation, nodes, iterations, communities } = createSimulation(data);
  const { indices, overviewCount } = communityRelations(data, nodes);
  let remaining = iterations;
  function step() {
    const count = Math.min(8, remaining);
    simulation.tick(count);
    remaining -= count;
    if (remaining) { timer = setTimeout(step, 0); return; }
    const positions = new Float32Array(nodes.length * 3);
    nodes.forEach((node, index) => positions.set([node.x, node.y, node.z], index * 3));
    self.postMessage({ generation: data.generation, positions, indices, overviewCount,
      hubs: communities.map(group => group.hub), layoutMs: performance.now() - started }, [positions.buffer, indices.buffer]);
  }
  step();
};
