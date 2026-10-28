import assert from "node:assert/strict";
import test from "node:test";
import { prepareGraph, createSimulation, buildMemoryHierarchy } from "../../pages/dashboard/graph-3d-core.js";
import { MemoryGravity } from "../../pages/dashboard/graph-3d-gravity.js";

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
    frames: new Float32Array([1,0,0,0,1,0,0,0,1, 1,0,0,0,1,0,0,0,1]),
  };
  Object.assign(layout, buildMemoryHierarchy(graph, layout.communityIds, layout.hubs));
  const gravity = new MemoryGravity(graph, layout);
  const positions = layout.positions.slice();
  gravity.advance(0, positions);
  return { graph, layout, gravity, positions };
}

function galaxySystem() {
  const graph = prepareGraph({
    nodes: Array.from({ length: 180 }, (_, id) => ({ id, community: 0,
      type: id === 0 ? "person" : id < 7 ? "topic" : id < 13 ? "summary" : "fact", weight: 1 })),
    edges: Array.from({ length: 179 }, (_, i) => {
      const child = i+1, parent = child < 7 ? 0 : child < 13 ? 1+(child-7)%6 : 7+(child-13)%6;
      return { source: child, target: parent, weight: 1 };
    }),
  });
  const result = createSimulation(graph);
  result.simulation.tick(result.iterations);
  const layout = { positions: new Float32Array(result.nodes.flatMap(node => [node.x,node.y,node.z])),
    communityIds: new Uint16Array(180), hubs: new Uint32Array([result.communities[0].hub]),
    communityRadii: new Float32Array([result.communities[0].radius]), frames: result.frames,
    parents: result.hierarchy.parents, order: result.hierarchy.order };
  const gravity = new MemoryGravity(graph, layout), positions = layout.positions.slice();
  gravity.advance(0, positions);
  return { graph, layout, gravity, positions };
}

test("starting gravity preserves the worker's galaxy layout", () => {
  const { positions, layout } = galaxySystem();
  assert.ok(positions.every((value, index) => value === layout.positions[index]));
});

test("motion retains the lengths of semantic branches within a small displacement", () => {
  const { graph, positions, layout, gravity } = galaxySystem();
  const lengths = () => graph.links.map(link => Math.hypot(...[0,1,2].map(axis => positions[link.source*3+axis]-positions[link.target*3+axis])));
  const before = lengths();
  gravity.advance(40, positions);
  const after = lengths();
  assert.ok(after.every((value,i) => Math.abs(value-before[i]) < layout.communityRadii[0]*0.06));
});

test("types and strongest real ties choose the orbital hierarchy", () => {
  const { gravity } = system();
  assert.deepEqual([...gravity.parents], [-1,0,1,2,3,0,-1,6]);
});

test("orbit assignment preserves the original graph and ignores stronger cross-community ties", () => {
  const { graph, gravity } = system();
  assert.equal(graph.links.length, 9);
  assert.equal(gravity.parents[4], 3);
  for (const index of gravity.order) {
    const parent = gravity.parents[index];
    if (parent >= 0) assert.ok(gravity.order.indexOf(parent) < gravity.order.indexOf(index));
  }
});

test("local gravity produces varying radius and faster motion near its rest center", () => {
  const { gravity, positions } = system();
  let near = { radius: Infinity }, far = { radius: 0 };
  for (let time = 0; time < 600; time++) {
    gravity.advance(0.25, positions);
    const radius = Math.hypot(...gravity.offsets.slice(21,24)), speed = Math.hypot(...gravity.velocities.slice(21,24));
    if (radius < near.radius) near = { radius, speed };
    if (radius > far.radius) far = { radius, speed };
  }
  assert.ok(far.radius - near.radius > far.radius * 0.15);
  assert.ok(near.speed > far.speed * 1.2);
});

test("holding a fact freezes its ancestry while unrelated systems keep orbiting", () => {
  const { gravity, positions } = system();
  gravity.setHeld([4]);
  const before = positions.slice();
  gravity.advance(3, positions);
  for (const index of [0,1,2,3,4]) assert.deepEqual([...positions.slice(index*3,index*3+3)], [...before.slice(index*3,index*3+3)]);
  assert.notDeepEqual([...positions.slice(21,24)], [...before.slice(21,24)]);
  gravity.setHeld([]);
  gravity.advance(0, positions);
  assert.deepEqual([...positions.slice(12,15)], [...before.slice(12,15)]);
});

test("orbital extents stay inside their community envelope", () => {
  const { gravity, layout, positions } = system();
  for (let time = 0; time < 120; time++) {
    gravity.advance(1, positions);
    for (let i = 0; i < gravity.parents.length; i++) {
      const community = layout.communityIds[i], root = layout.hubs[community];
      const distance = Math.hypot(...[0,1,2].map(axis => positions[i*3+axis] - positions[root*3+axis]));
      assert.ok(distance <= layout.communityRadii[community]);
    }
  }
});

test("overview rotation uses the community-weighted center rather than the bounding-box midpoint", () => {
  const { gravity } = system();
  assert.deepEqual([...gravity.center], [250,0,0]);
});

test("gravity preserves the depth of a spatial community", () => {
  const graph = prepareGraph({
    nodes: Array.from({ length: 41 }, (_, id) => ({ id, type: id === 0 ? "person" : "fact" })),
    edges: Array.from({ length: 40 }, (_, i) => ({ source: 0, target: i + 1 })),
  });
  const positions = new Float32Array(41 * 3);
  for (let i = 1; i < 41; i++) {
    const height = 1 - 2 * (i - 0.5) / 40, circle = Math.sqrt(1 - height * height);
    positions.set([Math.cos(i * 2.3999632297) * circle * 100,Math.sin(i * 2.3999632297) * circle * 100,height * 100],i*3);
  }
  const layout = { positions: positions.slice(), communityIds: new Uint16Array(41), hubs: new Uint32Array([0]),
    communityRadii: new Float32Array([300]), frames: new Float32Array([1,0,0,0,1,0,0,0,1]) };
  Object.assign(layout, buildMemoryHierarchy(graph, layout.communityIds, layout.hubs));
  const gravity = new MemoryGravity(graph, layout);
  const min = [Infinity,Infinity,Infinity], max = [-Infinity,-Infinity,-Infinity];
  for (let time = 0; time < 60; time++) {
    gravity.advance(1, positions);
    const instant = [0,1,2].map(axis => {
      const values = Array.from({ length: 40 }, (_, i) => positions[(i+1)*3+axis]);
      return Math.max(...values) - Math.min(...values);
    });
    assert.ok(instant[2] > Math.max(instant[0],instant[1])*0.35, `community flattens at ${time+1}s`);
    for (let i = 1; i < 41; i++) for (let axis = 0; axis < 3; axis++) {
      min[axis] = Math.min(min[axis], positions[i*3+axis]);
      max[axis] = Math.max(max[axis], positions[i*3+axis]);
    }
  }
  const spans = min.map((value,axis) => max[axis]-value);
  assert.ok(spans[2] > Math.max(spans[0],spans[1])*0.65);
});

test("semantic branches remain inside their own descendants throughout motion", () => {
  const { gravity, positions, layout } = galaxySystem();
  for (let time = 0; time < 120; time++) {
    gravity.advance(1, positions);
    const distance = index => Math.hypot(...[0,1,2].map(axis => positions[index*3+axis] - positions[axis]));
    for (let i = 1; i < gravity.parents.length; i++) assert.ok(distance(i) > distance(gravity.parents[i]) - layout.communityRadii[0]*0.01);
  }
});

test("central gravity keeps energy error bounded during sustained motion", () => {
  const { gravity, positions } = system();
  const energy = () => {
    const radius = Math.hypot(...gravity.offsets.slice(21,24));
    const speed = Math.hypot(...gravity.velocities.slice(21,24));
    return speed * speed * 0.5 - gravity.mu[7] / Math.sqrt(radius * radius + gravity.softeningSquared[7]);
  };
  const initial = energy();
  let error = 0;
  for (let time = 0; time < 1200; time++) {
    gravity.advance(0.5, positions);
    error = Math.max(error, Math.abs((energy() - initial) / initial));
  }
  assert.ok(error < 0.0001);
});

test("gravity follows the same trajectory at different rendering frame rates", () => {
  const first = system(), second = system();
  for (let frame = 0; frame < 90; frame++) first.gravity.advance(1/30, first.positions);
  for (let frame = 0; frame < 360; frame++) second.gravity.advance(1/120, second.positions);
  assert.deepEqual([...first.positions], [...second.positions]);
  assert.deepEqual([...first.gravity.velocities], [...second.gravity.velocities]);
});

test("members of a community keep a consistent angular momentum direction", () => {
  const { gravity, positions } = system();
  for (let time = 0; time < 120; time++) {
    gravity.advance(0.5, positions);
    for (const index of [1,2,3,4,5]) {
      const offset = index * 3;
      assert.ok(gravity.offsets[offset] * gravity.velocities[offset + 1] -
        gravity.offsets[offset + 1] * gravity.velocities[offset] > 0);
    }
  }
});

test("nested branches retain readable spacing at deeper levels", () => {
  const { gravity, positions } = galaxySystem();
  const gap = index => Math.hypot(...[0,1,2].map(axis => positions[index*3+axis] - positions[gravity.parents[index]*3+axis]));
  for (let i = 7; i < 13; i++) assert.ok(gap(i) > gap(gravity.parents[i])*0.5);
});

test("terminal nodes fill continuous radial space instead of a narrow shell", () => {
  const { positions, layout } = galaxySystem();
  const radii = Array.from({ length: 167 }, (_, i) => Math.hypot(...positions.slice((i+13)*3,(i+14)*3)) / layout.communityRadii[0]);
  assert.ok(Math.min(...radii) < 0.4);
  assert.ok(Math.max(...radii) > 0.7);
  assert.ok(Math.max(...radii)-Math.min(...radii) > 0.4);
});
