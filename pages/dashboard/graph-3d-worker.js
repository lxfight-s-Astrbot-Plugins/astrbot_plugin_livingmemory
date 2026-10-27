import { createSimulation } from "./graph-3d-core.js";

let timer;
self.onmessage = ({ data }) => {
  clearTimeout(timer);
  if (!data.nodes.length) return;
  const started = performance.now();
  const { simulation, nodes, iterations } = createSimulation(data);
  let remaining = iterations;
  function step() {
    const count = Math.min(8, remaining);
    simulation.tick(count);
    remaining -= count;
    if (remaining) { timer = setTimeout(step, 0); return; }
    const positions = new Float32Array(nodes.length * 3);
    nodes.forEach((node, index) => positions.set([node.x, node.y, node.z], index * 3));
    self.postMessage({ generation: data.generation, positions, layoutMs: performance.now() - started }, [positions.buffer]);
  }
  step();
};
