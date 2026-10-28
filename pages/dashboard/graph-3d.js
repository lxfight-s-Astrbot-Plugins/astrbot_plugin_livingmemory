import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { prepareGraph, SPACE_COLORS } from "./graph-3d-core.js";
import { MemoryGravity } from "./graph-3d-gravity.js";

export default class Graph3D {
  constructor() {
    this.colors = SPACE_COLORS;
    this.selection = null;
    this.showAllRelations = false;
    this.motionEnabled = true;
    this._active = true;
    this._frame = null;
    this._hoverFrame = null;
    this._motionTimer = null;
    this._lastMotionStamp = null;
    this._gravity = null;
    this._relationCommunity = -1;
    this._communityRanges = null;
    this._galaxyPhases = null;
    this.galaxyDust = null;
    this.communityLines = null;
    this._pulseClock = 0;
    this._viewOffset = { x: 0, y: 0 };
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
    this.motionEnabled = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
    this._onControlStart = () => {
      if (this._flight && this.selection) {
        this.controls.target.copy(this._selectionCenter());
        const offset = this._focusViewOffset();
        this._setViewOffset(offset.x, offset.y);
        this.controls.update();
      }
      this._flight = null;
    };
    this.controls.addEventListener("start", this._onControlStart);
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
      this._gravity = new MemoryGravity(this.graph, data);
      this._gravity.advance(0, this.positions.array);
      this._syncGravityHold();
      this.positions.needsUpdate = true;
      this.edgeGeometry.setIndex(new THREE.BufferAttribute(data.indices, 1));
      this.graph.overviewCount = data.overviewCount;
      this._communityRanges = data.ranges;
      const hubs = new Set(data.hubs);
      this.graph.labelOrder = [...hubs, ...this.graph.labelOrder.filter(index => !hubs.has(index))];
      const star = this.points.geometry.attributes.aStar;
      for (const hub of data.hubs) star.array[hub] = 1;
      star.needsUpdate = true;
      const bounds = new THREE.Sphere(new THREE.Vector3().fromArray(this._gravity.center), 0);
      for (let community = 0; community < data.hubs.length; community++) {
        this._scratch.fromBufferAttribute(this.positions, data.hubs[community]);
        bounds.radius = Math.max(bounds.radius, bounds.center.distanceTo(this._scratch) + data.communityRadii[community] + 12);
      }
      this.points.geometry.boundingSphere = bounds;
      this.edgeGeometry.boundingSphere = this.points.geometry.boundingSphere;
      this.focusEdges.geometry.boundingSphere = this.points.geometry.boundingSphere;
      this._buildGalaxyDust(data);
      this._buildCommunityLinks(data);
      this.graphGroup.visible = true;
      this._ready = true;
      this._updateRelations();
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
        this._syncGravityHold();
        this._updateRelations();
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
      const index = this._hit(event.clientX, event.clientY, event.pointerType === "touch" ? 18 : 8);
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
    this._stopFrames();
    this._gravity = null;
    this._pulseClock = 0;
    this._generation++;
    this._flight = null;
    this._ready = false;
    this._clearHover();
    this._disposeGraph();
    this._relationCommunity = -1;
    this._communityRanges = null;
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
      nodes: this.graph.nodes.map(({ id, type, size, weight, community, atomType }) => ({ id, type, size, weight, community, atomType })),
      links: this.graph.links,
      adjacency: this.graph.adjacency,
    });
  }

  _buildBuffers() {
    const count = this.graph.nodes.length;
    this.positions = new THREE.BufferAttribute(new Float32Array(count * 3), 3);
    const colors = new Float32Array(count * 3), sizes = new Float32Array(count), indices = new Float32Array(count);
    const stars = new Float32Array(count), pulsars = new Float32Array(count);
    const color = new THREE.Color();
    this.graph.nodes.forEach((node, i) => {
      color.set(SPACE_COLORS[node.type] || SPACE_COLORS.other).toArray(colors, i * 3);
      sizes[i] = node.size;
      indices[i] = i;
      stars[i] = node.type === "person" ? 0.35 : 0;
      pulsars[i] = node.pulsar;
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", this.positions);
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute("aIndex", new THREE.BufferAttribute(indices, 1));
    geometry.setAttribute("aStar", new THREE.BufferAttribute(stars, 1));
    geometry.setAttribute("aPulsar", new THREE.BufferAttribute(pulsars, 1));
    geometry.setAttribute("aEmphasis", new THREE.BufferAttribute(new Float32Array(count), 1));
    this.uniforms = { focus: { value: 0 }, hover: { value: -1 }, dpr: { value: this.renderer.getPixelRatio() }, viewDistance: { value: 650 }, pulseTime: { value: 0 } };
    const material = new THREE.ShaderMaterial({
      uniforms: this.uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      vertexShader: `attribute vec3 color; attribute float aSize; attribute float aIndex; attribute float aEmphasis; attribute float aStar; attribute float aPulsar;
        uniform float focus; uniform float hover; uniform float dpr; uniform float viewDistance; uniform float pulseTime;
        varying vec3 vColor; varying float vAlpha; varying float vStar; varying float vCore; varying float vPulsar; varying float vEmission; varying float vRing; varying vec2 vBeam;
        void main(){vec4 p=modelViewMatrix*vec4(position,1.);gl_Position=projectionMatrix*p;
        float linked=step(.5,aEmphasis);float chosen=step(1.5,aEmphasis);float hovered=1.-step(.5,abs(aIndex-hover));
        gl_PointSize=clamp(aSize*10.*pow(abs(viewDistance/p.z),.6)*(1.+chosen*.55+hovered*.2+aPulsar*.55)*dpr,2.,90.*dpr);
        vColor=mix(color,vec3(1.,.8,.48),max(chosen,hovered*.5));
        vStar=aStar*(.85+.15*sin(pulseTime*.5+aIndex));vPulsar=aPulsar;
        vCore=mix(aStar<.01?1.:aStar>.9?.85:.55,1.,max(chosen,hovered));
        vEmission=aPulsar*pow(.5+.5*sin(pulseTime*1.3+aIndex*2.399963),5.);
        vBeam=vec2(cos(pulseTime*.6+aIndex),sin(pulseTime*.6+aIndex));
        vRing=.18+fract(pulseTime*.23+aIndex*.37)*.75;
        vAlpha=max(hovered,mix(1.,mix(.09,1.,linked),focus))*(1.-smoothstep(1.2,4.,-p.z/viewDistance));}`,
      fragmentShader: `varying vec3 vColor; varying float vAlpha; varying float vStar; varying float vCore; varying float vPulsar; varying float vEmission; varying float vRing; varying vec2 vBeam;
        void main(){vec2 point=gl_PointCoord-.5;float r=length(point)*2.;
        if(r>1.)discard;float core=exp(-r*r*75.);float glow=exp(-r*r*12.)*.10;
        float corona=vStar*exp(-r*r*3.2)*.065;float emission=0.;
        if(vPulsar>0.){float beam=exp(-abs(dot(point,vec2(-vBeam.y,vBeam.x)))*55.)*exp(-r*2.5);
          float ring=exp(-pow((r-vRing)*35.,2.))*.12*(1.-vRing);
          emission=vEmission*(beam*.25+ring)+vPulsar*exp(-r*r*2.8)*.025;}
        vec3 tint=mix(vColor,vec3(1.,.94,.82),core*.6);tint=mix(tint,vec3(.86,.94,1.),min(.45,emission*.6));
        gl_FragColor=vec4(tint,(core*vCore+glow+corona+emission)*vAlpha);
        #include <colorspace_fragment>
        }`,
    });
    this.points = new THREE.Points(geometry, material);
    this.graphGroup.add(this.points);
    const lineIndices = new Uint32Array(this.graph.links.length * 2);
    this.graph.links.forEach((link, i) => {
      lineIndices[i * 2] = link.source; lineIndices[i * 2 + 1] = link.target;
    });
    this.edgeGeometry = new THREE.BufferGeometry().setAttribute("position", this.positions);
    this.edgeGeometry.setIndex(new THREE.BufferAttribute(lineIndices, 1));
    this.edges = new THREE.LineSegments(this.edgeGeometry,
      new THREE.LineBasicMaterial({ color: "#d2b993", transparent: true, depthWrite: false }));
    this.graphGroup.add(this.edges);
    const focusGeometry = new THREE.BufferGeometry().setAttribute("position", this.positions);
    focusGeometry.setIndex([]);
    this.focusEdges = new THREE.LineSegments(focusGeometry,
      new THREE.LineBasicMaterial({ color: "#f4d19b", transparent: true, opacity: 0.55, depthWrite: false }));
    this.graphGroup.add(this.focusEdges);
    this._updateRelations();
  }

  _buildGalaxyDust(data) {
    const count = Math.min(24000, this.graph.nodes.length * 12);
    const positions = new Float32Array(count * 3), centers = new Float32Array(count * 3), spins = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3), sizes = new Float32Array(count), systems = new Float32Array(count), haze = new Float32Array(count);
    const warm = new THREE.Color("#d9ac75"), cool = new THREE.Color("#7199cd"), tint = new THREE.Color();
    for (let i = 0; i < count; i++) {
      const node = i % this.graph.nodes.length, community = data.communityIds[node], center = data.hubs[community] * 3;
      const radius = data.communityRadii[community], seed = (i * 0.61803398875) % 1;
      const angle = i * 2.3999632297, height = 1 - 2 * (i + 0.5) / count, circle = Math.sqrt(1-height*height);
      const spread = radius * (0.018 + 0.07 * seed * seed), offset = i * 3;
      positions.set([data.positions[node*3]+Math.cos(angle)*circle*spread,
        data.positions[node*3+1]+Math.sin(angle)*circle*spread, data.positions[node*3+2]+height*spread], offset);
      centers.set(data.positions.subarray(center, center+3), offset);
      spins.set(data.frames.subarray(community*9+6, community*9+9), offset);
      const distance = Math.hypot(...[0,1,2].map(axis => data.positions[node*3+axis]-data.positions[center+axis])) / radius;
      tint.copy(warm).lerp(cool, Math.min(1, distance)).toArray(colors, offset);
      haze[i] = i % 19 === 0 ? 1 : 0;
      sizes[i] = haze[i] ? 28 + seed * 18 : 1.3 + seed ** 3 * 2.8;
      systems[i] = community;
    }
    this._galaxyPhases = new THREE.DataTexture(new Float32Array(data.hubs.length*4), data.hubs.length, 1, THREE.RGBAFormat, THREE.FloatType);
    this._galaxyPhases.needsUpdate = true;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("aCenter", new THREE.BufferAttribute(centers, 3));
    geometry.setAttribute("aSpin", new THREE.BufferAttribute(spins, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute("aSystem", new THREE.BufferAttribute(systems, 1));
    geometry.setAttribute("aHaze", new THREE.BufferAttribute(haze, 1));
    geometry.boundingSphere = this.points.geometry.boundingSphere;
    const material = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uPhase: { value: this._galaxyPhases }, uSystems: { value: data.hubs.length }, uOpacity: { value: 1 },
        dpr: this.uniforms.dpr, viewDistance: this.uniforms.viewDistance },
      vertexShader: `attribute vec3 aCenter;attribute vec3 aSpin;attribute vec3 color;
        attribute float aSize;attribute float aSystem;attribute float aHaze;
        uniform sampler2D uPhase;uniform float uSystems;uniform float dpr;uniform float viewDistance;
        varying vec3 vColor;varying float vHaze;varying float vDepth;
        void main(){float angle=texture2D(uPhase,vec2((aSystem+.5)/uSystems,.5)).r;
          vec3 p=position-aCenter;float c=cos(angle),s=sin(angle);
          p=aCenter+p*c+cross(aSpin,p)*s+aSpin*dot(aSpin,p)*(1.-c);
          vec4 view=modelViewMatrix*vec4(p,1.);gl_Position=projectionMatrix*view;
          gl_PointSize=clamp(aSize*dpr*pow(abs(viewDistance/view.z),.6),.8,65.*dpr);
          vColor=color;vHaze=aHaze;vDepth=1.-smoothstep(1.5,5.,-view.z/viewDistance);}`,
      fragmentShader: `uniform float uOpacity;varying vec3 vColor;varying float vHaze;varying float vDepth;
        void main(){float r=length(gl_PointCoord-.5)*2.;if(r>1.)discard;
          float light=exp(-r*r*mix(7.,3.,vHaze))*mix(.22,.012,vHaze);
          gl_FragColor=vec4(vColor,light*vDepth*uOpacity);
          #include <colorspace_fragment>
        }`,
    });
    this.galaxyDust = new THREE.Points(geometry, material);
    this.graphGroup.add(this.galaxyDust);
  }

  _buildCommunityLinks(data) {
    this._bundleCount = data.bundleWeights.length;
    if (!this._bundleCount) return;
    const positions = [], colors = [], color = new THREE.Color("#aab7d2");
    for (let i = 0; i < this._bundleCount; i++) {
      const source = data.bundlePairs[i*2], target = data.bundlePairs[i*2+1];
      const a = new THREE.Vector3().fromBufferAttribute(this.positions, data.hubs[source]);
      const b = new THREE.Vector3().fromBufferAttribute(this.positions, data.hubs[target]);
      const normal = b.clone().sub(a).cross(new THREE.Vector3(0.3,0.8,0.5)).normalize();
      const bend = Math.min(a.distanceTo(b)*0.14, (data.communityRadii[source]+data.communityRadii[target])*0.3);
      const curve = new THREE.CubicBezierCurve3(a, a.clone().lerp(b,0.32).addScaledVector(normal,bend),
        a.clone().lerp(b,0.68).addScaledVector(normal,bend), b);
      const intensity = 0.35 + 0.65 * Math.log1p(data.bundleWeights[i]) / Math.log1p(data.bundleWeights[0]);
      for (let segment = 0; segment < 12; segment++) {
        positions.push(...curve.getPoint(segment/12).toArray(), ...curve.getPoint((segment+1)/12).toArray());
        for (let end = 0; end < 2; end++) colors.push(color.r*intensity,color.g*intensity,color.b*intensity);
      }
    }
    const geometry = new THREE.BufferGeometry().setAttribute("position",new THREE.Float32BufferAttribute(positions,3));
    geometry.setAttribute("color",new THREE.Float32BufferAttribute(colors,3));
    this.communityLines = new THREE.LineSegments(geometry,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.3, depthWrite: false }));
    this.graphGroup.add(this.communityLines);
  }

  _fitScene() {
    this._updateHomeFrame();
    this.camera.position.copy(this._homePosition);
    this.controls.target.copy(this._center);
    this._setViewOffset(0, 0);
    this.controls.update();
    this._savedCamera = null;
  }

  _updateHomeFrame() {
    const sphere = this.points.geometry.boundingSphere;
    this._center = sphere.center.clone();
    this._radius = Math.max(sphere.radius, 30);
    const fov = THREE.MathUtils.degToRad(this.camera.fov / 2);
    this._overviewDistance = this._radius / Math.sin(Math.min(fov, Math.atan(Math.tan(fov) * this.camera.aspect))) * 1.12;
    this.uniforms.viewDistance.value = this._overviewDistance;
    this.controls.maxDistance = Math.max(8000, this._overviewDistance * 3);
    this.camera.far = Math.max(12000, this._overviewDistance * 5);
    this.camera.updateProjectionMatrix();
    this._homePosition = this._center.clone().addScaledVector(new THREE.Vector3(0.03, 0.14, 1).normalize(), this._overviewDistance);
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
    if (!this._savedCamera) this._savedCamera = { position: this.camera.position.clone(), target: this.controls.target.clone(), offset: { ...this._viewOffset } };
    this._focusNodeIndices = indices;
    this._syncGravityHold();
    const target = this._selectionCenter();
    this._updateRelations();
    const lengths = [];
    for (const index of this._highlightNodes) lengths.push(this._scratch.fromBufferAttribute(this.positions, index).distanceTo(target));
    lengths.sort((a, b) => a - b);
    const radius = lengths[Math.floor(lengths.length * 0.7)] || this._radius * 0.1;
    const distance = Math.max(65, Math.min(this._overviewDistance * 0.48, radius / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.25));
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    this._fly(target.clone().addScaledVector(direction, distance), target, 1, this._focusViewOffset());
    this._buildLabels();
  }

  _selectionCenter() {
    const center = new THREE.Vector3();
    for (const index of this._focusNodeIndices) center.add(this._scratch.fromBufferAttribute(this.positions, index));
    return center.divideScalar(this._focusNodeIndices.length);
  }

  _focusViewOffset() {
    return this.width < 600 ? { x: 0, y: Math.min(150, this.height * 0.18) }
      : { x: Math.min(165, this.width * 0.2), y: 0 };
  }

  _setViewOffset(x, y) {
    this._viewOffset.x = x; this._viewOffset.y = y;
    if (x === 0 && y === 0) this.camera.clearViewOffset();
    else this.camera.setViewOffset(this.width, this.height, x, y, this.width, this.height);
  }

  _updateRelations() {
    if (!this.edges) return;
    let start = 0, count = 0;
    if (this.showAllRelations) count = this.graph.links.length;
    else if (!this.selection && this._relationCommunity >= 0) {
      start = this._communityRanges[this._relationCommunity*2];
      count = this._communityRanges[this._relationCommunity*2+1];
    }
    this.edgeGeometry.setDrawRange(start*2, count*2);
    this.edges.visible = count > 0;
    if (this.communityLines) {
      this.communityLines.visible = !this.showAllRelations;
      this.communityLines.material.opacity = this.selection ? 0.08 : this._relationCommunity >= 0 ? 0.15 : 0.3;
    }
    if (this.galaxyDust) this.galaxyDust.material.uniforms.uOpacity.value = this.selection ? 0.45 : 1;
    const density = Math.max(1, this.graph.links.length / Math.max(1, this.graph.nodes.length));
    this.edges.material.opacity = this.selection ? 0.015 : this.showAllRelations
      ? Math.min(0.16, Math.max(0.045, 0.24 / Math.sqrt(density))) : 0.15;
    const emphasis = this.points.geometry.attributes.aEmphasis;
    emphasis.array.fill(0);
    this._highlightNodes = new Set();
    let links = [];
    if (this.selection?.type === "node") links = this.graph.adjacency[this.graph.nodeIndex.get(this.selection.id)] || [];
    else if (this.selection?.type === "memory") links = [...(this.graph.memoryLinks.get(this.selection.id) || [])];
    else if (this._hover >= 0) links = this.graph.adjacency[this._hover];
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

  _updateRelationLevel() {
    if (!this._ready || !this.graph.nodes.length) return;
    let next = -1, nearest = 1.5;
    if (!this.showAllRelations && !this.selection) {
      for (let community = 0; community < this._gravity.hubs.length; community++) {
        this._scratch.fromBufferAttribute(this.positions, this._gravity.hubs[community]);
        const radius = this._gravity.communityRadii[community];
        const distance = this.camera.position.distanceTo(this._scratch) / radius;
        const attention = this.controls.target.distanceTo(this._scratch) / radius;
        if (distance < 3.2 && attention < nearest) { nearest = attention; next = community; }
      }
    }
    if (next !== this._relationCommunity) { this._relationCommunity = next; this._updateRelations(); this._buildLabels(); }
  }

  setShowAllRelations(show) {
    this.showAllRelations = show;
    this._updateRelations();
    this._schedule();
  }

  setMotionEnabled(enabled) {
    this.motionEnabled = enabled;
    this._lastMotionStamp = null;
    if (this._motionTimer !== null) clearTimeout(this._motionTimer);
    this._motionTimer = null;
    this._schedule();
  }

  _syncGravityHold() {
    if (!this._gravity) return;
    this._gravity.setHeld([...(this._focusNodeIndices || []), this._hover]);
    this._lastMotionStamp = null;
  }

  clearSelection() {
    this.selection = null;
    this._focusNodeIndices = [];
    this._syncGravityHold();
    this._updateRelations();
    if (this._savedCamera) {
      this._fly(this._savedCamera.position, this._savedCamera.target, 0, this._savedCamera.offset);
      this._savedCamera = null;
    } else if (this.uniforms) { this.uniforms.focus.value = 0; this._setViewOffset(0, 0); }
    this._buildLabels();
    this._schedule();
  }

  resetView() {
    this.selection = null;
    this._focusNodeIndices = [];
    this._syncGravityHold();
    this._savedCamera = null;
    this._updateRelations();
    if (this._ready && this.graph.nodes.length) { this._updateHomeFrame(); this._fly(this._homePosition, this._center, 0); }
    this._buildLabels();
  }

  _fly(position, target, focus, offset = { x: 0, y: 0 }) {
    this._flight = {
      fromPosition: this.camera.position.clone(), fromTarget: this.controls.target.clone(),
      position: position.clone(), target: target.clone(), fromFocus: this.uniforms.focus.value, focus,
      fromOffset: { ...this._viewOffset }, offset,
      started: performance.now(), duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 1 : 1050,
    };
    this._schedule();
  }

  _buildLabels() {
    this.labelLayer.replaceChildren();
    this._labels = [];
    if (!this._ready || !this.graph.nodes.length) return;
    const overview = this._relationCommunity < 0 ? [...this._gravity.hubs].slice(0,16)
      : this.graph.labelOrder.filter(index => this._gravity.communityIds[index] === this._relationCommunity).slice(0,12);
    const candidates = new Set([this._hover, ...(this._focusNodeIndices || []), ...(this.selection ? this._highlightNodes : overview)]);
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

  _hit(clientX, clientY, radius = 8) {
    if (!this._ready || !this.graph.nodes.length) return -1;
    this.camera.updateMatrixWorld();
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left, y = clientY - rect.top;
    let hit = -1, closest = radius * radius, depth = Infinity;
    for (let index = 0; index < this.graph.nodes.length; index++) {
      const point = this._scratch.fromBufferAttribute(this.positions,index).project(this.camera);
      if (point.z < -1 || point.z > 1) continue;
      const dx = (point.x*0.5+0.5)*rect.width-x, dy = (-point.y*0.5+0.5)*rect.height-y;
      const distance = dx*dx + dy*dy;
      if (distance < closest || (distance === closest && point.z < depth)) {
        hit = index; closest = distance; depth = point.z;
      }
    }
    return hit;
  }

  _clearHover() {
    if (this._hoverFrame !== null) cancelAnimationFrame(this._hoverFrame);
    this._hoverFrame = null;
    this._hover = -1;
    this._syncGravityHold();
    this._updateRelations();
    if (this.uniforms) this.uniforms.hover.value = -1;
    if (this.labelLayer) this._buildLabels();
    this._schedule();
  }

  _schedule() {
    if (!this._active || document.hidden || this._frame !== null) return;
    if (this._motionTimer !== null) clearTimeout(this._motionTimer);
    this._motionTimer = null;
    this._frame = requestAnimationFrame(this._render);
  }

  _render(now) {
    this._frame = null;
    if (!this._active || document.hidden) return;
    const orbiting = this.motionEnabled && this._ready && this.graph.nodes.length > 0;
    if (orbiting) {
      if (this._lastMotionStamp !== null) {
        // Limit physics catch-up after a stalled frame so interaction stays responsive.
        const seconds = Math.min(0.1, (now - this._lastMotionStamp) / 1000);
        if (this._gravity.movingCount > 0) {
          this._gravity.advance(seconds, this.positions.array);
          this.positions.needsUpdate = true;
          const phases = this._galaxyPhases.image.data;
          for (let community = 0; community < this._gravity.angles.length; community++) phases[community*4] = this._gravity.angles[community];
          this._galaxyPhases.needsUpdate = true;
        }
        this._pulseClock += seconds;
        this.uniforms.pulseTime.value = this._pulseClock;
      }
      this._lastMotionStamp = now;
    }
    if (this._flight) {
      const flight = this._flight;
      const t = Math.min(1, (now - flight.started) / flight.duration);
      const eased = t < 0.5 ? 16 * t ** 5 : 1 - (-2 * t + 2) ** 5 / 2;
      this.camera.position.lerpVectors(flight.fromPosition, flight.position, eased);
      this.controls.target.lerpVectors(flight.fromTarget, flight.target, eased);
      this.uniforms.focus.value = THREE.MathUtils.lerp(flight.fromFocus, flight.focus, eased);
      this._setViewOffset(THREE.MathUtils.lerp(flight.fromOffset.x, flight.offset.x, eased),
        THREE.MathUtils.lerp(flight.fromOffset.y, flight.offset.y, eased));
      if (t === 1) this._flight = null;
      else this._schedule();
    }
    this.controls.update();
    this._updateRelationLevel();
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
    if (orbiting && this._frame === null) {
      this._motionTimer = setTimeout(() => { this._motionTimer = null; this._schedule(); },
        Math.max(0, 1000 / 30 - (performance.now() - now)));
    }
  }

  _stopFrames() {
    if (this._frame !== null) cancelAnimationFrame(this._frame);
    if (this._hoverFrame !== null) cancelAnimationFrame(this._hoverFrame);
    if (this._motionTimer !== null) clearTimeout(this._motionTimer);
    this._frame = this._hoverFrame = null;
    this._motionTimer = this._lastMotionStamp = null;
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
    if (this._ready && this.graph.nodes.length) {
      if (!this.selection) this._fitScene();
      else {
        this._updateHomeFrame();
        this._flight = null;
        this.controls.target.copy(this._selectionCenter());
        const offset = this._focusViewOffset();
        this._setViewOffset(offset.x, offset.y);
        this.controls.update();
      }
    }
    this._schedule();
  }

  _disposeGraph() {
    for (const object of this.graphGroup.children) { object.geometry.dispose(); object.material.dispose(); }
    this.graphGroup.clear();
    this._galaxyPhases?.dispose();
    this._galaxyPhases = null;
    this.galaxyDust = this.communityLines = null;
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
      renderedCommunityLinks: this.communityLines?.visible ? this._bundleCount : 0,
      framePending: this._frame !== null, layoutReady: this._ready, animating: !!this._flight,
      motionEnabled: this.motionEnabled, movingNodes: this._gravity?.movingCount || 0,
      cameraDistance: this.camera.position.distanceTo(this.controls.target),
    };
  }
}
