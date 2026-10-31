(() => {
  "use strict";

  /* ================================================================
     Graph UI — Knowledge Graph Controller
     Shares page_api data and selection across the two renderers.
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
    viewMode: "2d",
    renderer: null,
    graph3D: null,
    pageActive: true,
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
    var bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
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
    dom.viewMode = document.getElementById("graph-view-mode");
    dom.resetView = document.getElementById("graph-reset-view");
    dom.relationsButton = document.getElementById("graph-relations-btn");
    dom.motionButton = document.getElementById("graph-motion-btn");
    dom.spaceControls = document.getElementById("graph-space-controls");
    dom.page = document.getElementById("page-graph");
    dom.view2D = document.getElementById("graph-view-2d");
    dom.view3D = document.getElementById("graph-view-3d");

    if (!dom.canvas) return;

    dom.searchButton.addEventListener("click", runQuery);
    /* 「全量图谱」按钮可来回切换：受限概览 ↔ 全量图（#248）。 */
    dom.overviewButton.addEventListener("click", function() {
      fetchOverview(state.overviewMode !== "full");
    });
    setOverviewButtonLabel();
    dom.viewMode.addEventListener("change", switchView);
    dom.resetView.addEventListener("click", function() {
      state.selectedNodeId = state.selectedMemoryId = null;
      if (state.isGraphReady) state.renderer.resetView();
      if (window.lmClosePeek) window.lmClosePeek();
    });
    dom.relationsButton.addEventListener("click", function() {
      state.graph3D.setShowAllRelations(!state.graph3D.showAllRelations);
      dom.relationsButton.setAttribute("aria-pressed", String(state.graph3D.showAllRelations));
    });
    dom.motionButton.addEventListener("click", function() {
      state.graph3D.setMotionEnabled(!state.graph3D.motionEnabled);
      setMotionButtonLabel();
    });
    setMotionButtonLabel();
    setInteractionHint();

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
      state.renderer = window.Graph2D;
      window.Graph2D.init(dom.view2D, {
        onNodeClick: function(nodeId) {
          selectNode(nodeId, false);
        },
        onNodeDblClick: function(nodeId) {
          selectNode(nodeId, true);
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
      setInteractionHint();
      setMotionButtonLabel();
      if (state.payload) renderLegend(state.payload);
    });

    /* app.js starts loading after the host Bridge reports ready. */
  }

  function setMotionButtonLabel() {
    const paused = state.graph3D?.motionEnabled === false;
    const key = paused ? "graph.resumeMotion" : "graph.pauseMotion";
    dom.motionButton.setAttribute("data-i18n", key);
    dom.motionButton.textContent = window.t(key);
    dom.motionButton.setAttribute("aria-pressed", String(paused));
  }

  function setInteractionHint() {
    dom.canvas.setAttribute("title", state.viewMode === "3d" ? "" : window.t("graph.controls2d"));
    const queryKey = state.viewMode === "3d" ? "graph.searchPh" : "graph.queryPh";
    const scopeKey = state.viewMode === "3d" ? "graph.scopePh" : "graph.sessionPh";
    dom.queryInput.setAttribute("data-i18n-placeholder", queryKey);
    dom.queryInput.setAttribute("placeholder", window.t(queryKey));
    dom.sessionInput.setAttribute("data-i18n-placeholder", scopeKey);
    dom.sessionInput.setAttribute("placeholder", window.t(scopeKey));
  }

  /* 3D 场景只在图谱页可见时持有：离开页面立即 destroy，返回时按需重建。
     WebGL 上下文、three.js 缓冲和布局 Worker 是标签页里最贵的一档资源，
     切到记忆/系统页后继续驻留会让浏览器长期占用数百 MB 且无法回收。 */
  let graph3DLoading = null;

  function createGraph3D() {
    if (state.graph3D) return Promise.resolve(state.graph3D);
    if (graph3DLoading) return graph3DLoading;
    graph3DLoading = import(new URL("./assets/graph-3d.min.js", document.baseURI).href)
      .then(({ default: Graph3D }) => {
        const instance = new Graph3D();
        dom.view3D.hidden = false;
        instance.init(dom.view3D, {
          onNodeClick: nodeId => selectNode(nodeId, false),
          onBackgroundClick: clearSelection,
          onError: message => setCanvasMessage(message || window.t("graph.renderFail"), false),
        });
        state.graph3D = instance;
        return instance;
      })
      .catch(error => {
        dom.view3D.hidden = true;
        setCanvasMessage(error.message || window.t("graph.renderFail"), false);
        return null;
      })
      .finally(() => {
        graph3DLoading = null;
      });
    return graph3DLoading;
  }

  function releaseGraph3D() {
    if (!state.graph3D) return;
    if (state.renderer === state.graph3D) state.renderer = window.Graph2D;
    state.graph3D.destroy();
    state.graph3D = null;
    dom.view3D.hidden = true;
  }

  async function switchView() {
    const mode = dom.viewMode.value;
    if (mode === "3d" && !state.graph3D) {
      const instance = await createGraph3D();
      if (!instance || dom.viewMode.value !== "3d") {
        dom.viewMode.value = state.viewMode;
        return;
      }
    }
    state.viewMode = mode;
    state.renderer = mode === "3d" ? state.graph3D : window.Graph2D;
    dom.page.classList.toggle("graph-space-mode", mode === "3d");
    dom.view2D.hidden = mode !== "2d";
    dom.view3D.hidden = mode !== "3d";
    dom.spaceControls.hidden = mode !== "3d";
    setMotionButtonLabel();
    state.isGraphReady = true;
    window.lmSetGraphActive(state.pageActive);
    setInteractionHint();
    if (state.payload) { renderScene(); renderLegend(state.payload); }
  }

  window.lmSetGraphActive = function(active) {
    state.pageActive = active;
    if (window.Graph2D) window.Graph2D.setActive(active && state.viewMode === "2d");
    if (!active) {
      releaseGraph3D();
      document.documentElement.dataset.graphView = "";
      return;
    }
    if (state.viewMode !== "3d") {
      document.documentElement.dataset.graphView = state.viewMode;
      return;
    }
    if (state.graph3D) {
      state.renderer = state.graph3D;
      state.graph3D.setActive(true);
    } else {
      /* 回到图谱页时 3D 已按离开策略销毁，重建后重新加载当前快照。 */
      createGraph3D().then(instance => {
        if (!instance || !state.pageActive || state.viewMode !== "3d" || state.graph3D !== instance) return;
        state.renderer = instance;
        instance.setActive(true);
        if (state.payload) renderScene();
        document.documentElement.dataset.graphView = state.viewMode;
      });
    }
    document.documentElement.dataset.graphView = state.viewMode;
  };

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
      state.selectedNodeId = null;
      state.selectedMemoryId = id;
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
    var snapshot = payload.snapshot || {};
    /* 全量图谱 snapshot 的 key/metadata/status 等边字段前端从不消费，
       却占 payload 体积一半以上；入库时收敛为渲染与详情实际用到的字段，
       否则这些字符串会跟着 state.payload 一起长期驻留。 */
    if (snapshot.edges && snapshot.edges.length) {
      snapshot.edges = compactEdges(snapshot.edges);
    }
    state.payload = payload;
    state.graphIndex = buildGraphIndex(snapshot);

    if (!payload.enabled) {
      state.selectedNodeId = state.selectedMemoryId = null;
      setCanvasMessage(window.t("graph.disabledMsg"), false);
      renderLegend(payload);
      if (state.isGraphReady) renderScene();
      return;
    }

    var hasGraphData = Boolean((snapshot.nodes || []).length);
    var shouldFocusSelection = hasGraphData &&
      focusSelection !== false &&
      payload.mode !== "overview";

    if (!shouldFocusSelection) {
      state.selectedNodeId = null;
      state.selectedMemoryId = null;
    }

    /* Auto-select based on payload mode */
    if (shouldFocusSelection) {
      ensureSelection(payload);
    } else if (!hasGraphData) {
      state.selectedNodeId = null;
      state.selectedMemoryId = null;
    }

    renderLegend(payload);
    if (state.isGraphReady) renderScene();

    if (window.lmFetchGraphStats) window.lmFetchGraphStats();
  }

  function renderScene() {
    const renderer = state.renderer;
    const payload = state.payload;
    /* 回到图谱页时 3D 可能仍在按需重建：此时不加载数据，
       等实例就绪后由 lmSetGraphActive 重新调用 renderScene。 */
    if (state.viewMode === "3d" && !state.graph3D) return;
    if (!payload.enabled) {
      renderer.selection = null;
      renderer.loadData({ snapshot: { nodes: [], edges: [] } });
      setCanvasMessage(window.t("graph.disabledMsg"), false);
      return;
    }
    const hasGraphData = state.graphIndex.nodeMap.size > 0;
    const selection = state.selectedNodeId !== null ? { type: "node", id: state.selectedNodeId }
      : state.selectedMemoryId !== null ? { type: "memory", id: state.selectedMemoryId } : null;
    state.renderer.selection = selection;
    if (state.renderer.renderer && state.viewMode === "2d") state.renderer.renderer._selection = selection;
    setCanvasMessage(window.t(hasGraphData ? "graph.layouting" : "graph.canvasEmpty"), hasGraphData);
    state.renderer.loadData(state.payload, {
      onLayoutDone: function() {
        if (state.renderer !== renderer || state.payload !== payload) return;
        if (state.graphIndex.nodeMap.size) setCanvasMessage("", false);
      },
    });
    if (selection?.type === "node") state.renderer.selectNode(selection.id);
    else if (selection?.type === "memory") state.renderer.selectMemory(selection.id);
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
    if (state.isGraphReady && state.renderer.selection) state.renderer.clearSelection();
    if (window.lmClosePeek) window.lmClosePeek();
  }

  function selectNode(nodeId, focusCamera) {
    nodeId = numericId(nodeId);
    if (nodeId === null) return;
    if (!state.graphIndex || !state.graphIndex.nodeMap.has(nodeId)) return;
    state.selectedNodeId = nodeId;
    state.selectedMemoryId = null;

    if (state.isGraphReady && (state.renderer.selection?.type !== "node" || state.renderer.selection.id !== nodeId)) {
      state.renderer.selectNode(nodeId);
    }

    /* 一并传出该节点的关联记忆，图 → 记忆不再是死路 */
    var node = state.graphIndex.nodeMap.get(nodeId);
    if (window.lmOpenPeekNode && node) {
      var related = [];
      var memoryIds = state.graphIndex.getNodeMemories(nodeId);
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
      state.renderer.selectMemory(memoryId);
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
    var colors = state.viewMode === "3d" ? state.graph3D.colors : NODE_TYPE_COLORS;

    /* 图例只解释画布上真实存在的编码：节点颜色。关系 chip 既没有色点，
       也与画布上的边色无关，还用的是未本地化的枚举名。 */
    var chips = Object.entries(nodeTypes).sort(function(a, b) { return b[1] - a[1]; })
      .map(function(e) {
        return '<span class="legend-chip"><span class="dot" style="background:' +
          (colors[e[0]] || colors.other) + '"></span>' +
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

  /* 前端渲染与详情实际消费的边字段；其余字段（key/metadata/status 等）
     在全量图里占 payload 一半以上，不参与任何计算。 */
  var EDGE_FIELDS = ["id", "source", "target", "relation_type", "memory_id", "weight", "confidence"];

  function compactEdges(edges) {
    var compact = new Array(edges.length);
    for (var i = 0; i < edges.length; i++) {
      var edge = edges[i];
      var item = {};
      for (var f = 0; f < EDGE_FIELDS.length; f++) item[EDGE_FIELDS[f]] = edge[EDGE_FIELDS[f]];
      compact[i] = item;
    }
    return compact;
  }

  /* 选中节点时才需要的「节点 → 关联记忆」索引：全量图有 20 万条边，
     加载时就建一遍会让首屏多耗数百毫秒并常驻更多 Set/Map。 */
  function buildGraphIndex(snapshot) {
    var nodes = snapshot.nodes || [];
    var edges = snapshot.edges || [];
    var memories = snapshot.memories || [];

    var nodeMap = new Map(nodes.map(function(n) { return [Number(n.id), n]; }));
    var memoryMap = new Map(memories.map(function(m) { return [Number(m.memory_id), m]; }));
    var nodeToMemories = null;

    return {
      nodeMap: nodeMap,
      memoryMap: memoryMap,
      getNodeMemories: function(nodeId) {
        if (nodeToMemories === null) {
          nodeToMemories = new Map();
          edges.forEach(function(edge) {
            var memoryId = Number(edge.memory_id);
            var source = Number(edge.source);
            var target = Number(edge.target);
            if (!nodeToMemories.has(source)) nodeToMemories.set(source, new Set());
            if (!nodeToMemories.has(target)) nodeToMemories.set(target, new Set());
            nodeToMemories.get(source).add(memoryId);
            nodeToMemories.get(target).add(memoryId);
          });
        }
        return nodeToMemories.get(nodeId);
      },
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
