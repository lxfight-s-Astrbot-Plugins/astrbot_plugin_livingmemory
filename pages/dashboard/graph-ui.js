(() => {
  "use strict";

  /* ================================================================
     State
     ================================================================ */
  const state = {
    payload: null,
    graphIndex: null,
    selectedNodeId: null,
    selectedMemoryId: null,
    isLoading: false,
    hasLoadedOverview: false,
    graph3d: null,
    resizeObserver: null,
    scenePositions: new Map(),
    sceneReady: false,
    focusTimer: null,
    resumeFrame: null,
  };

  const dom = {};

  /* Node type config */
  const NODE_TYPE_LABELS = {
    get topic() { return window.t("graph.nodeTopic"); },
    get person() { return window.t("graph.nodePerson"); },
    get fact() { return window.t("graph.nodeFact"); },
    get summary() { return window.t("graph.nodeSummary"); },
  };

  const NODE_TYPE_COLORS = {
    topic: "#7950f2", person: "#20c997", fact: "#fcc419",
    summary: "#f06595", other: "#909296",
  };

  const TYPE_ORBITS = {
    summary: { inner: 32, outer: 118, phase: 0.18, polarStart: 0.24, polarEnd: 0.78 },
    topic: { inner: 160, outer: 245, phase: 1.08, polarStart: 0.28, polarEnd: 0.8 },
    person: { inner: 212, outer: 306, phase: 2.28, polarStart: 0.22, polarEnd: 0.7 },
    fact: { inner: 254, outer: 354, phase: 3.74, polarStart: 0.32, polarEnd: 0.84 },
    other: { inner: 220, outer: 320, phase: 5.08, polarStart: 0.2, polarEnd: 0.82 },
  };

  const RELATION_PALETTE = [
    "#38bdf8","#818cf8","#f59e0b","#10b981","#f472b6","#22d3ee","#fb7185","#a78bfa",
  ];

  /* ================================================================
     Bridge helpers
     ================================================================ */
  function buildEndpoint(path) {
    return ("page/" + String(path).replace(/^\/+/, "")).replace(/\/+/g, "/");
  }

  async function requestGraph(path, options) {
    options = options || {};
    var bridge = window.AstrBotPluginPage;
    if (!bridge) throw new Error(window.t("graph.bridgeError"));

    var method = (options.method || "GET").toUpperCase();
    if (method === "GET") {
      var qi = path.indexOf("?");
      if (qi !== -1) {
        var base = path.substring(0, qi);
        var qs = path.substring(qi + 1);
        var params = {};
        new URLSearchParams(qs).forEach(function(v, k) { params[k] = v; });
        return await bridge.apiGet(buildEndpoint(base), params);
      }
      return await bridge.apiGet(buildEndpoint(path), {});
    }
    return await bridge.apiPost(buildEndpoint(path), options.body || {});
  }

  /* ================================================================
     Init
     ================================================================ */
  function init() {
    dom.queryInput = document.getElementById("graph-query-input");
    dom.sessionInput = document.getElementById("graph-session-filter");
    dom.memoryInput = document.getElementById("graph-memory-id");
    dom.searchButton = document.getElementById("graph-search-btn");
    dom.focusButton = document.getElementById("graph-focus-btn");
    dom.overviewButton = document.getElementById("graph-overview-btn");
    dom.legend = document.getElementById("graph-legend");
    dom.canvas = document.getElementById("graph-canvas");
    dom.canvasState = document.getElementById("graph-canvas-state");

    if (!dom.canvas) return;

    dom.searchButton.addEventListener("click", runQuery);
    dom.focusButton.addEventListener("click", focusMemory);
    dom.overviewButton.addEventListener("click", fetchOverview);

    dom.queryInput.addEventListener("keydown", function(e) {
      if (e.key === "Enter") { e.preventDefault(); runQuery(); }
    });
    dom.memoryInput.addEventListener("keydown", function(e) {
      if (e.key === "Enter") { e.preventDefault(); focusMemory(); }
    });

    initResizeObserver();
    setCanvasMessage(
      typeof window.ForceGraph3D === "function"
        ? window.t("graph.canvasDefault")
        : window.t("graph.canvasNo3D"),
      false
    );

    /* Auto-load overview on page */
    setTimeout(function() {
      if (!state.hasLoadedOverview && !state.isLoading) {
        fetchOverview();
      }
    }, 100);
  }

  /* Expose for app.js lazy-load */
  window.ensureGraphScene = function() {
    if (!state.hasLoadedOverview && !state.isLoading) fetchOverview();
  };

  function initResizeObserver() {
    window.addEventListener("resize", resizeGraphScene, { passive: true });
    if (typeof window.ResizeObserver !== "function" || !dom.canvas) return;
    state.resizeObserver = new ResizeObserver(function() { resizeGraphScene(); });
    state.resizeObserver.observe(dom.canvas);
  }

  /* ================================================================
     Data Fetching
     ================================================================ */
  function getFilters() {
    return {
      session_id: dom.sessionInput ? dom.sessionInput.value.trim() || null : null,
    };
  }

  async function fetchOverview() {
    setLoading(true);
    try {
      var filters = getFilters();
      var params = new URLSearchParams();
      if (filters.session_id) params.set("session_id", filters.session_id);
      var qs = params.toString();
      var payload = await requestGraph("/graph/overview" + (qs ? "?" + qs : ""));
      state.hasLoadedOverview = true;
      renderPayload(payload, { focusSelection: !state.sceneReady });
      if (window.lmFetchGraphStats) window.lmFetchGraphStats();
    } catch (e) {
      setCanvasMessage(e.message || window.t("graph.errorFetch"), false);
    } finally {
      setLoading(false);
    }
  }

  async function runQuery() {
    var query = dom.queryInput.value.trim();
    if (!query) { fetchOverview(); return; }

    setLoading(true);
    try {
      var filters = getFilters();
      var payload = await requestGraph("/graph/query", {
        method: "POST",
        body: { query: query, session_id: filters.session_id },
      });
      renderPayload(payload, { focusSelection: true });
    } catch (e) {
      setCanvasMessage(e.message || window.t("graph.queryFail"), false);
    } finally {
      setLoading(false);
    }
  }

  async function focusMemory() {
    var text = dom.memoryInput.value.trim();
    if (!text) { setCanvasMessage(window.t("graph.focusEmpty"), false); return; }
    var memoryId = Number.parseInt(text, 10);
    if (Number.isNaN(memoryId)) { setCanvasMessage(window.t("graph.focusNotInt"), false); return; }

    setLoading(true);
    try {
      var filters = getFilters();
      var payload = await requestGraph("/graph/query", {
        method: "POST",
        body: { memory_id: memoryId, session_id: filters.session_id },
      });
      renderPayload(payload, { memoryId: memoryId, focusSelection: true });
    } catch (e) {
      setCanvasMessage(e.message || window.t("graph.focusFail"), false);
    } finally {
      setLoading(false);
    }
  }

  /* ================================================================
     Render Pipeline
     ================================================================ */
  function setLoading(loading) {
    state.isLoading = loading;
    if (dom.searchButton) dom.searchButton.disabled = loading;
    if (dom.focusButton) dom.focusButton.disabled = loading;
    if (dom.overviewButton) dom.overviewButton.disabled = loading;
  }

  function renderPayload(payload, options) {
    options = options || {};
    state.payload = payload;
    state.graphIndex = buildGraphIndex(payload.snapshot || {});

    if (options.memoryId) {
      state.selectedMemoryId = options.memoryId;
      state.selectedNodeId = null;
    } else {
      ensureSelection(payload);
    }

    if (!payload.enabled) {
      clearGraphScene();
      setCanvasMessage(window.t("graph.disabledCanvas"), false);
      renderLegend(payload);
      return;
    }

    renderLegend(payload);
    renderCanvas(payload, {
      focusSelection: options.focusSelection === undefined
        ? payload.mode === "query" || payload.mode === "memory_focus" || !state.sceneReady
        : Boolean(options.focusSelection),
    });
    renderSelectionPanels(payload);
    if (window.lmFetchGraphStats) window.lmFetchGraphStats();
  }

  /* ================================================================
     Selection Logic
     ================================================================ */
  function ensureSelection(payload) {
    if (state.graphIndex && state.graphIndex.nodeMap.has(state.selectedNodeId)) {
      state.selectedMemoryId = null; return;
    }
    if (state.graphIndex && state.graphIndex.memoryMap.has(state.selectedMemoryId)) {
      state.selectedNodeId = null; return;
    }

    state.selectedNodeId = null;
    state.selectedMemoryId = null;

    var matchedNodeIds = payload.matched_node_ids || [];
    var firstNode = matchedNodeIds.find(function(id) {
      return state.graphIndex && state.graphIndex.nodeMap.has(id);
    });
    if (firstNode !== undefined) { state.selectedNodeId = firstNode; return; }

    var firstRetrieved = payload.retrieval && payload.retrieval.items && payload.retrieval.items[0];
    if (firstRetrieved && state.graphIndex && state.graphIndex.memoryMap.has(firstRetrieved.memory_id)) {
      state.selectedMemoryId = firstRetrieved.memory_id; return;
    }

    var topNodes = payload.top_nodes || [];
    if (topNodes.length && state.graphIndex && state.graphIndex.nodeMap.has(topNodes[0].id)) {
      state.selectedNodeId = topNodes[0].id; return;
    }

    var snapMemories = (payload.snapshot && payload.snapshot.memories) || [];
    if (snapMemories.length && state.graphIndex && state.graphIndex.memoryMap.has(snapMemories[0].memory_id)) {
      state.selectedMemoryId = snapMemories[0].memory_id;
    }
  }

  function clearSelection() {
    if (!state.payload) return;
    state.selectedNodeId = null;
    state.selectedMemoryId = null;
    renderCanvas(state.payload, { focusSelection: false });
    renderSelectionPanels(state.payload);
    if (window.lmClosePeek) window.lmClosePeek();
  }

  function selectNode(nodeId, options) {
    options = options || {};
    if (!state.graphIndex || !state.graphIndex.nodeMap.has(nodeId)) return;
    state.selectedNodeId = nodeId;
    state.selectedMemoryId = null;
    renderCanvas(state.payload, { focusSelection: Boolean(options.focusCamera) });

    /* Show in peek panel */
    var node = state.graphIndex.nodeMap.get(nodeId);
    if (window.lmOpenPeekNode && node) window.lmOpenPeekNode(node);
  }

  function selectMemory(memoryId, options) {
    options = options || {};
    if (!state.graphIndex || !state.graphIndex.memoryMap.has(memoryId)) return;
    state.selectedMemoryId = memoryId;
    state.selectedNodeId = null;
    renderCanvas(state.payload, { focusSelection: Boolean(options.focusCamera) });

    /* Show in peek panel */
    var memory = state.graphIndex.memoryMap.get(memoryId);
    if (window.lmState && memory) {
      var item = {
        memory_id: memoryId,
        summary: memory.summary || memory.content || "",
        content: memory.content || memory.summary || "",
        memory_type: memory.type || "",
        importance: memory.importance,
        status: memory.status || "active",
        raw: memory,
      };
      if (window.lmOpenPeekMemory) window.lmOpenPeekMemory(item);
    }
  }

  /* ================================================================
     Selection Panels (now using peek panel)
     ================================================================ */
  function renderSelectionPanels(payload) {
    /* Selection is handled by the canvas click events -> peek panel */
    /* No separate top/related/retrieval panels in new layout */
  }

  /* ================================================================
     Legend
     ================================================================ */
  function renderLegend(payload) {
    if (!dom.legend) return;
    var summary = payload.summary || {};
    var nodeTypes = summary.node_type_breakdown || {};
    var relTypes = summary.relation_breakdown || {};

    var chips = Object.entries(nodeTypes).sort(function(a, b) { return b[1] - a[1]; })
      .map(function(e) {
        return '<span class="legend-chip"><span class="dot" style="background:' +
          (NODE_TYPE_COLORS[e[0]] || NODE_TYPE_COLORS.other) + '"></span>' +
          typeLabel(e[0]) + ' &middot; ' + e[1] + '</span>';
      });

    var rchips = Object.entries(relTypes).sort(function(a, b) { return b[1] - a[1]; }).slice(0, 4)
      .map(function(e) {
        return '<span class="legend-chip">' + relationLabel(e[0]) + ' &middot; ' + e[1] + '</span>';
      });

    dom.legend.innerHTML = chips.concat(rchips).join("") ||
      '<span class="legend-chip">No connections</span>';
  }

  /* ================================================================
     Graph Index
     ================================================================ */
  function buildGraphIndex(snapshot) {
    var nodes = snapshot.nodes || [];
    var edges = snapshot.edges || [];
    var entries = snapshot.entries || [];
    var memories = snapshot.memories || [];

    var nodeMap = new Map(nodes.map(function(n) { return [Number(n.id), n]; }));
    var memoryMap = new Map(memories.map(function(m) { return [Number(m.memory_id), m]; }));
    var memoryToEntries = new Map();
    var memoryToNodes = new Map();
    var nodeToMemories = new Map();
    var nodeToEntries = new Map();
    var neighborMap = new Map();

    function ensureSet(map, key) {
      if (!map.has(key)) map.set(key, new Set());
      return map.get(key);
    }

    entries.forEach(function(entry) {
      var mId = Number(entry.memory_id);
      if (!memoryToEntries.has(mId)) memoryToEntries.set(mId, []);
      memoryToEntries.get(mId).push(entry);
      (entry.node_ids || []).forEach(function(nId) {
        var nodeId = Number(nId);
        ensureSet(memoryToNodes, mId).add(nodeId);
        ensureSet(nodeToMemories, nodeId).add(mId);
        if (!nodeToEntries.has(nodeId)) nodeToEntries.set(nodeId, []);
        nodeToEntries.get(nodeId).push(entry);
      });
    });

    edges.forEach(function(edge) {
      var s = Number(edge.source);
      var t = Number(edge.target);
      var mId = Number(edge.memory_id);
      ensureSet(memoryToNodes, mId).add(s);
      ensureSet(memoryToNodes, mId).add(t);
      ensureSet(nodeToMemories, s).add(mId);
      ensureSet(nodeToMemories, t).add(mId);
      ensureSet(neighborMap, s).add(t);
      ensureSet(neighborMap, t).add(s);
    });

    return {
      nodeMap: nodeMap, memoryMap: memoryMap,
      memoryToEntries: memoryToEntries, memoryToNodes: memoryToNodes,
      nodeToMemories: nodeToMemories, nodeToEntries: nodeToEntries,
      neighborMap: neighborMap, edges: edges, entries: entries,
    };
  }

  /* ================================================================
     Canvas Rendering
     ================================================================ */
  function renderCanvas(payload, options) {
    options = options || {};
    var nodes = (payload.snapshot && payload.snapshot.nodes) || [];
    if (!nodes.length) {
      state.sceneReady = false;
      clearGraphScene();
      setCanvasMessage("No visible graph data", false);
      return;
    }

    if (!ensureGraphScene()) {
      state.sceneReady = false;
      return;
    }

    captureScenePositions();
    var sceneData = buildGraphSceneData(payload);
    state.graph3d.graphData(sceneData);

    if (state.resumeFrame !== null) window.cancelAnimationFrame(state.resumeFrame);
    state.resumeFrame = window.requestAnimationFrame(function() {
      if (!state.graph3d) return;
      if (typeof state.graph3d.d3ReheatSimulation === "function") state.graph3d.d3ReheatSimulation();
      if (typeof state.graph3d.resumeAnimation === "function") state.graph3d.resumeAnimation();
      state.resumeFrame = null;
    });

    resizeGraphScene();
    state.sceneReady = true;
    setCanvasMessage("", false);

    if (options.focusSelection) queueCameraFocus();
  }

  function setCanvasMessage(msg, loading) {
    if (!dom.canvasState) return;
    dom.canvasState.textContent = msg || "";
  }

  function clearGraphScene() {
    window.clearTimeout(state.focusTimer);
    if (state.resumeFrame !== null) { window.cancelAnimationFrame(state.resumeFrame); state.resumeFrame = null; }
    if (state.graph3d && typeof state.graph3d.graphData === "function") {
      if (typeof state.graph3d.pauseAnimation === "function") state.graph3d.pauseAnimation();
      state.graph3d.graphData({ nodes: [], links: [] });
    }
  }

  function resizeGraphScene() {
    if (!state.graph3d) return;
    var w = Math.max(360, Math.floor(dom.canvas.clientWidth || 960));
    var h = Math.max(440, Math.floor(dom.canvas.clientHeight || 620));
    state.graph3d.width(w).height(h);
  }

  function captureScenePositions() {
    if (!state.graph3d || typeof state.graph3d.graphData !== "function") return;
    var data = state.graph3d.graphData();
    var nodes = data && data.nodes ? data.nodes : [];
    nodes.forEach(function(node) {
      var id = Number(node.id);
      if (Number.isFinite(id) && Number.isFinite(node.x) && Number.isFinite(node.y) && Number.isFinite(node.z)) {
        state.scenePositions.set(id, {
          x: node.x, y: node.y, z: node.z,
          vx: Number.isFinite(node.vx) ? node.vx : 0,
          vy: Number.isFinite(node.vy) ? node.vy : 0,
          vz: Number.isFinite(node.vz) ? node.vz : 0,
        });
      }
    });
  }

  /* ================================================================
     3D Graph Engine
     ================================================================ */
  function ensureGraphScene() {
    if (state.graph3d) return true;
    if (typeof window.ForceGraph3D !== "function" || !dom.canvas) return false;

    dom.canvas.innerHTML = "";
    var graph = window.ForceGraph3D()(dom.canvas)
      .backgroundColor("#050816")
      .showNavInfo(false)
      .nodeRelSize(3.8)
      .nodeVal(function(node) { return node.__size; })
      .nodeColor(function(node) { return node.__color; })
      .nodeLabel(function(node) { return node.__label; })
      .nodeOpacity(0.98)
      .nodeResolution(20)
      .linkColor(function(link) { return link.__color; })
      .linkWidth(function(link) { return link.__width; })
      .linkOpacity(0.76)
      .linkResolution(10)
      .linkCurvature(function(link) { return link.__curvature; })
      .linkLabel(function(link) { return link.__label; })
      .linkDirectionalArrowLength(function(link) { return link.__arrowLength; })
      .linkDirectionalArrowColor(function(link) { return link.__particleColor; })
      .linkDirectionalParticles(function(link) { return link.__particles; })
      .linkDirectionalParticleWidth(function(link) { return link.__particleWidth; })
      .linkDirectionalParticleColor(function(link) { return link.__particleColor; })
      .linkDirectionalParticleSpeed(function(link) { return link.__particleSpeed; })
      .onNodeClick(function(node) { selectNode(Number(node.id), { focusCamera: true }); })
      .onBackgroundClick(function() { clearSelection(); })
      .warmupTicks(90)
      .cooldownTime(2200)
      .onEngineStop(function() { captureScenePositions(); });

    if (typeof graph.pauseAnimation === "function") graph.pauseAnimation();

    try {
      var charge = graph.d3Force("charge");
      if (charge && typeof charge.strength === "function") charge.strength(-260);
    } catch (e) { console.warn("Graph charge force config failed", e); }

    try {
      var controls = graph.controls();
      if (controls) {
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.autoRotate = true;
        controls.autoRotateSpeed = 0.35;
        controls.minDistance = 80;
        controls.maxDistance = 1600;
      }
    } catch (e) { console.warn("Graph controls config failed", e); }

    try {
      var renderer = graph.renderer();
      if (renderer && typeof renderer.setPixelRatio === "function") {
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      }
    } catch (e) { console.warn("Graph renderer config failed", e); }

    state.graph3d = graph;
    resizeGraphScene();
    return true;
  }

  /* ================================================================
     Scene Data Builder
     ================================================================ */
  function buildGraphSceneData(payload) {
    var snapshotNodes = (payload.snapshot && payload.snapshot.nodes) || [];
    var snapshotEdges = (payload.snapshot && payload.snapshot.edges) || [];
    var selection = getSelectionContext();
    var highlightedNodes = selection.highlightNodeIds;
    var highlightedMemories = selection.highlightMemoryIds;
    var maxWeight = Math.max.apply(null, snapshotNodes.map(function(n) { return Number(n.weight || 1); }).concat([1]));
    var seededPositions = buildSeedPositions(snapshotNodes, maxWeight);

    var nodes = snapshotNodes.map(function(node) {
      var nodeId = Number(node.id);
      var weightRatio = clamp01(Number(node.weight || 0) / maxWeight);
      var isSelected = state.selectedNodeId === nodeId;
      var isHighlighted = highlightedNodes.has(nodeId);
      var isMuted = highlightedNodes.size > 0 && !isHighlighted;
      var memoryRatio = clamp01(Number(node.memory_count || 0) / 8);
      var size = 2.6 + weightRatio * 6.8 + memoryRatio * 1.8 + (isSelected ? 2.4 : isHighlighted ? 1.2 : 0);
      var pos = state.scenePositions.get(nodeId) || seededPositions.get(nodeId) || { x: 0, y: 0, z: 0 };
      return {
        id: nodeId, x: pos.x, y: pos.y, z: pos.z,
        vx: pos.vx || 0, vy: pos.vy || 0, vz: pos.vz || 0,
        __size: size,
        __color: nodeColor(node.type, { isSelected: isSelected, isHighlighted: isHighlighted, isMuted: isMuted }),
        __label: buildNodeTooltip(node),
      };
    });

    var validNodeIds = new Set(nodes.map(function(n) { return Number(n.id); }));
    var groupedPairs = new Map();
    snapshotEdges.forEach(function(edge, idx) {
      var s = Number(edge.source), t = Number(edge.target);
      if (!validNodeIds.has(s) || !validNodeIds.has(t)) return;
      var key = edgePairKey(s, t);
      if (!groupedPairs.has(key)) groupedPairs.set(key, []);
      groupedPairs.get(key).push(idx);
    });

    var links = snapshotEdges.map(function(edge, idx) {
      var s = Number(edge.source), t = Number(edge.target);
      if (!validNodeIds.has(s) || !validNodeIds.has(t)) return null;
      var pairKey = edgePairKey(s, t);
      var siblings = groupedPairs.get(pairKey) || [idx];
      var sibIdx = siblings.indexOf(idx);
      var centerOffset = (siblings.length - 1) / 2;
      var curvature = siblings.length === 1 ? (s <= t ? 0.06 : -0.06) : (sibIdx - centerOffset) * 0.18;
      var isActive = highlightedNodes.size === 0 || (highlightedNodes.has(s) && highlightedNodes.has(t));
      var isMemHighlighted = highlightedMemories.has(Number(edge.memory_id));
      var baseColor = relationColor(edge.relation_type);
      return {
        id: pairKey + ":" + idx + ":" + (edge.memory_id || "na"),
        source: s, target: t,
        __color: linkColor(baseColor, { isActive: isActive, isMemoryHighlighted: isMemHighlighted }),
        __label: relationLabel(edge.relation_type) + " · Memory #" + edge.memory_id,
        __width: isMemHighlighted ? 2.6 : isActive ? 1.2 : 0.32,
        __curvature: curvature,
        __arrowLength: isMemHighlighted ? 5.2 : isActive ? 3.2 : 0,
        __particles: isMemHighlighted ? 5 : isActive ? 2 : 0,
        __particleWidth: isMemHighlighted ? 3.6 : 2.2,
        __particleColor: toRGBA(mixHex(baseColor, "#ffffff", isMemHighlighted ? 0.16 : 0.08), 0.92),
        __particleSpeed: isMemHighlighted ? 0.0105 : 0.0048,
      };
    }).filter(Boolean);

    return { nodes: nodes, links: links };
  }

  function getSelectionContext() {
    if (!state.graphIndex) {
      return { type: null, highlightNodeIds: new Set(), highlightMemoryIds: new Set(), item: null, entries: [], memories: [] };
    }

    if (state.selectedNodeId !== null && state.graphIndex.nodeMap.has(state.selectedNodeId)) {
      var node = state.graphIndex.nodeMap.get(state.selectedNodeId);
      var memIds = Array.from(state.graphIndex.nodeToMemories.get(state.selectedNodeId) || new Set());
      var memories = memIds.map(function(id) { return state.graphIndex.memoryMap.get(id); }).filter(Boolean);
      var neighbors = state.graphIndex.neighborMap.get(state.selectedNodeId) || new Set();
      return {
        type: "node", item: node,
        entries: state.graphIndex.nodeToEntries.get(state.selectedNodeId) || [],
        memories: memories,
        highlightNodeIds: new Set([state.selectedNodeId].concat(Array.from(neighbors))),
        highlightMemoryIds: new Set(memIds),
      };
    }

    if (state.selectedMemoryId !== null && state.graphIndex.memoryMap.has(state.selectedMemoryId)) {
      var mem = state.graphIndex.memoryMap.get(state.selectedMemoryId);
      var nodeIds = state.graphIndex.memoryToNodes.get(state.selectedMemoryId) || new Set();
      return {
        type: "memory", item: mem,
        entries: state.graphIndex.memoryToEntries.get(state.selectedMemoryId) || [],
        memories: [mem],
        highlightNodeIds: new Set(nodeIds),
        highlightMemoryIds: new Set([state.selectedMemoryId]),
      };
    }

    return { type: null, highlightNodeIds: new Set(), highlightMemoryIds: new Set(), item: null, entries: [], memories: [] };
  }

  /* ================================================================
     Seed / Camera Helpers
     ================================================================ */
  function buildSeedPositions(nodes, maxWeight) {
    var groups = { summary: [], topic: [], person: [], fact: [], other: [] };
    nodes.forEach(function(node) {
      var g = Object.prototype.hasOwnProperty.call(TYPE_ORBITS, node.type) ? node.type : "other";
      groups[g].push(node);
    });
    var positions = new Map();
    Object.keys(groups).forEach(function(g) {
      groups[g].sort(function(a, b) { return Number(b.weight || 0) - Number(a.weight || 0); });
      groups[g].forEach(function(node, idx) {
        positions.set(Number(node.id), seedPosition(node, g, idx, groups[g].length, maxWeight));
      });
    });
    return positions;
  }

  function seedPosition(node, groupKey, idx, total, maxWeight) {
    var orbit = TYPE_ORBITS[groupKey] || TYPE_ORBITS.other;
    var ratio = total <= 1 ? 0.5 : idx / total;
    var wRatio = clamp01(Number(node.weight || 0) / maxWeight);
    var radius = orbit.inner + (1 - wRatio) * (orbit.outer - orbit.inner) + (idx % 3) * 12;
    var theta = orbit.phase + ratio * Math.PI * 2.1;
    var phi = Math.PI * (orbit.polarStart + ratio * (orbit.polarEnd - orbit.polarStart));
    return {
      x: Math.cos(theta) * Math.sin(phi) * radius,
      y: Math.cos(phi) * radius * 0.78,
      z: Math.sin(theta) * Math.sin(phi) * radius,
    };
  }

  function queueCameraFocus() {
    window.clearTimeout(state.focusTimer);
    state.focusTimer = window.setTimeout(function() { focusGraphCameraOnSelection(900); }, 180);
  }

  function focusGraphCameraOnSelection(duration) {
    if (!state.graph3d) return;
    if (state.selectedNodeId !== null) {
      focusCameraOnNode(state.selectedNodeId, duration); return;
    }
    var sel = getSelectionContext();
    var centroid = computeCentroid(sel.highlightNodeIds);
    if (!centroid) return;
    var dist = Math.max(180, 120 + sel.highlightNodeIds.size * 10);
    var mag = Math.hypot(centroid.x, centroid.y, centroid.z);
    var r = mag > 1 ? 1 + dist / mag : 1;
    state.graph3d.cameraPosition(
      { x: centroid.x * r + dist * 0.34, y: centroid.y * r + dist * 0.18, z: centroid.z * r + dist * 0.62 },
      centroid, duration
    );
  }

  function focusCameraOnNode(nodeId, duration) {
    if (!state.graph3d || typeof state.graph3d.graphData !== "function") return;
    var data = state.graph3d.graphData();
    var node = (data && data.nodes || []).find(function(n) { return Number(n.id) === Number(nodeId); });
    if (!node) return;
    var dist = Math.max(150, Number(node.__size || 0) * 18);
    var mag = Math.hypot(node.x || 0, node.y || 0, node.z || 0);
    var r = mag > 1 ? 1 + dist / mag : 1;
    state.graph3d.cameraPosition(
      { x: (node.x || 0) * r + dist * 0.18, y: (node.y || 0) * r + dist * 0.08, z: (node.z || 0) * r + dist * 0.42 },
      { x: node.x || 0, y: node.y || 0, z: node.z || 0 }, duration
    );
  }

  function computeCentroid(nodeIds) {
    if (!state.graph3d || typeof state.graph3d.graphData !== "function" || !nodeIds.size) return null;
    var lookup = new Map((state.graph3d.graphData() && state.graph3d.graphData().nodes || []).map(function(n) { return [Number(n.id), n]; }));
    var total = 0, sum = { x: 0, y: 0, z: 0 };
    nodeIds.forEach(function(id) {
      var n = lookup.get(Number(id));
      if (!n || !Number.isFinite(n.x) || !Number.isFinite(n.y) || !Number.isFinite(n.z)) return;
      total++; sum.x += n.x; sum.y += n.y; sum.z += n.z;
    });
    return total ? { x: sum.x / total, y: sum.y / total, z: sum.z / total } : null;
  }

  /* ================================================================
     Color / Label Helpers
     ================================================================ */
  function nodeColor(type, opts) {
    opts = opts || {};
    var base = NODE_TYPE_COLORS[String(type).toLowerCase()] || NODE_TYPE_COLORS.other;
    if (opts.isSelected) return toRGBA(mixHex(base, "#ffffff", 0.24), 0.98);
    if (opts.isHighlighted) return toRGBA(mixHex(base, "#ffffff", 0.12), 0.94);
    if (opts.isMuted) return toRGBA("#334155", 0.34);
    return toRGBA(base, 0.88);
  }

  function relationColor(type) { var v = String(type || "related"), idx = Math.abs(hashString(v)) % RELATION_PALETTE.length; return RELATION_PALETTE[idx]; }
  function linkColor(base, opts) {
    opts = opts || {};
    if (opts.isMemoryHighlighted) return toRGBA(mixHex(base, "#ffffff", 0.14), 0.88);
    if (opts.isActive) return toRGBA(base, 0.4);
    return toRGBA("#334155", 0.14);
  }

  function buildNodeTooltip(node) {
    var t = normalizeNodeType(node.type);
    return '<div class="graph-tooltip"><span class="graph-tooltip-badge graph-node-' + esc(String(t)) + '">' +
      esc(typeLabel(node.type)) + '</span><strong>' + esc(node.label || "Unnamed") +
      '</strong><span>' + esc((node.canonical_value || node.label || "").substring(0, 72)) +
      '</span><small>Memory ' + (node.memory_count || 0) + ' · Deg ' + (node.degree || 0) + ' · Entries ' + (node.entry_count || 0) + '</small></div>';
  }

  /* ================================================================
     Math / Util
     ================================================================ */
  function hexToRgb(h) { var v = String(h || "#000").replace("#","").trim(); v = v.length===3 ? v.split("").map(function(c){return c+c;}).join("") : v.padEnd(6,"0").slice(0,6); return { r: parseInt(v.slice(0,2),16), g: parseInt(v.slice(2,4),16), b: parseInt(v.slice(4,6),16) }; }
  function rgbToHex(rgb) { return "#" + [rgb.r, rgb.g, rgb.b].map(function(c) { return Math.min(255,Math.max(0,c)).toString(16).padStart(2,"0"); }).join(""); }
  function mixHex(a, b, r) { var la = hexToRgb(a), lb = hexToRgb(b), w = clamp01(r); return rgbToHex({ r: Math.round(la.r+(lb.r-la.r)*w), g: Math.round(la.g+(lb.g-la.g)*w), b: Math.round(la.b+(lb.b-la.b)*w) }); }
  function toRGBA(hex, alpha) { var rgb = hexToRgb(hex); return "rgba(" + rgb.r + "," + rgb.g + "," + rgb.b + "," + clamp01(alpha) + ")"; }
  function hashString(s) { return String(s||"").split("").reduce(function(a,c){return a*31+c.charCodeAt(0);},7); }
  function clamp01(v) { return Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0)); }
  function edgePairKey(a, b) { return Math.min(Number(a), Number(b)) + ":" + Math.max(Number(a), Number(b)); }
  function normalizeNodeType(t) { return Object.prototype.hasOwnProperty.call(NODE_TYPE_COLORS, t) ? t : "other"; }
  function typeLabel(t) { return NODE_TYPE_LABELS[t] || t || "Node"; }
  function relationLabel(t) { return String(t||"related").replace(/_/g," ").replace(/\b\w/g,function(c){return c.toUpperCase();}); }
  function esc(s) { return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

  document.addEventListener("DOMContentLoaded", init);
})();
