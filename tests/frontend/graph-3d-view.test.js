import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import Graph3D from "../../pages/dashboard/graph-3d.js";

for (const [name,x,y,expected] of [
  ["a foreground star off the cursor does not steal a precise background-node click",500,500,1],
  ["clicking empty screen space does not select a distant node",100,100,-1],
]) test(name, () => {
  const graph = new Graph3D();
  graph._ready = true;
  graph.canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }) };
  graph.camera = new THREE.PerspectiveCamera(48,1,0.1,100);
  graph.camera.position.set(0,0,10);graph.camera.lookAt(0,0,0);graph.camera.updateMatrixWorld();
  graph.graph = { nodes: [{ id: 1 }, { id: 2 }] };
  graph.positions = new THREE.BufferAttribute(new Float32Array([0.045,0,8, 0,0,-10]),3);
  assert.equal(graph._hit(x,y),expected);
});

test("an empty graph has no labels or dependency on a gravity layout", () => {
  const graph = new Graph3D();
  graph._ready = true;
  graph.graph = { nodes: [] };
  graph.labelLayer = { replaceChildren() {} };
  assert.doesNotThrow(() => graph._buildLabels());
  assert.deepEqual(graph._labels, []);
});

test("picking uses the current camera pose before the next render", () => {
  const graph = new Graph3D();
  graph._ready = true;
  graph.graph = { nodes: [{ id: 1 }] };
  graph.canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }) };
  graph.positions = new THREE.BufferAttribute(new Float32Array([0,0,0]),3);
  graph.camera = new THREE.PerspectiveCamera(48,1,0.1,100);
  graph.camera.position.set(0,0,10);graph.camera.lookAt(0,0,0);graph.camera.updateMatrixWorld();
  graph.camera.position.set(5,0,10);graph.camera.lookAt(0,0,0);
  assert.equal(graph._hit(500,500),0);
});
