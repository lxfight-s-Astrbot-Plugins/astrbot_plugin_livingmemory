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
    overviewMode: null, /* "limited" | "full" | null */
  };

  const EXPANDED_GRAPH_LIMITS = {
    limit_memories: 24,
    limit_entries: 80,
    limit_nodes: 80,
    limit_edges: 120,
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
    topic: "#78a94b", person: "#2a9e96", fact: "#c58c2a",
    summary: "#df6d62", other: "#74868a",
  };

  /* ================================================================
     Bridge helpers
     ================================================================ */
  function buildEndpoint(path) {
    var cleanPath = String(path).replace(/^\/+/, "");
    if (cleanPath.startsWith("page/")) return cleanPath;
    return ("page/" + cleanPath).replace(/\/+/g, "/");
  }

  function numericId(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "string" && !value.trim()) return null;
    var id = Number(value);
    return Number.isFinite(id) ? id : null;
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
    dom.searchButton = document.getElementById("graph-search-btn");
    dom.overviewButton = document.getElementById("graph-overview-btn");
    dom.legend = document.getElementById("graph-legend");
    dom.canvas = document.getElementById("graph-canvas");
    dom.canvasState = document.getElementById("graph-canvas-state");

    if (!dom.canvas) return;

    dom.searchButton.addEventListener("click", runQuery);
    /* 「全量图谱」按钮可来回切换：受限概览 ↔ 全量图（#248）。 */
    dom.overviewButton.addEventListener("click", function() {
      fetchOverview(state.overviewMode !== "full");
    });
    setOverviewButtonLabel();

    dom.queryInput.addEventListener("keydown", function(e) {
      if (e.key === "Enter") { e.preventDefault(); runQuery(); }
    });
    /* 会话过滤此前改完不提交也没有 Enter，输入等于无效 */
    dom.sessionInput.addEventListener("keydown", function(e) {
      if (e.key === "Enter") {
        e.preventDefault();
        fetchOverview(state.overviewMode === "full");
      }
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
    } else {
      setCanvasMessage(window.t("graph2d.moduleFail"), false);
    }

    window.addEventListener("languagechange", function() {
      initLabels();
      setOverviewButtonLabel();
      if (state.payload) renderLegend(state.payload);
    });

    /* app.js starts loading after the host Bridge reports ready. */
  }

  /* Expose for app.js lazy-load */
  window.ensureGraphScene = function() {
    /* 页面默认加载受限概览（最近记忆子图，节点/边受后端上限约束），
       避免一次性绘制全量节点与关系造成视觉毛团（#248）。 */
    if (!state.hasLoadedOverview && !state.isLoading) fetchOverview(false);
  };

  /* ================================================================
     Data Fetching
     ================================================================ */
  function getFilters() {
    return {
      session_id: dom.sessionInput ? dom.sessionInput.value.trim() || null : null,
    };
  }

  function addOptionalFilter(body, key, value) {
    if (value !== null && value !== undefined && String(value).trim()) {
      body[key] = String(value).trim();
    }
    return body;
  }

  function addExpandedLimits(target) {
    Object.entries(EXPANDED_GRAPH_LIMITS).forEach(function(entry) {
      target[entry[0]] = String(entry[1]);
    });
    return target;
  }

  async function fetchOverview(fullGraph) {
    setLoading(true);
    setCanvasMessage(window.t("graph.loadingOverview"), true);
    try {
      var filters = getFilters();
      var params = new URLSearchParams();
      if (fullGraph) params.set("full_graph", "true");
      if (filters.session_id) params.set("session_id", filters.session_id);
      var qs = params.toString();
      var payload = await requestGraph("/graph/overview" + (qs ? "?" + qs : ""));
      state.hasLoadedOverview = true;
      state.overviewMode = fullGraph ? "full" : "limited";
      setOverviewButtonLabel();
      renderPayload(payload, true);
      // 统计卡片由 renderPayload 内部统一刷新，此处不再重复请求
    } catch (e) {
      setCanvasMessage(e.message || window.t("graph.errorFetch"), false);
    } finally {
      setLoading(false);
    }
  }

  /* 按当前模式切换按钮文案（全量图谱 ↔ 受限概览），并同步 data-i18n
     供语言切换时重渲染。 */
  function setOverviewButtonLabel() {
    if (!dom.overviewButton) return;
    var key = state.overviewMode === "full" ? "graph.limitedBtn" : "graph.overviewBtn";
    var label = dom.overviewButton.querySelector ? dom.overviewButton.querySelector("span") : null;
    if (label) {
      label.setAttribute("data-i18n", key);
      label.textContent = window.t(key);
    }
    dom.overviewButton.setAttribute("data-i18n-aria", key);
  }

  async function runQuery() {
    if (state.isLoading) return; // 回车触发绕过按钮 disabled，显式防并发
    var query = dom.queryInput.value.trim();
    if (!query) { fetchOverview(false); return; }

    setLoading(true);
    setCanvasMessage(window.t("graph.loadingQuery", query), true);
    try {
      var filters = getFilters();
      var body = addExpandedLimits({ query: query });
      addOptionalFilter(body, "session_id", filters.session_id);
      var payload = await requestGraph("/graph/query", {
        method: "POST",
        body: body,
      });
      renderPayload(payload, true);
    } catch (e) {
      setCanvasMessage(e.message || window.t("graph.queryFail"), false);
    } finally {
      setLoading(false);
    }
  }

  /**
   * 按记忆 ID 定位子图。ID 由调用方从上下文传入，用户不需要知道也不需要手输。
   * @param {number|string} memoryId - 记忆 ID
   */
  async function focusMemoryById(memoryId) {
    var id = numericId(memoryId);
    if (id === null || state.isLoading) return;

    setLoading(true);
    setCanvasMessage(window.t("graph.loadingFocus", id), true);
    try {
      var filters = getFilters();
      var body = addExpandedLimits({ memory_id: id });
      addOptionalFilter(body, "session_id", filters.session_id);
      var payload = await requestGraph("/graph/query", {
        method: "POST",
        body: body,
      });
      renderPayload(payload, true);
    } catch (e) {
      setCanvasMessage(e.message || window.t("graph.focusFail"), false);
    } finally {
      setLoading(false);
    }
  }

  /* 记忆详情 → 图谱定位 */
  window.lmFocusMemoryOnGraph = async function(memoryId) {
    if (window.lmSwitchPage) await window.lmSwitchPage("graph");
    return focusMemoryById(memoryId);
  };

  /* ================================================================
     Render Pipeline
     ================================================================ */
  function setLoading(loading) {
    state.isLoading = loading;
    if (dom.searchButton) dom.searchButton.disabled = loading;
    if (dom.overviewButton) dom.overviewButton.disabled = loading;
  }

  function renderPayload(payload, focusSelection) {
    state.payload = payload;
    state.graphIndex = buildGraphIndex(payload.snapshot || {});

    if (!payload.enabled) {
      setCanvasMessage(window.t("graph.disabledMsg"), false);
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
      if (hasGraphData) {
        /* 布局期间显示「正在生成图谱布局…」，完成后由回调清除（#248）。 */
        setCanvasMessage(window.t("graph.layouting"), true);
      } else {
        setCanvasMessage(window.t("graph.canvasEmpty"), false);
      }
      window.Graph2D.loadData(payload, {
        onLayoutDone: function() {
          if (state.graphIndex && state.graphIndex.nodeMap.size > 0) {
            setCanvasMessage("", false);
          }
        },
      });
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
    var selectedNodeId = numericId(state.selectedNodeId);
    if (state.graphIndex && selectedNodeId !== null && state.graphIndex.nodeMap.has(selectedNodeId)) {
      state.selectedNodeId = selectedNodeId;
      state.selectedMemoryId = null;
      return;
    }
    var selectedMemoryId = numericId(state.selectedMemoryId);
    if (state.graphIndex && selectedMemoryId !== null && state.graphIndex.memoryMap.has(selectedMemoryId)) {
      state.selectedNodeId = null;
      state.selectedMemoryId = selectedMemoryId;
      return;
    }

    state.selectedNodeId = null;
    state.selectedMemoryId = null;

    var matchedNodeIds = payload.matched_node_ids || [];
    var firstNode = matchedNodeIds.find(function(id) {
      var nodeId = numericId(id);
      return nodeId !== null && state.graphIndex && state.graphIndex.nodeMap.has(nodeId);
    });
    if (firstNode !== undefined) {
      state.selectedNodeId = numericId(firstNode);
      return;
    }

    var firstRetrieved = payload.retrieval && payload.retrieval.items && payload.retrieval.items[0];
    var firstRetrievedId = firstRetrieved ? numericId(firstRetrieved.memory_id) : null;
    if (firstRetrievedId !== null && state.graphIndex && state.graphIndex.memoryMap.has(firstRetrievedId)) {
      state.selectedMemoryId = firstRetrievedId;
      return;
    }

    var topNodes = payload.top_nodes || [];
    var topNodeId = topNodes.length ? numericId(topNodes[0].id) : null;
    if (topNodeId !== null && state.graphIndex && state.graphIndex.nodeMap.has(topNodeId)) {
      state.selectedNodeId = topNodeId;
      return;
    }

    var snapMemories = (payload.snapshot && payload.snapshot.memories) || [];
    var snapMemoryId = snapMemories.length ? numericId(snapMemories[0].memory_id) : null;
    if (snapMemoryId !== null && state.graphIndex && state.graphIndex.memoryMap.has(snapMemoryId)) {
      state.selectedMemoryId = snapMemoryId;
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
    nodeId = numericId(nodeId);
    if (nodeId === null) return;
    if (!state.graphIndex || !state.graphIndex.nodeMap.has(nodeId)) return;
    state.selectedNodeId = nodeId;
    state.selectedMemoryId = null;

    if (state.isGraphReady) {
      window.Graph2D.selectNode(nodeId);
    }

    /* 一并传出该节点的关联记忆，图 → 记忆不再是死路 */
    var node = state.graphIndex.nodeMap.get(nodeId);
    if (window.lmOpenPeekNode && node) {
      var related = [];
      var memoryIds = state.graphIndex.nodeToMemories.get(nodeId);
      if (memoryIds) {
        memoryIds.forEach(function(memoryId) {
          var memory = state.graphIndex.memoryMap.get(memoryId);
          if (memory) related.push(memory);
        });
      }
      window.lmOpenPeekNode(Object.assign({}, node, { memories: related }));
    }
  }

  function selectMemory(memoryId) {
    memoryId = numericId(memoryId);
    if (memoryId === null) return;
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

    /* 图例只解释画布上真实存在的编码：节点颜色。关系 chip 既没有色点，
       也与画布上的边色无关，还用的是未本地化的枚举名。 */
    var chips = Object.entries(nodeTypes).sort(function(a, b) { return b[1] - a[1]; })
      .map(function(e) {
        return '<span class="legend-chip"><span class="dot" style="background:' +
          (NODE_TYPE_COLORS[e[0]] || NODE_TYPE_COLORS.other) + '"></span>' +
          typeLabel(e[0]) + ' &middot; ' + e[1] + '</span>';
      });

    /* 服务端对全量图有硬上限；被截断时明说，否则与左上角总数互相矛盾。 */
    var visibleNodes = Number(summary.visible_node_count || 0);
    var totalNodes = Number(summary.graph_node_count || 0);
    if (totalNodes > visibleNodes && visibleNodes > 0) {
      chips.push('<span class="legend-chip">' + window.t("graph.visibleNodes", visibleNodes, totalNodes) + '</span>');
    }

    dom.legend.innerHTML = chips.join("") ||
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

  document.addEventListener("DOMContentLoaded", init);
})();
