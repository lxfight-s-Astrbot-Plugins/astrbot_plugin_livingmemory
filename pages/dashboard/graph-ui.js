(() => {
  "use strict";

  /* ================================================================
     Graph UI — 2D Knowledge Graph Controller
     Bridges page_api data → Graph2D renderer
     ================================================================ */

  const state = {
    payload: null,
    graphIndex: null,
    selectedNodeId: null,
    selectedMemoryId: null,
    isLoading: false,
    hasLoadedOverview: false,
    isGraphReady: false,
  };

  const dom = {};

  /* Node type config */
  var NODE_TYPE_LABELS = {};
  function initLabels() {
    NODE_TYPE_LABELS = {
      get topic() { return window.t("graph.nodeTopic"); },
      get person() { return window.t("graph.nodePerson"); },
      get fact() { return window.t("graph.nodeFact"); },
      get summary() { return window.t("graph.nodeSummary"); },
    };
  }

  const NODE_TYPE_COLORS = {
    topic: "#7c6fca", person: "#2f9e8b", fact: "#c99a16",
    summary: "#c8648d", other: "#8b949e",
  };

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
        return unwrapGraphData(await bridge.apiGet(buildEndpoint(base), params));
      }
      return unwrapGraphData(await bridge.apiGet(buildEndpoint(path), {}));
    }
    return unwrapGraphData(await bridge.apiPost(buildEndpoint(path), options.body || {}));
  }

  function unwrapGraphData(response) {
    if (response && response.status === "ok" && Object.prototype.hasOwnProperty.call(response, "data")) {
      return response.data || {};
    }
    if (response && response.status === "error") {
      throw new Error(response.message || window.t("misc.requestFailed"));
    }
    return response || {};
  }

  /* ================================================================
     Init
     ================================================================ */
  function init() {
    initLabels();
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

    /* Init Graph2D */
    if (window.Graph2D && typeof window.Graph2D.init === "function") {
      window.Graph2D.init(dom.canvas, {
        onNodeClick: function(nodeId) {
          selectNode(nodeId, false);
        },
        onNodeDblClick: function(nodeId) {
          selectNode(nodeId, true);
        },
        onNodeHover: function(nodeId) {
          /* Tooltip could go here */
        },
        onBackgroundClick: function() {
          clearSelection();
        },
      });
      state.isGraphReady = true;
      setCanvasMessage(window.t("graph.canvasDefault"), false);
    } else {
      setCanvasMessage(window.t("graph2d.moduleFail"), false);
    }

    window.addEventListener("languagechange", function() {
      initLabels();
      if (state.payload) renderLegend(state.payload);
      if (!state.payload && dom.canvasState && dom.canvasState.textContent) {
        setCanvasMessage(window.t("graph.canvasDefault"), false);
      }
    });

    /* Auto-load overview */
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
      renderPayload(payload, true);
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
      renderPayload(payload, true);
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
      renderPayload(payload, true);
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

  function renderPayload(payload, focusSelection) {
    state.payload = payload;
    state.graphIndex = buildGraphIndex(payload.snapshot || {});

    if (!payload.enabled) {
      setCanvasMessage(window.t("graph.disabledCanvas"), false);
      renderLegend(payload);
      return;
    }

    var snapshot = payload.snapshot || {};
    var hasGraphData = Boolean((snapshot.nodes || []).length);
    var shouldFocusSelection = hasGraphData &&
      focusSelection !== false &&
      payload.mode !== "overview";

    if (!shouldFocusSelection) {
      state.selectedNodeId = null;
      state.selectedMemoryId = null;
      if (state.isGraphReady && window.Graph2D) {
        window.Graph2D.selection = null;
        if (window.Graph2D.renderer) window.Graph2D.renderer._selection = null;
      }
    }

    /* Delegate to Graph2D renderer */
    if (state.isGraphReady) {
      window.Graph2D.loadData(payload);
      setCanvasMessage(hasGraphData ? "" : window.t("graph.canvasEmpty"), false);
    }

    /* Auto-select based on payload mode */
    if (shouldFocusSelection) {
      ensureSelection(payload);
    } else if (!hasGraphData) {
      state.selectedNodeId = null;
      state.selectedMemoryId = null;
    }

    renderLegend(payload);

    /* Apply selection to Graph2D */
    if (state.isGraphReady) {
      if (state.selectedNodeId !== null) {
        window.Graph2D.selectNode(state.selectedNodeId);
      } else if (state.selectedMemoryId !== null) {
        window.Graph2D.selectMemory(state.selectedMemoryId);
      }
    }

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
    if (state.isGraphReady) window.Graph2D.clearSelection();
    if (window.lmClosePeek) window.lmClosePeek();
  }

  function selectNode(nodeId, focusCamera) {
    if (!state.graphIndex || !state.graphIndex.nodeMap.has(nodeId)) return;
    state.selectedNodeId = nodeId;
    state.selectedMemoryId = null;

    if (state.isGraphReady) {
      window.Graph2D.selectNode(nodeId);
    }

    /* Show in peek panel */
    var node = state.graphIndex.nodeMap.get(nodeId);
    if (window.lmOpenPeekNode && node) window.lmOpenPeekNode(node);
  }

  function selectMemory(memoryId) {
    if (!state.graphIndex || !state.graphIndex.memoryMap.has(memoryId)) return;
    state.selectedMemoryId = memoryId;
    state.selectedNodeId = null;

    if (state.isGraphReady) {
      window.Graph2D.selectMemory(memoryId);
    }

    /* Show in peek panel */
    var memory = state.graphIndex.memoryMap.get(memoryId);
    if (window.lmState && memory) {
      var item = {
        memory_id: memoryId,
        summary: memory.summary || memory.content || "",
        content: memory.content || memory.summary || "",
        memory_type: memory.memory_type || "",
        importance: memory.importance,
        status: memory.status || "active",
        raw: memory,
      };
      if (window.lmOpenPeekMemory) window.lmOpenPeekMemory(item);
    }
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
      '<span class="legend-chip">' + window.t("graph.legendEmpty") + '</span>';
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
     Canvas Messages
     ================================================================ */
  function setCanvasMessage(msg, loading) {
    if (!dom.canvasState) return;
    dom.canvasState.textContent = msg || "";
    dom.canvasState.style.display = msg ? "block" : "none";
  }

  /* ================================================================
     Labels
     ================================================================ */
  function typeLabel(t) { return NODE_TYPE_LABELS[t] || t || window.t("graph.nodeUnknown"); }
  function relationLabel(t) { return String(t || "related").replace(/_/g, " ").replace(/\b\w/g, function(c) { return c.toUpperCase(); }); }

  document.addEventListener("DOMContentLoaded", init);
})();
