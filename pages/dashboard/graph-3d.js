import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { prepareGraph, SPACE_COLORS } from "./graph-3d-core.js";

export default class Graph3D {
  constructor() {
    this.colors = SPACE_COLORS;
    this.selection = null;
    this.showAllRelations = false;
    this._active = true;
    this._frame = null;
    this._hoverFrame = null;
    this._flight = null;
    this._generation = 0;
    this._listeners = [];
    this._pointers = new Set();
    this._ready = false;
    this._hover = -1;
    this._scratch = new THREE.Vector3();
    this._schedule = this._schedule.bind(this);
    this._render = this._render.bind(this);
  }

  init(container, callbacks = {}) {
    this.container = container;
    this.callbacks = callbacks;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(48, 1, 0.1, 12000);
    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.canvas = this.renderer.domElement;
    this.canvas.tabIndex = 0;
    this.canvas.setAttribute("aria-label", window.t("graph.view3d"));
    this.container.appendChild(this.canvas);
    this.labelLayer = document.createElement("div");
    this.labelLayer.className = "space-labels";
    this.labelLayer.setAttribute("aria-hidden", "true");
    this.container.appendChild(this.labelLayer);
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.minDistance = 20;
    this.controls.maxDistance = 8000;
    this.controls.addEventListener("change", this._schedule);
    this._onControlStart = () => { this._flight = null; };
    this.controls.addEventListener("start", this._onControlStart);
    this.raycaster = new THREE.Raycaster();
    this.graphGroup = new THREE.Group();
    this.scene.add(this.graphGroup);
    const dust = new Float32Array(1100 * 3);
    for (let i = 0; i < 1100; i++) {
      const angle = i * 2.3999632297, h = 1 - 2 * (i + 0.5) / 1100;
      const radius = 2400 + (i % 17) * 95, circle = Math.sqrt(1 - h * h);
      dust.set([Math.cos(angle) * circle * radius, h * radius, Math.sin(angle) * circle * radius], i * 3);
    }
    this.dust = new THREE.Points(
      new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(dust, 3)),
      new THREE.PointsMaterial({ color: "#8498b0", size: 1.4, transparent: true, opacity: 0.3, depthWrite: false })
    );
    this.scene.add(this.dust);
    // AstrBot plugin frames have an opaque origin; create the worker within it.
    this._workerUrl = URL.createObjectURL(new Blob([__GRAPH_3D_WORKER_SOURCE__], { type: "text/javascript" }));
    this.worker = new Worker(this._workerUrl);
    this.worker.onmessage = ({ data }) => {
      if (data.generation !== this._generation) return;
      this.positions.array.set(data.positions);
      this.positions.needsUpdate = true;
      this.points.geometry.computeBoundingSphere();
      this.edgeGeometry.boundingSphere = this.points.geometry.boundingSphere;
      this.focusEdges.geometry.boundingSphere = this.points.geometry.boundingSphere;
      this.graphGroup.visible = true;
      this._ready = true;
      this._layoutMs = data.layoutMs;
      this._fitScene();
      this._buildLabels();
      if (this.selection) this._applySelection();
      this._schedule();
      this._onLayoutDone?.();
    };
    this.worker.onerror = event => { this.callbacks.onError?.(event.message); };
    this._listen(this.canvas, "pointerdown", event => {
      if (!this._pointers.size) {
        this._pointerStart = { x: event.clientX, y: event.clientY };
        this._gestureMoved = false;
      } else this._gestureMoved = true;
      this._pointers.add(event.pointerId);
      this._clearHover();
    });
    this._listen(this.canvas, "pointermove", event => {
      if (this._pointers.size) {
        if (Math.hypot(event.clientX - this._pointerStart.x, event.clientY - this._pointerStart.y) > 4) this._gestureMoved = true;
        return;
      }
      if (this._hoverFrame !== null) cancelAnimationFrame(this._hoverFrame);
      this._hoverFrame = requestAnimationFrame(() => {
        this._hoverFrame = null;
        const index = this._hit(event.clientX, event.clientY);
        if (index === this._hover) return;
        this._hover = index;
        this.canvas.style.cursor = index < 0 ? "grab" : "pointer";
        if (this.uniforms) this.uniforms.hover.value = index;
        this._buildLabels();
        this._schedule();
      });
    });
    this._listen(this.canvas, "pointerup", event => {
      const click = this._pointers.size === 1 && !this._gestureMoved && event.button === 0;
      this._pointers.delete(event.pointerId);
      if (!click || !this._ready) return;
      const index = this._hit(event.clientX, event.clientY);
      if (index >= 0) {
        const id = this.graph.nodes[index].id;
        this.selectNode(id);
        this.callbacks.onNodeClick?.(id);
      } else {
        this.clearSelection();
        this.callbacks.onBackgroundClick?.();
      }
    });
    this._listen(this.canvas, "pointercancel", event => { this._pointers.delete(event.pointerId); this._gestureMoved = true; });
    this._listen(this.canvas, "pointerleave", () => this._clearHover());
    this._listen(this.canvas, "keydown", event => {
      if (event.key === "Escape") { this.clearSelection(); this.callbacks.onBackgroundClick?.(); }
    });
    this._listen(document, "visibilitychange", () => {
      if (document.hidden) this._stopFrames();
      else this._schedule();
    });
    this._listen(window, "resize", () => this.resize());
    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(container);
    this.resize();
  }

  _listen(target, name, callback) {
    target.addEventListener(name, callback);
    this._listeners.push({ target, name, callback });
  }

  loadData(payload, options = {}) {
    this._onLayoutDone = options.onLayoutDone;
    if (this._payload === payload) {
      if (this._ready) {
        if (!this.selection) this.clearSelection();
        this._onLayoutDone?.();
      }
      return;
    }
    this._payload = payload;
    this._generation++;
    this._flight = null;
    this._ready = false;
    this._clearHover();
    this._disposeGraph();
    this.graph = prepareGraph(payload.snapshot);
    if (this.selection?.type === "node" && !this.graph.nodeIndex.has(this.selection.id)) this.selection = null;
    this.graphGroup.visible = false;
    this._buildBuffers();
    if (!this.graph.nodes.length) {
      this.worker.postMessage({ nodes: [], links: [] });
      this.selection = null;
      this._ready = true;
      this._buildLabels();
      this._schedule();
      this._onLayoutDone?.();
      return;
    }
    this.worker.postMessage({
      generation: this._generation,
      nodes: this.graph.nodes.map(node => ({ id: node.id })),
      links: this.graph.links,
    });
  }

  _buildBuffers() {
    const count = this.graph.nodes.length;
    this.positions = new THREE.BufferAttribute(new Float32Array(count * 3), 3);
    const colors = new Float32Array(count * 3), sizes = new Float32Array(count), indices = new Float32Array(count);
    const color = new THREE.Color();
    this.graph.nodes.forEach((node, i) => {
      color.set(SPACE_COLORS[node.type] || SPACE_COLORS.other).toArray(colors, i * 3);
      sizes[i] = Math.min(2.8, 0.7 + Math.log1p(Number(node.memory_count || 0) + Number(node.degree || 0)) * 0.35);
      indices[i] = i;
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", this.positions);
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute("aIndex", new THREE.BufferAttribute(indices, 1));
    geometry.setAttribute("aEmphasis", new THREE.BufferAttribute(new Float32Array(count), 1));
    this.uniforms = { focus: { value: 0 }, hover: { value: -1 }, dpr: { value: this.renderer.getPixelRatio() } };
    const material = new THREE.ShaderMaterial({
      uniforms: this.uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: `attribute vec3 color; attribute float aSize; attribute float aIndex; attribute float aEmphasis;
        uniform float focus; uniform float hover; uniform float dpr; varying vec3 vColor; varying float vAlpha;
        void main(){vec4 p=modelViewMatrix*vec4(position,1.);gl_Position=projectionMatrix*p;
        float linked=step(.5,aEmphasis);float chosen=step(1.5,aEmphasis);float hovered=1.-step(.5,abs(aIndex-hover));
        gl_PointSize=clamp(aSize*26.*(650./-p.z)*(1.+chosen*.55+hovered*.2)*dpr,3.,160.*dpr);
        vColor=mix(color,vec3(1.,.8,.48),max(chosen,hovered*.5));
        vAlpha=max(hovered,mix(1.,mix(.09,1.,linked),focus))*(1.-smoothstep(1800.,9000.,-p.z));}`,
      fragmentShader: `varying vec3 vColor; varying float vAlpha; void main(){float r=length(gl_PointCoord-.5)*2.;
        if(r>1.)discard;float core=exp(-r*r*90.);float glow=exp(-r*r*8.)*.24;
        gl_FragColor=vec4(mix(vColor,vec3(1.,.94,.82),core*.6),(core+glow)*vAlpha);}`,
    });
    this.points = new THREE.Points(geometry, material);
    this.graphGroup.add(this.points);
    const lineIndices = new Uint32Array(this.graph.links.length * 2);
    this.graph.linkOrder.forEach((index, i) => {
      const link = this.graph.links[index];
      lineIndices[i * 2] = link.source; lineIndices[i * 2 + 1] = link.target;
    });
    this.edgeGeometry = new THREE.BufferGeometry().setAttribute("position", this.positions);
    this.edgeGeometry.setIndex(new THREE.BufferAttribute(lineIndices, 1));
    this.edges = new THREE.LineSegments(this.edgeGeometry,
      new THREE.LineBasicMaterial({ color: "#bfa782", transparent: true, opacity: 0.045, depthWrite: false }));
    this.graphGroup.add(this.edges);
    const focusGeometry = new THREE.BufferGeometry().setAttribute("position", this.positions);
    focusGeometry.setIndex([]);
    this.focusEdges = new THREE.LineSegments(focusGeometry,
      new THREE.LineBasicMaterial({ color: "#e4ba75", transparent: true, opacity: 0.24, depthWrite: false }));
    this.graphGroup.add(this.focusEdges);
    this._updateRelations();
  }

  _fitScene() {
    const sphere = this.points.geometry.boundingSphere;
    this._center = sphere.center.clone();
    this._radius = Math.max(sphere.radius, 30);
    const fov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    this._overviewDistance = this._radius / Math.sin(Math.min(fov, Math.atan(Math.tan(fov) * this.camera.aspect))) * 1.12;
    this.controls.maxDistance = Math.max(8000, this._overviewDistance * 3);
    this.camera.far = Math.max(12000, this._overviewDistance * 5);
    this.camera.updateProjectionMatrix();
    this._homePosition = this._center.clone().addScaledVector(new THREE.Vector3(0.03, 0.14, 1).normalize(), this._overviewDistance);
    this.camera.position.copy(this._homePosition);
    this.controls.target.copy(this._center);
    this.controls.update();
    this._savedCamera = null;
  }

  selectNode(id) {
    this.selection = { type: "node", id };
    if (this._ready) this._applySelection();
  }

  selectMemory(id) {
    this.selection = { type: "memory", id };
    if (this._ready) this._applySelection();
  }

  _applySelection() {
    const selection = this.selection;
    const indices = selection.type === "node"
      ? [this.graph.nodeIndex.get(selection.id)]
      : [...(this.graph.memoryNodes.get(selection.id) || [])];
    if (!indices.length || indices[0] === undefined) { this.clearSelection(); return; }
    if (!this._savedCamera) this._savedCamera = { position: this.camera.position.clone(), target: this.controls.target.clone() };
    const target = new THREE.Vector3();
    for (const index of indices) target.add(this._scratch.fromBufferAttribute(this.positions, index));
    target.divideScalar(indices.length);
    this._focusNodeIndices = indices;
    this._updateRelations();
    const lengths = [];
    for (const index of this._highlightNodes) lengths.push(this._scratch.fromBufferAttribute(this.positions, index).distanceTo(target));
    lengths.sort((a, b) => a - b);
    const radius = lengths[Math.floor(lengths.length * 0.7)] || this._radius * 0.1;
    const distance = Math.max(65, Math.min(this._overviewDistance * 0.48, radius / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.25));
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    const offset = new THREE.Vector3(this.width < 600 ? 0 : 1, this.width < 600 ? -1 : 0, 0).applyQuaternion(this.camera.quaternion);
    target.addScaledVector(offset, distance * 0.23);
    this._fly(target.clone().addScaledVector(direction, distance), target, 1);
    this._buildLabels();
  }

  _updateRelations() {
    if (!this.edges) return;
    this.edgeGeometry.setDrawRange(0, (this.showAllRelations ? this.graph.links.length : this.graph.overviewCount) * 2);
    this.edges.material.opacity = this.selection ? 0.003 : this.showAllRelations ? 0.025 : 0.045;
    const emphasis = this.points.geometry.attributes.aEmphasis;
    emphasis.array.fill(0);
    this._highlightNodes = new Set();
    let links = [];
    if (this.selection?.type === "node") links = this.graph.adjacency[this.graph.nodeIndex.get(this.selection.id)] || [];
    else if (this.selection?.type === "memory") links = [...(this.graph.memoryLinks.get(this.selection.id) || [])];
    links = links.slice().sort((a, b) => this.graph.links[b].weight - this.graph.links[a].weight || a - b);
    if (!this.showAllRelations) links = links.slice(0, 64);
    const lineIndices = [];
    for (const index of links) {
      const link = this.graph.links[index];
      lineIndices.push(link.source, link.target);
      this._highlightNodes.add(link.source).add(link.target);
      emphasis.array[link.source] = emphasis.array[link.target] = 1;
    }
    for (const index of this._focusNodeIndices || []) { emphasis.array[index] = 2; this._highlightNodes.add(index); }
    emphasis.needsUpdate = true;
    this.focusEdges.geometry.setIndex(lineIndices);
    this.focusEdges.visible = lineIndices.length > 0;
  }

  setShowAllRelations(show) {
    this.showAllRelations = show;
    this._updateRelations();
    this._schedule();
  }

  clearSelection() {
    this.selection = null;
    this._focusNodeIndices = [];
    this._updateRelations();
    if (this._savedCamera) {
      this._fly(this._savedCamera.position, this._savedCamera.target, 0);
      this._savedCamera = null;
    } else if (this.uniforms) this.uniforms.focus.value = 0;
    this._buildLabels();
    this._schedule();
  }

  resetView() {
    this.selection = null;
    this._focusNodeIndices = [];
    this._savedCamera = null;
    this._updateRelations();
    if (this._ready && this.graph.nodes.length) this._fly(this._homePosition, this._center, 0);
    this._buildLabels();
  }

  _fly(position, target, focus) {
    this._flight = {
      fromPosition: this.camera.position.clone(), fromTarget: this.controls.target.clone(),
      position: position.clone(), target: target.clone(), fromFocus: this.uniforms.focus.value, focus,
      started: performance.now(), duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 1 : 1050,
    };
    this._schedule();
  }

  _buildLabels() {
    this.labelLayer.replaceChildren();
    this._labels = [];
    if (!this._ready || !this.graph) return;
    const candidates = new Set([this._hover, ...(this._focusNodeIndices || []), ...(this.selection ? this._highlightNodes : this.graph.labelOrder.slice(0, 16))]);
    for (const index of candidates) {
      if (index < 0) continue;
      if (this._labels.length >= 24) break;
      const element = document.createElement("span");
      element.className = "space-node-label";
      element.textContent = this.graph.nodes[index].label || this.graph.nodes[index].canonical_value || "";
      this.labelLayer.appendChild(element);
      this._labels.push({ index, element });
    }
  }

  _hit(clientX, clientY) {
    if (!this._ready || !this.graph.nodes.length) return -1;
    const rect = this.canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2((clientX - rect.left) / rect.width * 2 - 1, -(clientY - rect.top) / rect.height * 2 + 1);
    this.raycaster.params.Points.threshold = this.camera.position.distanceTo(this.controls.target) * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 14 / this.height;
    this.raycaster.setFromCamera(pointer, this.camera);
    return this.raycaster.intersectObject(this.points)[0]?.index ?? -1;
  }

  _clearHover() {
    if (this._hoverFrame !== null) cancelAnimationFrame(this._hoverFrame);
    this._hoverFrame = null;
    this._hover = -1;
    if (this.uniforms) this.uniforms.hover.value = -1;
    if (this.labelLayer) this._buildLabels();
    this._schedule();
  }

  _schedule() {
    if (!this._active || document.hidden || this._frame !== null) return;
    this._frame = requestAnimationFrame(this._render);
  }

  _render(now) {
    this._frame = null;
    if (!this._active || document.hidden) return;
    if (this._flight) {
      const flight = this._flight;
      const t = Math.min(1, (now - flight.started) / flight.duration);
      const eased = t < 0.5 ? 16 * t ** 5 : 1 - (-2 * t + 2) ** 5 / 2;
      this.camera.position.lerpVectors(flight.fromPosition, flight.position, eased);
      this.controls.target.lerpVectors(flight.fromTarget, flight.target, eased);
      this.uniforms.focus.value = THREE.MathUtils.lerp(flight.fromFocus, flight.focus, eased);
      if (t === 1) this._flight = null;
      else this._schedule();
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    const boxes = [];
    for (const { index, element } of this._labels || []) {
      const point = this._scratch.fromBufferAttribute(this.positions, index).project(this.camera);
      const x = (point.x * 0.5 + 0.5) * this.width, y = (-point.y * 0.5 + 0.5) * this.height + 14;
      const width = Math.min(150, element.textContent.length * 11);
      const box = { x: x - width / 2, y, width, height: 20 };
      const hidden = point.z < -1 || point.z > 1 || x < 0 || x > this.width || y < 0 || y > this.height ||
        boxes.some(other => box.x < other.x + other.width && box.x + width > other.x && box.y < other.y + other.height && box.y + 20 > other.y);
      element.hidden = hidden;
      if (!hidden) { boxes.push(box); element.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px) translateX(-50%)`; }
    }
  }

  _stopFrames() {
    if (this._frame !== null) cancelAnimationFrame(this._frame);
    if (this._hoverFrame !== null) cancelAnimationFrame(this._hoverFrame);
    this._frame = this._hoverFrame = null;
  }

  setActive(active) {
    this._active = active;
    this.controls.enabled = active;
    if (active) { this.resize(); this._schedule(); }
    else this._stopFrames();
  }

  resize() {
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.width = width; this.height = height;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
    if (this._ready && this.graph.nodes.length && !this.selection) this._fitScene();
    this._schedule();
  }

  _disposeGraph() {
    for (const object of this.graphGroup.children) { object.geometry.dispose(); object.material.dispose(); }
    this.graphGroup.clear();
    this._focusNodeIndices = [];
  }

  destroy() {
    this._active = false;
    this._stopFrames();
    this.worker.terminate();
    URL.revokeObjectURL(this._workerUrl);
    this.controls.removeEventListener("change", this._schedule);
    this.controls.removeEventListener("start", this._onControlStart);
    this.controls.dispose();
    this._resizeObserver.disconnect();
    for (const { target, name, callback } of this._listeners) target.removeEventListener(name, callback);
    this._disposeGraph();
    this.dust.geometry.dispose(); this.dust.material.dispose();
    this.renderer.dispose();
    this.container.replaceChildren();
  }

  getDiagnostics() {
    return {
      sourceNodes: this.graph?.nodes.length || 0, sourceEdges: this._payload?.snapshot.edges.length || 0,
      renderedRelations: this.graph ? this.edgeGeometry.drawRange.count / 2 : 0,
      drawCalls: this.renderer.info.render.calls, layoutMs: this._layoutMs || 0,
      framePending: this._frame !== null, layoutReady: this._ready, animating: !!this._flight,
      cameraDistance: this.camera.position.distanceTo(this.controls.target),
    };
  }
}
