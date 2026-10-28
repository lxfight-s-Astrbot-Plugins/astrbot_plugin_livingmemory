import assert from "node:assert/strict";
import test from "node:test";
import { prepareGraph } from "../../pages/dashboard/graph-3d-core.js";
import { MemoryOrbits } from "../../pages/dashboard/graph-3d-orbits.js";

function system() {
  const graph = prepareGraph({
    nodes: [
      { id: 1, type: "person" }, { id: 2, type: "person" },
      { id: 3, type: "topic" }, { id: 4, type: "summary" },
      { id: 5, type: "fact", metadata: { atom_type: "factual" } },
      { id: 6, type: "fact", metadata: { atom_type: "preference" } },
      { id: 7, type: "person" }, { id: 8, type: "fact" },
    ],
    edges: [
      { source: 1, target: 3, weight: 2 }, { source: 2, target: 3, weight: 5 },
      { source: 3, target: 4, weight: 2 }, { source: 1, target: 4, weight: 10 },
      { source: 4, target: 5, weight: 1 }, { source: 3, target: 5, weight: 20 },
      { source: 1, target: 6, weight: 3 }, { source: 3, target: 6, weight: 10 },
      { source: 7, target: 5, weight: 100 },
    ],
  });
  const layout = {
    positions: new Float32Array([0,0,0, 70,0,0, 120,70,12, 135,90,20, 160,100,30, -100,40,10, 1000,0,0, 1040,20,0]),
    communityIds: new Uint16Array([0,0,0,0,0,0,1,1]),
    hubs: new Uint32Array([0,6]), communityRadii: new Float32Array([400,150]),
  };
  const orbits = new MemoryOrbits(graph, layout);
  const positions = layout.positions.slice();
  orbits.advance(0, positions);
  return { graph, layout, orbits, positions };
}

test("types and strongest real ties choose the orbital hierarchy", () => {
  const { orbits } = system();
  assert.deepEqual([...orbits.parents], [-1,0,1,2,3,0,-1,6]);
});

test("orbit assignment preserves the original graph and ignores stronger cross-community ties", () => {
  const { graph, orbits } = system();
  assert.equal(graph.links.length, 9);
  assert.equal(orbits.parents[4], 3);
  for (const index of orbits.order) {
    const parent = orbits.parents[index];
    if (parent >= 0) assert.ok(orbits.order.indexOf(parent) < orbits.order.indexOf(index));
  }
});

test("satellites move around their moving parent at a constant radius", () => {
  const { orbits, positions } = system();
  const before = positions.slice();
  orbits.advance(2, positions);
  assert.notDeepEqual([...positions.slice(6,9)], [...before.slice(6,9)]);
  for (const index of orbits.order) {
    const parent = orbits.parents[index];
    if (parent < 0) continue;
    const distance = Math.hypot(...[0,1,2].map(axis => positions[index*3+axis] - positions[parent*3+axis]));
    assert.ok(Math.abs(distance - orbits.radii[index]) < 0.001);
  }
});

test("holding a fact freezes its ancestry while unrelated systems keep orbiting", () => {
  const { orbits, positions } = system();
  orbits.setHeld([4]);
  const before = positions.slice();
  orbits.advance(3, positions);
  for (const index of [0,1,2,3,4]) assert.deepEqual([...positions.slice(index*3,index*3+3)], [...before.slice(index*3,index*3+3)]);
  assert.notDeepEqual([...positions.slice(21,24)], [...before.slice(21,24)]);
  orbits.setHeld([]);
  orbits.advance(0, positions);
  assert.deepEqual([...positions.slice(12,15)], [...before.slice(12,15)]);
});

test("orbital extents stay inside their community envelope", () => {
  const { orbits, layout, positions } = system();
  for (let time = 0; time < 120; time++) {
    orbits.advance(1, positions);
    for (let i = 0; i < orbits.parents.length; i++) {
      const community = layout.communityIds[i], root = layout.hubs[community];
      const distance = Math.hypot(...[0,1,2].map(axis => positions[i*3+axis] - positions[root*3+axis]));
      assert.ok(distance <= layout.communityRadii[community]);
    }
  }
});
