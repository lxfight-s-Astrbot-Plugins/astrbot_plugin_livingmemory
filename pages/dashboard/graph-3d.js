import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { prepareGraph } from "./graph-3d-core.js";
import { MemoryGravity } from "./graph-3d-gravity.js";

export default class Graph3D {
  constructor() {
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
    this.tidalStreams = null;
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
    const starCount = 1600, wispCount = 420, dustCount = starCount + wispCount;
    const dust = new Float32Array(dustCount * 3), dustColors = new Float32Array(dustCount * 3);
    const dustSizes = new Float32Array(dustCount), dustAlphas = new Float32Array(dustCount);
    const starPalette = [
      new THREE.Color("#dbe6ff"),
      new THREE.Color("#ffe9cc"),
      new THREE.Color("#a8c2ff"),
      new THREE.Color("#f5f7ff"),
    ];
    const wispPalette = [
      new THREE.Color("#2f58a8"),
      new THREE.Color("#1f6e8c"),
      new THREE.Color("#4f4696"),
      new THREE.Color("#3b739e"),
    ];
    for (let i = 0; i < dustCount; i++) {
      const isWisp = i >= starCount;
      const angle = i * 2.3999632297, h = 1 - 2 * (i + 0.5) / dustCount;
      const seed = (i * 0.61803398875) % 1;
      const radius = isWisp ? 900 + seed * 2600 : 1400 + (i % 29) * 105;
      const circle = Math.sqrt(1 - h * h);
      dust.set([Math.cos(angle) * circle * radius, h * radius, Math.sin(angle) * circle * radius], i * 3);
      if (isWisp) {
        wispPalette[i % wispPalette.length].toArray(dustColors, i * 3);
        dustSizes[i] = 18 + seed * 28;
        dustAlphas[i] = 0.032;
      } else {
        starPalette[i % starPalette.length].toArray(dustColors, i * 3);
        dustSizes[i] = 1.3 + seed ** 2 * 2.5;
        dustAlphas[i] = 0.36;
      }
    }
    const dustGeometry = new THREE.BufferGeometry();
    dustGeometry.setAttribute("position", new THREE.BufferAttribute(dust, 3));
    dustGeometry.setAttribute("color", new THREE.BufferAttribute(dustColors, 3));
    dustGeometry.setAttribute("aSize", new THREE.BufferAttribute(dustSizes, 1));
    dustGeometry.setAttribute("aAlpha", new THREE.BufferAttribute(dustAlphas, 1));
    this.dust = new THREE.Points(
      dustGeometry,
      new THREE.ShaderMaterial({
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
        uniforms: { dpr: { value: this.renderer.getPixelRatio() } },
        vertexShader: `attribute vec3 color; attribute float aSize; attribute float aAlpha; uniform float dpr;
          varying vec3 vColor; varying float vAlpha;
          void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);gl_PointSize=aSize*dpr;vColor=color;vAlpha=aAlpha;}`,
        fragmentShader: `varying vec3 vColor; varying float vAlpha;
          void main(){float r=length(gl_PointCoord-.5)*2.;if(r>1.)discard;
            float falloff=exp(-r*r*mix(2.6,5.5,step(.15,vAlpha)));
            gl_FragColor=vec4(vColor,falloff*(1.-r*r)*vAlpha);#include <colorspace_fragment>}`,
      })
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
    const nodeColors = window.GraphShared.readNodeColors();
    this.graph.nodes.forEach((node, i) => {
      color.set(nodeColors[node.type] || nodeColors.other).toArray(colors, i * 3);
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
        vAlpha=max(hovered,mix(1.,mix(.09,1.,linked),focus))*(1.-smoothstep(1.4,4.6,-p.z/viewDistance));}`,
      fragmentShader: `varying vec3 vColor; varying float vAlpha; varying float vStar; varying float vCore; varying float vPulsar; varying float vEmission; varying float vRing; varying vec2 vBeam;
        void main(){vec2 point=gl_PointCoord-.5;float r=length(point)*2.;
        if(r>1.)discard;float core=exp(-r*r*48.);float glow=exp(-r*r*10.)*.13;
        float corona=vStar*exp(-r*r*3.)*.075;
        float spike=max(0.,vStar-.35)*(exp(-abs(point.x)*42.)*exp(-abs(point.y)*5.)+exp(-abs(point.y)*42.)*exp(-abs(point.x)*5.))*.18;
        float emission=0.;
        if(vPulsar>0.){float beam=exp(-abs(dot(point,vec2(-vBeam.y,vBeam.x)))*55.)*exp(-r*2.5);
          float ring=exp(-pow((r-vRing)*35.,2.))*.12*(1.-vRing);
          emission=vEmission*(beam*.25+ring)+vPulsar*exp(-r*r*2.8)*.025;}
        vec3 tint=mix(vColor,vec3(.96,.98,1.),core*.65);tint=mix(tint,vec3(.86,.94,1.),min(.45,emission*.6));
        gl_FragColor=vec4(tint,(core*vCore+glow+corona+spike+emission)*vAlpha);
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
      new THREE.LineBasicMaterial({ color: "#9fb4f0", transparent: true, depthWrite: false }));
    this.graphGroup.add(this.edges);
    const focusGeometry = new THREE.BufferGeometry().setAttribute("position", this.positions);
    focusGeometry.setIndex([]);
    this.focusEdges = new THREE.LineSegments(focusGeometry,
      new THREE.LineBasicMaterial({ color: "#d6e2ff", transparent: true, opacity: 0.6, depthWrite: false }));
    this.graphGroup.add(this.focusEdges);
    this._updateRelations();
  }

  _buildGalaxyDust(data) {
    if (!this.graph.nodes.length) return;
    const count = Math.min(28000, Math.max(800, this.graph.nodes.length * 14));
    const positions = new Float32Array(count * 3), centers = new Float32Array(count * 3), spins = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3), sizes = new Float32Array(count), systems = new Float32Array(count), haze = new Float32Array(count);
    const palettes = {
      0: [new THREE.Color("#b2a6ff"), new THREE.Color("#6fe8d8"), new THREE.Color("#7caeff")],
      1: [new THREE.Color("#ffca8e"), new THREE.Color("#9db6ff"), new THREE.Color("#63b3ff")],
      2: [new THREE.Color("#ffba7a"), new THREE.Color("#b4b8ff"), new THREE.Color("#7ac2ff")],
      3: [new THREE.Color("#ffd4be"), new THREE.Color("#c4a6ff"), new THREE.Color("#7caeff")],
      4: [new THREE.Color("#ffd29c"), new THREE.Color("#8eb4ff"), new THREE.Color("#66e3ff")],
      5: [new THREE.Color("#fff0d0"), new THREE.Color("#f5b878"), new THREE.Color("#d48f78")],
    };
    const coreTint = new THREE.Color("#fff1d6"), nebulaRose = new THREE.Color("#f5a8cb"), tint = new THREE.Color();
    for (let i = 0; i < count; i++) {
      const node = i % this.graph.nodes.length, community = data.communityIds[node], center = data.hubs[community] * 3;
      const radius = data.communityRadii[community], frame = community * 9;
      const gType = data.galaxyTypes ? data.galaxyTypes[community] : 1;
      const isDisk = gType >= 1 && gType <= 4;
      const seed = (i * 0.61803398875) % 1;
      const u1 = ((i * 0.75487766625 + 0.19) % 1) * 2 - 1;
      const u2 = ((i * 0.41421356237 + 0.47) % 1) * 2 - 1;
      const u3 = ((i * 0.73205080757 + 0.83) % 1) * 2 - 1;
      const bellW = (u1 + u2 + u3) / 3;
      const offset = i * 3;
      const ux = data.frames[frame], uy = data.frames[frame + 1], uz = data.frames[frame + 2];
      const vx = data.frames[frame + 3], vy = data.frames[frame + 4], vz = data.frames[frame + 5];
      const wx = data.frames[frame + 6], wy = data.frames[frame + 7], wz = data.frames[frame + 8];
      const hx = data.positions[center], hy = data.positions[center + 1], hz = data.positions[center + 2];
      const [warm, mid, cool] = palettes[gType] || palettes[1];
      if (isDisk && i % 7 === 0) {
        // Galactic bulge: compact oblate spheroid (elongated bar for barred spirals) of warm core starlight.
        const angle = i * 2.3999632297;
        const bulgeFrac = Math.pow(seed, 1.35);
        const bulgeR = radius * (gType === 2 ? 0.28 : 0.23) * bulgeFrac;
        const bulgeW = radius * 0.044 * bellW * (1 - 0.5 * bulgeFrac);
        const ca = Math.cos(angle) * bulgeR * (gType === 2 ? 1.25 : 1);
        const sa = Math.sin(angle) * bulgeR * (gType === 2 ? 0.55 : 1);
        positions.set([
          hx + ux * ca + vx * sa + wx * bulgeW,
          hy + uy * ca + vy * sa + wy * bulgeW,
          hz + uz * ca + vz * sa + wz * bulgeW,
        ], offset);
        tint.copy(coreTint).lerp(warm, Math.min(1, bulgeFrac * 1.1)).toArray(colors, offset);
        haze[i] = i % 21 === 0 ? 1 : 0;
        sizes[i] = haze[i] ? 32 + seed * 24 : 1.1 + seed ** 2 * 2.2;
      } else {
        const nx = data.positions[node * 3], ny = data.positions[node * 3 + 1], nz = data.positions[node * 3 + 2];
        const dx = nx - hx, dy = ny - hy, dz = nz - hz;
        const distNorm = Math.min(1, Math.hypot(dx, dy, dz) / radius);
        if (isDisk) {
          // Align disk dust along the spiral/ring tangent while keeping the galactic midplane thin.
          const lx = dx * ux + dy * uy + dz * uz, ly = dx * vx + dy * vy + dz * vz;
          const len = Math.hypot(lx, ly);
          const rx = len > 1e-4 ? lx / len : Math.cos(i * 2.3999632297);
          const ry = len > 1e-4 ? ly / len : Math.sin(i * 2.3999632297);
          const v1 = ((i * 0.30901699437 + 0.29) % 1) * 2 - 1;
          const v2 = ((i * 0.86602540378 + 0.61) % 1) * 2 - 1;
          const bellR = (u1 - u2) * 0.5, bellT = (v1 + v2 + u3) / 3;
          const dr = bellR * radius * (gType === 3 ? 0.022 + 0.044 * seed : 0.014 + 0.030 * seed);
          const dt = bellT * radius * (0.028 + 0.075 * seed);
          const dw = bellW * radius * (0.005 + 0.013 * seed * (1 - 0.35 * distNorm));
          const du = dr * rx - dt * ry, dv = dr * ry + dt * rx;
          positions.set([
            nx + ux * du + vx * dv + wx * dw,
            ny + uy * du + vy * dv + wy * dw,
            nz + uz * du + vz * dv + wz * dw,
          ], offset);
        } else {
          // Elliptical galaxies and star clusters retain soft 3D volumetric starlight and nebula mist.
          const angle = i * 2.3999632297, height = 1 - 2 * (i + 0.5) / count, circle = Math.sqrt(1 - height * height);
          const spread = radius * (gType === 5 ? 0.03 + 0.09 * seed * seed : 0.02 + 0.07 * seed * seed);
          positions.set([nx + Math.cos(angle) * circle * spread,
            ny + Math.sin(angle) * circle * spread, nz + height * spread], offset);
        }
        if (distNorm < 0.38) tint.copy(warm).lerp(mid, distNorm / 0.38);
        else tint.copy(mid).lerp(cool, (distNorm - 0.38) / 0.62);
        if (gType !== 5 && (this.graph.nodes[node].type === "summary" || this.graph.nodes[node].pulsar > 0)) tint.lerp(nebulaRose, 0.26);
        tint.toArray(colors, offset);
        const hazeStride = gType === 3 || gType === 0 ? 9 : 13;
        haze[i] = i % hazeStride === 0 ? 1 : 0;
        sizes[i] = haze[i] ? 24 + seed * 24 : 1.1 + seed ** 3 * 2.5;
      }
      centers.set([hx, hy, hz], offset);
      spins.set([wx, wy, wz], offset);
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
          gl_PointSize=clamp(aSize*dpr*pow(abs(viewDistance/view.z),.6),.8,90.*dpr);
          vColor=color;vHaze=aHaze;vDepth=1.-smoothstep(1.5,5.,-view.z/viewDistance);}`,
      fragmentShader: `uniform float uOpacity;varying vec3 vColor;varying float vHaze;varying float vDepth;
        void main(){float r=length(gl_PointCoord-.5)*2.;if(r>1.)discard;
          float edge=1.-r*r;
          float light=mix(exp(-r*r*8.5)*.22,exp(-r*r*3.6)*edge*.026,vHaze);
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
    const positions = [], colors = [], color = new THREE.Color("#8fa6e8");
    const streamCount = this._bundleCount * 42;
    const streamPositions = new Float32Array(streamCount * 3), streamColors = new Float32Array(streamCount * 3);
    const streamSizes = new Float32Array(streamCount), streamHaze = new Float32Array(streamCount);
    const streamWarm = new THREE.Color("#d9c8ff"), streamCool = new THREE.Color("#73a8ff"), streamTint = new THREE.Color();
    let streamIdx = 0;
    const segments = 16;
    for (let i = 0; i < this._bundleCount; i++) {
      const source = data.bundlePairs[i*2], target = data.bundlePairs[i*2+1];
      const a = new THREE.Vector3().fromBufferAttribute(this.positions, data.hubs[source]);
      const b = new THREE.Vector3().fromBufferAttribute(this.positions, data.hubs[target]);
      const dir = b.clone().sub(a);
      const normal = dir.clone().cross(new THREE.Vector3(0.3,0.8,0.5)).normalize();
      const binormal = dir.clone().cross(normal).normalize();
      const bend = Math.min(a.distanceTo(b)*0.14, (data.communityRadii[source]+data.communityRadii[target])*0.3);
      const curve = new THREE.CubicBezierCurve3(a, a.clone().lerp(b,0.32).addScaledVector(normal,bend),
        a.clone().lerp(b,0.68).addScaledVector(normal,bend), b);
      const intensity = 0.35 + 0.65 * Math.log1p(data.bundleWeights[i]) / Math.log1p(data.bundleWeights[0]);
      for (let segment = 0; segment < segments; segment++) {
        const t0 = segment / segments, t1 = (segment + 1) / segments;
        positions.push(...curve.getPoint(t0).toArray(), ...curve.getPoint(t1).toArray());
        for (const t of [t0, t1]) {
          const arcFade = (0.35 + 0.65 * Math.sin(Math.PI * t)) * intensity;
          colors.push(color.r * arcFade, color.g * arcFade, color.b * arcFade);
        }
      }
      // Populate tidal star streams and faint cosmic filaments bridging connected galaxies.
      const span = a.distanceTo(b);
      for (let s = 0; s < 42; s++) {
        const idx = i * 42 + s;
        const t = 0.08 + 0.84 * ((idx * 0.61803398875) % 1);
        const pt = curve.getPoint(t);
        const scatterAngle = idx * 2.3999632297;
        const spread = Math.min(48, span * 0.055) * (((idx * 0.75487766625) % 1) ** 1.4);
        pt.addScaledVector(normal, Math.cos(scatterAngle) * spread);
        pt.addScaledVector(binormal, Math.sin(scatterAngle) * spread);
        streamPositions.set([pt.x, pt.y, pt.z], streamIdx * 3);
        streamTint.copy(streamWarm).lerp(streamCool, Math.abs(t - 0.5) * 2).multiplyScalar(0.65 + 0.35 * intensity).toArray(streamColors, streamIdx * 3);
        streamHaze[streamIdx] = s % 6 === 0 ? 1 : 0;
        streamSizes[streamIdx] = streamHaze[streamIdx] ? 22 + ((idx * 0.41421356) % 1) * 18 : 1.3 + ((idx * 0.7320508) % 1) * 1.8;
        streamIdx++;
      }
    }
    const geometry = new THREE.BufferGeometry().setAttribute("position",new THREE.Float32BufferAttribute(positions,3));
    geometry.setAttribute("color",new THREE.Float32BufferAttribute(colors,3));
    this.communityLines = new THREE.LineSegments(geometry,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.22, depthWrite: false }));
    this.graphGroup.add(this.communityLines);
    const streamGeometry = new THREE.BufferGeometry();
    streamGeometry.setAttribute("position", new THREE.BufferAttribute(streamPositions, 3));
    streamGeometry.setAttribute("color", new THREE.BufferAttribute(streamColors, 3));
    streamGeometry.setAttribute("aSize", new THREE.BufferAttribute(streamSizes, 1));
    streamGeometry.setAttribute("aHaze", new THREE.BufferAttribute(streamHaze, 1));
    streamGeometry.boundingSphere = this.points.geometry.boundingSphere;
    this.tidalStreams = new THREE.Points(streamGeometry, new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uOpacity: { value: 1 }, dpr: this.uniforms.dpr, viewDistance: this.uniforms.viewDistance },
      vertexShader: `attribute vec3 color; attribute float aSize; attribute float aHaze;
        uniform float dpr; uniform float viewDistance; varying vec3 vColor; varying float vHaze;
        void main(){vec4 view=modelViewMatrix*vec4(position,1.);gl_Position=projectionMatrix*view;
          gl_PointSize=clamp(aSize*dpr*pow(abs(viewDistance/view.z),.6),.8,65.*dpr);vColor=color;vHaze=aHaze;}`,
      fragmentShader: `uniform float uOpacity; varying vec3 vColor; varying float vHaze;
        void main(){float r=length(gl_PointCoord-.5)*2.;if(r>1.)discard;
          float light=mix(exp(-r*r*7.5)*.26,exp(-r*r*3.4)*(1.-r*r)*.045,vHaze);
          gl_FragColor=vec4(vColor,light*uOpacity);#include <colorspace_fragment>}`,
    }));
    this.graphGroup.add(this.tidalStreams);
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
    this.dust?.scale.setScalar(Math.max(1, this._radius / 1800));
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
      this.communityLines.material.opacity = this.selection ? 0.08 : this._relationCommunity >= 0 ? 0.14 : 0.22;
    }
    if (this.tidalStreams) {
      this.tidalStreams.visible = !this.showAllRelations;
      this.tidalStreams.material.uniforms.uOpacity.value = this.selection ? 0.25 : this._relationCommunity >= 0 ? 0.55 : 1;
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
      started: performance.now(), duration: 1050,
    };
    this._schedule();
  }

  _buildLabels() {
    this.labelLayer.replaceChildren();
    this._labels = [];
    if (!this._ready || !this.graph.nodes.length) return;
    const hubSet = new Set(this._gravity?.hubs || []);
    const focusSet = new Set(this._focusNodeIndices || []);
    const overview = this._relationCommunity < 0 ? [...hubSet].slice(0,16)
      : this.graph.labelOrder.filter(index => this._gravity.communityIds[index] === this._relationCommunity).slice(0,12);
    const candidates = new Set([this._hover, ...focusSet, ...(this.selection ? this._highlightNodes : overview)]);
    for (const index of candidates) {
      if (index < 0) continue;
      if (this._labels.length >= 24) break;
      const node = this.graph.nodes[index];
      const element = document.createElement("span");
      const role = index === this._hover || focusSet.has(index) ? " is-focus" : hubSet.has(index) ? " is-hub" : "";
      element.className = "space-node-label" + role;
      if (node.type && element.dataset) element.dataset.type = node.type;
      element.textContent = node.label || node.canonical_value || "";
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
    this.galaxyDust = this.communityLines = this.tidalStreams = null;
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
    this.renderer.forceContextLoss();
    this.container.replaceChildren();
  }
}
