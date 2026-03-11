(() => {
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

  const NODE_TYPE_LABELS = {
    topic: "主题",
    person: "人物",
    fact: "事实",
    summary: "摘要",
  };

  const NODE_TYPE_COLORS = {
    topic: "#818cf8",
    person: "#34d399",
    fact: "#fbbf24",
    summary: "#f472b6",
    other: "#94a3b8",
  };

  const TYPE_ORBITS = {
    summary: { inner: 32, outer: 118, phase: 0.18, polarStart: 0.24, polarEnd: 0.78 },
    topic: { inner: 160, outer: 245, phase: 1.08, polarStart: 0.28, polarEnd: 0.8 },
    person: { inner: 212, outer: 306, phase: 2.28, polarStart: 0.22, polarEnd: 0.7 },
    fact: { inner: 254, outer: 354, phase: 3.74, polarStart: 0.32, polarEnd: 0.84 },
    other: { inner: 220, outer: 320, phase: 5.08, polarStart: 0.2, polarEnd: 0.82 },
  };

  const RELATION_PALETTE = [
    "#38bdf8",
    "#818cf8",
    "#f59e0b",
    "#10b981",
    "#f472b6",
    "#22d3ee",
    "#fb7185",
    "#a78bfa",
  ];

  const MODE_LABELS = {
    overview: "最近概览",
    query: "检索视图",
    memory_focus: "记忆聚焦",
  };

  const SCORE_LABELS = [
    ["document_keyword_score", "文档关键词"],
    ["document_vector_score", "文档向量"],
    ["graph_keyword_score", "图关键词"],
    ["graph_vector_score", "图向量"],
  ];

  function init() {
    dom.tabButton = document.querySelector('.tab-btn[data-tab="graph-view"]');
    dom.tabContent = document.querySelector('.tab-content[data-tab="graph-view"]');
    if (!dom.tabButton || !dom.tabContent) {
      return;
    }

    dom.queryInput = document.getElementById("graph-query-input");
    dom.sessionInput = document.getElementById("graph-session-filter");
    dom.personaInput = document.getElementById("graph-persona-filter");
    dom.memoryInput = document.getElementById("graph-memory-id");
    dom.searchButton = document.getElementById("graph-search-btn");
    dom.focusButton = document.getElementById("graph-focus-btn");
    dom.overviewButton = document.getElementById("graph-overview-btn");
    dom.modeBadge = document.getElementById("graph-mode-badge");
    dom.statusLine = document.getElementById("graph-status-line");
    dom.legend = document.getElementById("graph-legend");
    dom.canvas = document.getElementById("graph-canvas");
    dom.canvasShell = dom.canvas?.closest(".graph-canvas-shell") || null;
    dom.canvasState = document.getElementById("graph-canvas-state");
    dom.inspector = document.getElementById("graph-inspector");
    dom.topNodes = document.getElementById("graph-top-nodes");
    dom.relatedMemories = document.getElementById("graph-related-memories");
    dom.retrievalList = document.getElementById("graph-retrieval-list");
    dom.visibleNodes = document.getElementById("graph-visible-node-count");
    dom.visibleEdges = document.getElementById("graph-visible-edge-count");
    dom.visibleEntries = document.getElementById("graph-visible-entry-count");
    dom.visibleMemories = document.getElementById("graph-visible-memory-count");
    dom.routeLabel = document.getElementById("graph-route-label");

    dom.tabButton.addEventListener("click", () => {
      window.setTimeout(() => {
        resizeGraphScene();
        if (!state.hasLoadedOverview && !state.isLoading) {
          fetchOverview();
        }
      }, 0);
    });

    dom.searchButton.addEventListener("click", runQuery);
    dom.focusButton.addEventListener("click", focusMemory);
    dom.overviewButton.addEventListener("click", fetchOverview);
    dom.queryInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        runQuery();
      }
    });
    dom.memoryInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        focusMemory();
      }
    });

    dom.topNodes.addEventListener("click", onPanelClick);
    dom.relatedMemories.addEventListener("click", onPanelClick);
    dom.retrievalList.addEventListener("click", onPanelClick);

    initResizeObserver();
    setCanvasMessage(
      typeof window.ForceGraph3D === "function"
        ? "点击“最近概览”加载图谱，或直接输入检索词。"
        : "3D 图谱组件未加载，请刷新页面并检查静态资源。",
      false
    );
  }

  function initResizeObserver() {
    window.addEventListener("resize", resizeGraphScene, { passive: true });
    if (typeof window.ResizeObserver !== "function" || !dom.canvas) {
      return;
    }
    state.resizeObserver = new ResizeObserver(() => {
      resizeGraphScene();
    });
    state.resizeObserver.observe(dom.canvas);
  }

  function getToken() {

    return localStorage.getItem("lmem_token") || "";
  }

  function getFilters() {
    return {
      session_id: dom.sessionInput.value.trim() || null,
      persona_id: dom.personaInput.value.trim() || null,
    };
  }

  async function requestGraph(path, options = {}) {
    const token = getToken();
    if (!token) {
      throw new Error("当前会话未登录，请先登录 WebUI。");
    }

    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${token}`);
    if (options.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(path, {
      method: options.method || "GET",
      headers,
      body:
        options.body === undefined
          ? undefined
          : typeof options.body === "string"
            ? options.body
            : JSON.stringify(options.body),
      credentials: "same-origin",
    });

    const data = await response
      .json()
      .catch(() => ({ success: false, error: "服务器响应格式错误" }));
    if (!response.ok) {
      throw new Error(data.detail || data.error || data.message || "图谱请求失败");
    }
    if (!data.success) {
      throw new Error(data.error || "图谱请求失败");
    }
    return data.data || {};
  }

  async function fetchOverview() {
    setLoading(true, "正在加载最近图谱概览...");
    try {
      const filters = getFilters();
      const params = new URLSearchParams();
      if (filters.session_id) {
        params.set("session_id", filters.session_id);
      }
      if (filters.persona_id) {
        params.set("persona_id", filters.persona_id);
      }
      const query = params.toString();
      const payload = await requestGraph(`/api/graph/overview${query ? `?${query}` : ""}`);
      state.hasLoadedOverview = true;
      renderPayload(payload, { focusSelection: !state.sceneReady });
    } catch (error) {
      renderError(error.message || "无法加载图谱概览");
    } finally {
      setLoading(false);
    }
  }

  async function runQuery() {
    const query = dom.queryInput.value.trim();
    if (!query) {
      fetchOverview();
      return;
    }

    setLoading(true, `正在检索“${query}”相关图谱...`);
    try {
      const filters = getFilters();
      const payload = await requestGraph("/api/graph/query", {
        method: "POST",
        body: {
          query,
          session_id: filters.session_id,
          persona_id: filters.persona_id,
        },
      });
      renderPayload(payload, { focusSelection: true });
    } catch (error) {
      renderError(error.message || "图谱检索失败");
    } finally {
      setLoading(false);
    }
  }

  async function focusMemory() {
    const memoryIdText = dom.memoryInput.value.trim();
    if (!memoryIdText) {
      renderError("请输入要定位的记忆 ID。");
      return;
    }

    const memoryId = Number.parseInt(memoryIdText, 10);
    if (Number.isNaN(memoryId)) {
      renderError("记忆 ID 必须是整数。");
      return;
    }

    setLoading(true, `正在聚焦记忆 #${memoryId} 的关系图...`);
    try {
      const filters = getFilters();
      const payload = await requestGraph("/api/graph/query", {
        method: "POST",
        body: {
          memory_id: memoryId,
          session_id: filters.session_id,
          persona_id: filters.persona_id,
        },
      });
      renderPayload(payload, { memoryId, focusSelection: true });
    } catch (error) {
      renderError(error.message || "定位记忆失败");
    } finally {
      setLoading(false);
    }
  }

  function setLoading(isLoading, message = "") {
    state.isLoading = isLoading;
    dom.searchButton.disabled = isLoading;
    dom.focusButton.disabled = isLoading;
    dom.overviewButton.disabled = isLoading;
    if (isLoading) {
      setCanvasMessage(message || "图谱载入中...", true);
    }
  }
  function renderPayload(payload, options = {}) {
    state.payload = payload;
    state.graphIndex = buildGraphIndex(payload.snapshot || {});

    if (options.memoryId) {
      state.selectedMemoryId = options.memoryId;
      state.selectedNodeId = null;
    } else {
      ensureSelection(payload);
    }

    if (!payload.enabled) {
      renderDisabled();
      return;
    }

    renderStatus(payload);
    renderStats(payload);
    renderLegend(payload);
    renderCanvas(payload, {
      focusSelection:
        options.focusSelection === undefined
          ? payload.mode === "query" || payload.mode === "memory_focus" || !state.sceneReady
          : Boolean(options.focusSelection),
    });
    renderSelectionPanels(payload);
  }

  function ensureSelection(payload) {
    if (state.graphIndex?.nodeMap.has(state.selectedNodeId)) {
      state.selectedMemoryId = null;
      return;
    }
    if (state.graphIndex?.memoryMap.has(state.selectedMemoryId)) {
      state.selectedNodeId = null;
      return;
    }

    state.selectedNodeId = null;
    state.selectedMemoryId = null;

    const matchedNodeIds = payload.matched_node_ids || [];
    const firstMatchedNodeId = matchedNodeIds.find((nodeId) =>
      state.graphIndex.nodeMap.has(nodeId)
    );
    if (firstMatchedNodeId !== undefined) {
      state.selectedNodeId = firstMatchedNodeId;
      return;
    }

    const firstRetrievedMemory = payload.retrieval?.items?.[0]?.memory_id;
    if (state.graphIndex.memoryMap.has(firstRetrievedMemory)) {
      state.selectedMemoryId = firstRetrievedMemory;
      return;
    }

    const firstTopNode = payload.top_nodes?.[0]?.id;
    if (state.graphIndex.nodeMap.has(firstTopNode)) {
      state.selectedNodeId = firstTopNode;
      return;
    }

    const firstMemory = payload.snapshot?.memories?.[0]?.memory_id;
    if (state.graphIndex.memoryMap.has(firstMemory)) {
      state.selectedMemoryId = firstMemory;
    }
  }

  function renderDisabled() {
    state.sceneReady = false;
    dom.modeBadge.textContent = "图记忆未启用";
    dom.statusLine.textContent = "当前实例未启用图记忆功能，请先开启图记忆并完成索引。";
    renderStats({ summary: {} });
    dom.routeLabel.textContent = "未启用";
    dom.legend.innerHTML = '<span class="graph-legend-chip muted">暂无图数据</span>';
    dom.topNodes.innerHTML = emptyPanel("__PLACEHOLDER__");
    dom.relatedMemories.innerHTML = emptyPanel("暂无可展示的图记忆");
    dom.retrievalList.innerHTML = emptyPanel("点击“最近概览”加载图谱，或直接输入检索词。");
    dom.inspector.innerHTML = emptyPanel("请选择节点或记忆查看详细信息。");
    clearGraphScene();
    setCanvasMessage("当前实例尚未启用图记忆。", false);
  }

  function renderError(message) {
    state.sceneReady = false;
    dom.modeBadge.textContent = "图谱加载失败";
    dom.statusLine.textContent = message;
    dom.legend.innerHTML = '<span class="graph-legend-chip danger">请求失败</span>';
    dom.topNodes.innerHTML = emptyPanel("暂无数据");
    dom.relatedMemories.innerHTML = emptyPanel("暂无数据");
    dom.retrievalList.innerHTML = emptyPanel("暂无数据");
    dom.inspector.innerHTML = emptyPanel(message);
    clearGraphScene();
    setCanvasMessage(message, false);
  }

  function renderStatus(payload) {
    const modeText = MODE_LABELS[payload.mode] || "图谱视图";
    dom.modeBadge.textContent = modeText;

    const filters = payload.filters || {};
    const filterParts = [];
    if (filters.session_id) {
      filterParts.push(`会话 ${filters.session_id}`);
    }
    if (filters.persona_id) {
      filterParts.push(`人格 ${filters.persona_id}`);
    }

    let line = "展示图记忆中的核心连接。";
    if (payload.mode === "query" && payload.query) {
      line = `当前展示 “${payload.query}” 的双路四模式召回对应子图。`;
    } else if (payload.mode === "memory_focus" && payload.memory_id !== null) {
      line = `当前聚焦记忆 #${payload.memory_id} 的关系子图。`;
    }
    if (filterParts.length) {
      line += ` 过滤条件：${filterParts.join(" · ")}`;
    }
    dom.statusLine.textContent = line;
    dom.routeLabel.textContent =
      payload.mode === "query" ? "文档 + 图 · 关键词 + 向量" : "图谱浏览";
  }

  function renderStats(payload) {
    const summary = payload.summary || {};
    dom.visibleNodes.textContent = summary.visible_node_count || 0;
    dom.visibleEdges.textContent = summary.visible_edge_count || 0;
    dom.visibleEntries.textContent = summary.visible_entry_count || 0;
    dom.visibleMemories.textContent = summary.visible_memory_count || 0;
  }

  function renderLegend(payload) {
    const summary = payload.summary || {};
    const nodeTypeBreakdown = summary.node_type_breakdown || {};
    const relationBreakdown = summary.relation_breakdown || {};

    const nodeChips = Object.entries(nodeTypeBreakdown)
      .sort((left, right) => right[1] - left[1])
      .map(
        ([type, count]) => `
          <span class="graph-legend-chip graph-node-${escapeHTML(type)}">
            ${escapeHTML(typeLabel(type))} · ${count}
          </span>
        `
      );

    const relationChips = Object.entries(relationBreakdown)
      .sort((left, right) => right[1] - left[1])
      .slice(0, 4)
      .map(
        ([type, count]) => `
          <span class="graph-legend-chip secondary">
            ${escapeHTML(relationLabel(type))} · ${count}
          </span>
        `
      );

    const chips = [...nodeChips, ...relationChips];
    dom.legend.innerHTML = chips.length
      ? chips.join("")
      : '<span class="graph-legend-chip muted">暂无图谱连接</span>';
  }

  function renderCanvas(payload, options = {}) {
    const nodes = payload.snapshot?.nodes || [];
    if (!nodes.length) {
      state.sceneReady = false;
      clearGraphScene();
      setCanvasMessage("当前范围内暂无可视化图数据。", false);
      return;
    }

    if (!ensureGraphScene()) {
      state.sceneReady = false;
      setCanvasMessage("当前页面未能加载 3D 图谱组件，请刷新页面后重试。", false);
      return;
    }

    captureScenePositions();
    const sceneData = buildGraphSceneData(payload);
    state.graph3d.graphData(sceneData);
    if (state.resumeFrame !== null) {
      window.cancelAnimationFrame(state.resumeFrame);
      state.resumeFrame = null;
    }
    state.resumeFrame = window.requestAnimationFrame(() => {
      if (!state.graph3d) {
        return;
      }
      if (typeof state.graph3d.d3ReheatSimulation === "function") {
        state.graph3d.d3ReheatSimulation();
      }
      if (typeof state.graph3d.resumeAnimation === "function") {
        state.graph3d.resumeAnimation();
      }
      state.resumeFrame = null;
    });
    resizeGraphScene();
    dom.canvas.classList.add("is-ready");
    dom.canvasShell?.classList.add("is-ready");
    state.sceneReady = true;
    setCanvasMessage("", false);

    if (options.focusSelection) {
      queueCameraFocus();
    }
  }

  function renderSelectionPanels(payload) {
    renderTopNodes(payload);
    renderRelatedMemories(payload);
    renderRetrieval(payload);
    renderInspector();
  }

  function renderTopNodes(payload) {
    const topNodes = payload.top_nodes || [];
    if (!topNodes.length) {
      dom.topNodes.innerHTML = emptyPanel("暂无核心节点");
      return;
    }

    dom.topNodes.innerHTML = topNodes
      .map(
        (node) => `
          <button
            class="graph-chip ${state.selectedNodeId === Number(node.id) ? "is-active" : ""}"
            data-node-select="${node.id}"
          >
            <span class="graph-chip-title">${escapeHTML(truncate(node.label || "未命名节点", 18))}</span>
            <span class="graph-chip-meta">${escapeHTML(typeLabel(node.type))} · 度 ${Number(node.degree || 0)}</span>
          </button>
        `
      )
      .join("");
  }

  function renderRelatedMemories(payload) {
    const memories = payload.snapshot?.memories || [];
    if (!memories.length) {
      dom.relatedMemories.innerHTML = emptyPanel("暂无关联记忆");
      return;
    }

    const selection = getSelectionContext();
    const preferredMemoryIds = selection.highlightMemoryIds;
    const sortedMemories = [...memories].sort((left, right) => {
      const leftPreferred = preferredMemoryIds.has(Number(left.memory_id)) ? 1 : 0;
      const rightPreferred = preferredMemoryIds.has(Number(right.memory_id)) ? 1 : 0;
      if (leftPreferred !== rightPreferred) {
        return rightPreferred - leftPreferred;
      }
      const leftScore = Number(left.retrieval?.final_score || -1);
      const rightScore = Number(right.retrieval?.final_score || -1);
      if (leftScore !== rightScore) {
        return rightScore - leftScore;
      }
      return Number(right.entry_count || 0) - Number(left.entry_count || 0);
    });

    dom.relatedMemories.innerHTML = sortedMemories
      .slice(0, 8)
      .map((memory) => {
        const memoryId = Number(memory.memory_id);
        const selectedClass = state.selectedMemoryId === memoryId ? "is-active" : "";
        const scoreBadge = memory.retrieval
          ? `<span class="graph-score-pill">${formatScore(memory.retrieval.final_score)}</span>`
          : "";
        return `
          <article class="graph-memory-card ${selectedClass}" data-memory-select="${memoryId}">
            <div class="graph-memory-card-header">
              <span class="graph-memory-id">#${memoryId}</span>
              ${scoreBadge}
            </div>
            <h4 class="graph-memory-title">${escapeHTML(truncate(memory.summary || "无摘要", 46))}</h4>
            <div class="graph-memory-metrics">
              <span>节点 ${memory.node_count || 0}</span>
              <span>条目 ${memory.entry_count || 0}</span>
              <span>关系 ${memory.edge_count || 0}</span>
            </div>
            <button class="btn btn-ghost graph-memory-action" type="button" data-memory-focus="${memoryId}">聚焦此记忆</button>
          </article>
        `;
      })
      .join("");
  }

  function renderRetrieval(payload) {
    const items = payload.retrieval?.items || [];
    if (!items.length) {
      dom.retrievalList.innerHTML = emptyPanel(
        "执行检索后，这里会展示文档 / 图 × 关键词 / 向量的召回细节。"
      );
      return;
    }

    dom.retrievalList.innerHTML = items
      .slice(0, 6)
      .map((item, index) => {
        const bars = SCORE_LABELS.map(([key, label]) => {
          const value = clamp01(Number(item.score_breakdown?.[key] || 0));
          return `
            <div class="graph-score-row">
              <span>${escapeHTML(label)}</span>
              <div class="graph-score-track"><span style="width:${(value * 100).toFixed(1)}%"></span></div>
              <strong>${formatScore(value)}</strong>
            </div>
          `;
        }).join("");
        return `
          <article class="graph-retrieval-card ${state.selectedMemoryId === Number(item.memory_id) ? "is-active" : ""}" data-memory-select="${item.memory_id}">
            <div class="graph-retrieval-header">
              <div>
                <span class="graph-retrieval-rank">TOP ${index + 1}</span>
                <h4>记忆 #${item.memory_id}</h4>
              </div>
              <span class="graph-score-pill strong">${formatScore(item.final_score)}</span>
            </div>
            <p class="graph-retrieval-content">${escapeHTML(truncate(item.content || "", 72))}</p>
            <div class="graph-score-grid">${bars}</div>
          </article>
        `;
      })
      .join("");
  }
  function renderInspector() {
    const selection = getSelectionContext();
    if (!selection.type) {
      dom.inspector.innerHTML = emptyPanel("点击节点、记忆卡片或召回结果查看详细信息。");
      return;
    }

    if (selection.type === "node") {
      const node = selection.item;
      const entries = selection.entries.slice(0, 4);
      const relatedMemories = selection.memories.slice(0, 4);
      dom.inspector.innerHTML = `
        <div class="graph-inspector-header">
          <span class="graph-detail-badge graph-node-${escapeHTML(node.type || "other")}">${escapeHTML(typeLabel(node.type))}</span>
          <h3>${escapeHTML(node.label || "未命名节点")}</h3>
          <p>${escapeHTML(node.canonical_value || node.label || "")}</p>
        </div>
        <div class="graph-detail-grid">
          <div><span>关联记忆</span><strong>${node.memory_count || 0}</strong></div>
          <div><span>连接度</span><strong>${node.degree || 0}</strong></div>
          <div><span>命中条目</span><strong>${node.entry_count || 0}</strong></div>
          <div><span>权重</span><strong>${Number(node.weight || 0).toFixed(2)}</strong></div>
        </div>
        <div class="graph-detail-section">
          <h4>相关记忆</h4>
          ${relatedMemories.length ? relatedMemories.map((memory) => `
            <button class="graph-inline-item" data-memory-select="${memory.memory_id}">
              <span>#${memory.memory_id}</span>
              <strong>${escapeHTML(truncate(memory.summary || "无摘要", 30))}</strong>
            </button>
          `).join("") : '<p class="graph-muted-copy">暂无相关记忆</p>'}
        </div>
        <div class="graph-detail-section">
          <h4>相关条目</h4>
          ${entries.length ? entries.map((entry) => `
            <article class="graph-entry-card">
              <span class="graph-entry-type">${escapeHTML(typeLabel(entry.entry_type))}</span>
              <p>${escapeHTML(truncate(entry.content || "", 120))}</p>
            </article>
          `).join("") : '<p class="graph-muted-copy">暂无相关条目</p>'}
        </div>
      `;
      return;
    }

    const memory = selection.item;
    const entries = selection.entries.slice(0, 4);
    const nodeIds = [...selection.highlightNodeIds];
    dom.inspector.innerHTML = `
      <div class="graph-inspector-header">
        <span class="graph-detail-badge secondary">记忆 #${memory.memory_id}</span>
        <h3>${escapeHTML(truncate(memory.summary || "无摘要", 56))}</h3>
        <p>${escapeHTML(memory.session_id || "未设置会话")}</p>
      </div>
      <div class="graph-detail-grid">
        <div><span>节点</span><strong>${memory.node_count || 0}</strong></div>
        <div><span>条目</span><strong>${memory.entry_count || 0}</strong></div>
        <div><span>关系</span><strong>${memory.edge_count || 0}</strong></div>
        <div><span>重要性</span><strong>${formatImportance(memory.importance)}</strong></div>
      </div>
      <div class="graph-detail-section">
        <h4>节点分布</h4>
        <div class="graph-inline-chips">
          ${nodeIds.length ? nodeIds.slice(0, 8).map((nodeId) => {
            const node = state.graphIndex.nodeMap.get(nodeId);
            if (!node) {
              return "";
            }
            return `<button class="graph-inline-chip" data-node-select="${nodeId}">${escapeHTML(truncate(node.label || "未命名节点", 18))}</button>`;
          }).join("") : '<span class="graph-muted-copy">暂无节点</span>'}
        </div>
      </div>
      <div class="graph-detail-section">
        <h4>图谱条目</h4>
        ${entries.length ? entries.map((entry) => `
          <article class="graph-entry-card">
            <span class="graph-entry-type">${escapeHTML(typeLabel(entry.entry_type))}</span>
            <p>${escapeHTML(truncate(entry.content || "", 120))}</p>
          </article>
        `).join("") : '<p class="graph-muted-copy">暂无图谱条目</p>'}
      </div>
    `;
  }

  function onPanelClick(event) {
    const focusButton = event.target.closest("[data-memory-focus]");
    if (focusButton) {
      dom.memoryInput.value = focusButton.dataset.memoryFocus || "";
      focusMemory();
      return;
    }

    const memoryButton = event.target.closest("[data-memory-select]");
    if (memoryButton) {
      selectMemory(Number(memoryButton.dataset.memorySelect), { focusCamera: true });
      return;
    }

    const nodeButton = event.target.closest("[data-node-select]");
    if (nodeButton) {
      selectNode(Number(nodeButton.dataset.nodeSelect), { focusCamera: true });
    }
  }

  function clearSelection() {
    if (!state.payload || (state.selectedNodeId === null && state.selectedMemoryId === null)) {
      return;
    }
    state.selectedNodeId = null;
    state.selectedMemoryId = null;
    renderCanvas(state.payload, { focusSelection: false });
    renderSelectionPanels(state.payload);
  }

  function selectNode(nodeId, options = {}) {
    if (!state.graphIndex?.nodeMap.has(nodeId)) {
      return;
    }
    state.selectedNodeId = nodeId;
    state.selectedMemoryId = null;
    renderCanvas(state.payload, { focusSelection: Boolean(options.focusCamera) });
    renderSelectionPanels(state.payload);
  }

  function selectMemory(memoryId, options = {}) {
    if (!state.graphIndex?.memoryMap.has(memoryId)) {
      return;
    }
    state.selectedMemoryId = memoryId;
    state.selectedNodeId = null;
    renderCanvas(state.payload, { focusSelection: Boolean(options.focusCamera) });
    renderSelectionPanels(state.payload);
  }

  function buildGraphIndex(snapshot) {
    const nodes = snapshot.nodes || [];
    const edges = snapshot.edges || [];
    const entries = snapshot.entries || [];
    const memories = snapshot.memories || [];

    const nodeMap = new Map(nodes.map((node) => [Number(node.id), node]));
    const memoryMap = new Map(memories.map((memory) => [Number(memory.memory_id), memory]));
    const memoryToEntries = new Map();
    const memoryToNodes = new Map();
    const nodeToMemories = new Map();
    const nodeToEntries = new Map();
    const neighborMap = new Map();

    const ensureSet = (map, key) => {
      if (!map.has(key)) {
        map.set(key, new Set());
      }
      return map.get(key);
    };

    entries.forEach((entry) => {
      const memoryId = Number(entry.memory_id);
      if (!memoryToEntries.has(memoryId)) {
        memoryToEntries.set(memoryId, []);
      }
      memoryToEntries.get(memoryId).push(entry);
      (entry.node_ids || []).forEach((nodeIdValue) => {
        const nodeId = Number(nodeIdValue);
        ensureSet(memoryToNodes, memoryId).add(nodeId);
        ensureSet(nodeToMemories, nodeId).add(memoryId);
        if (!nodeToEntries.has(nodeId)) {
          nodeToEntries.set(nodeId, []);
        }
        nodeToEntries.get(nodeId).push(entry);
      });
    });

    edges.forEach((edge) => {
      const source = Number(edge.source);
      const target = Number(edge.target);
      const memoryId = Number(edge.memory_id);
      ensureSet(memoryToNodes, memoryId).add(source);
      ensureSet(memoryToNodes, memoryId).add(target);
      ensureSet(nodeToMemories, source).add(memoryId);
      ensureSet(nodeToMemories, target).add(memoryId);
      ensureSet(neighborMap, source).add(target);
      ensureSet(neighborMap, target).add(source);
    });

    return {
      nodeMap,
      memoryMap,
      memoryToEntries,
      memoryToNodes,
      nodeToMemories,
      nodeToEntries,
      neighborMap,
      edges,
      entries,
    };
  }

  function getSelectionContext() {
    if (!state.graphIndex) {
      return {
        type: null,
        highlightNodeIds: new Set(),
        highlightMemoryIds: new Set(),
        item: null,
        entries: [],
        memories: [],
      };
    }

    if (state.selectedNodeId !== null && state.graphIndex.nodeMap.has(state.selectedNodeId)) {
      const node = state.graphIndex.nodeMap.get(state.selectedNodeId);
      const memoryIds = [...(state.graphIndex.nodeToMemories.get(state.selectedNodeId) || new Set())];
      const memories = memoryIds
        .map((memoryId) => state.graphIndex.memoryMap.get(memoryId))
        .filter(Boolean);
      const neighborIds = state.graphIndex.neighborMap.get(state.selectedNodeId) || new Set();
      const highlightNodeIds = new Set([state.selectedNodeId, ...neighborIds]);
      return {
        type: "node",
        item: node,
        entries: state.graphIndex.nodeToEntries.get(state.selectedNodeId) || [],
        memories,
        highlightNodeIds,
        highlightMemoryIds: new Set(memoryIds),
      };
    }

    if (state.selectedMemoryId !== null && state.graphIndex.memoryMap.has(state.selectedMemoryId)) {
      const memory = state.graphIndex.memoryMap.get(state.selectedMemoryId);
      const nodeIds = state.graphIndex.memoryToNodes.get(state.selectedMemoryId) || new Set();
      return {
        type: "memory",
        item: memory,
        entries: state.graphIndex.memoryToEntries.get(state.selectedMemoryId) || [],
        memories: [memory],
        highlightNodeIds: new Set(nodeIds),
        highlightMemoryIds: new Set([state.selectedMemoryId]),
      };
    }

    return {
      type: null,
      highlightNodeIds: new Set(),
      highlightMemoryIds: new Set(),
      item: null,
      entries: [],
      memories: [],
    };
  }

  function ensureGraphScene() {
    if (state.graph3d) {
      return true;
    }
    if (typeof window.ForceGraph3D !== "function" || !dom.canvas) {
      return false;
    }

    dom.canvas.innerHTML = "";
    const graph = window.ForceGraph3D()(dom.canvas)
      .backgroundColor("#050816")
      .showNavInfo(false)
      .nodeRelSize(3.8)
      .nodeVal((node) => node.__size)
      .nodeColor((node) => node.__color)
      .nodeLabel((node) => node.__label)
      .nodeOpacity(0.98)
      .nodeResolution(20)
      .linkColor((link) => link.__color)
      .linkWidth((link) => link.__width)
      .linkOpacity(0.76)
      .linkResolution(10)
      .linkCurvature((link) => link.__curvature)
      .linkLabel((link) => link.__label)
      .linkDirectionalArrowLength((link) => link.__arrowLength)
      .linkDirectionalArrowColor((link) => link.__particleColor)
      .linkDirectionalParticles((link) => link.__particles)
      .linkDirectionalParticleWidth((link) => link.__particleWidth)
      .linkDirectionalParticleColor((link) => link.__particleColor)
      .linkDirectionalParticleSpeed((link) => link.__particleSpeed)
      .onNodeClick((node) => {
        selectNode(Number(node.id), { focusCamera: true });
      })
      .onBackgroundClick(() => {
        clearSelection();
      })
      .warmupTicks(90)
      .cooldownTime(2200)
      .onEngineStop(() => {
        captureScenePositions();
      });

    if (typeof graph.pauseAnimation === "function") {
      graph.pauseAnimation();
    }

    try {
      const chargeForce = graph.d3Force("charge");
      if (chargeForce && typeof chargeForce.strength === "function") {
        chargeForce.strength(-260);
      }
    } catch (error) {
      console.warn("Failed to configure graph charge force", error);
    }

    try {
      const controls = graph.controls();
      if (controls) {
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.autoRotate = true;
        controls.autoRotateSpeed = 0.35;
        controls.minDistance = 80;
        controls.maxDistance = 1600;
      }
    } catch (error) {
      console.warn("Failed to configure graph controls", error);
    }

    try {
      const renderer = graph.renderer();
      if (renderer && typeof renderer.setPixelRatio === "function") {
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      }
    } catch (error) {
      console.warn("Failed to configure graph renderer", error);
    }

    state.graph3d = graph;
    resizeGraphScene();
    return true;
  }

  function clearGraphScene() {
    window.clearTimeout(state.focusTimer);
    if (state.resumeFrame !== null) {
      window.cancelAnimationFrame(state.resumeFrame);
      state.resumeFrame = null;
    }
    if (state.graph3d && typeof state.graph3d.graphData === "function") {
      if (typeof state.graph3d.pauseAnimation === "function") {
        state.graph3d.pauseAnimation();
      }
      state.graph3d.graphData({ nodes: [], links: [] });
    } else if (dom.canvas) {
      dom.canvas.innerHTML = "";
    }
    dom.canvas?.classList.remove("is-ready");
    dom.canvasShell?.classList.remove("is-ready");
  }

  function getCanvasSize() {
    const width = Math.max(
      360,
      Math.floor(dom.canvas?.clientWidth || dom.canvas?.getBoundingClientRect?.().width || 960)
    );
    const height = Math.max(
      440,
      Math.floor(dom.canvas?.clientHeight || dom.canvas?.getBoundingClientRect?.().height || 620)
    );
    return { width, height };
  }

  function resizeGraphScene() {
    if (!state.graph3d) {
      return;
    }
    const { width, height } = getCanvasSize();
    state.graph3d.width(width).height(height);
    try {
      const renderer = state.graph3d.renderer();
      if (renderer && typeof renderer.setPixelRatio === "function") {
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      }
    } catch (error) {
      console.warn("Failed to resize graph renderer", error);
    }
  }

  function captureScenePositions() {
    if (!state.graph3d || typeof state.graph3d.graphData !== "function") {
      return;
    }
    const graphData = state.graph3d.graphData();
    const nodes = graphData?.nodes || [];
    nodes.forEach((node) => {
      const id = Number(node.id);
      if (
        Number.isFinite(id) &&
        Number.isFinite(node.x) &&
        Number.isFinite(node.y) &&
        Number.isFinite(node.z)
      ) {
        state.scenePositions.set(id, {
          x: node.x,
          y: node.y,
          z: node.z,
          vx: Number.isFinite(node.vx) ? node.vx : 0,
          vy: Number.isFinite(node.vy) ? node.vy : 0,
          vz: Number.isFinite(node.vz) ? node.vz : 0,
        });
      }
    });
  }

  function buildGraphSceneData(payload) {
    const snapshotNodes = payload.snapshot?.nodes || [];
    const snapshotEdges = payload.snapshot?.edges || [];
    const selection = getSelectionContext();
    const highlightedNodes = selection.highlightNodeIds;
    const highlightedMemories = selection.highlightMemoryIds;
    const maxWeight = Math.max(...snapshotNodes.map((node) => Number(node.weight || 1)), 1);
    const seededPositions = buildSeedPositions(snapshotNodes, maxWeight);

    const nodes = snapshotNodes.map((node) => {
      const nodeId = Number(node.id);
      const weightRatio = clamp01(Number(node.weight || 0) / maxWeight);
      const isSelected = state.selectedNodeId === nodeId;
      const isHighlighted = highlightedNodes.has(nodeId);
      const isMuted = highlightedNodes.size > 0 && !isHighlighted;
      const memoryRatio = clamp01(Number(node.memory_count || 0) / 8);
      const size =
        2.6 +
        weightRatio * 6.8 +
        memoryRatio * 1.8 +
        (isSelected ? 2.4 : isHighlighted ? 1.2 : 0);
      const position = state.scenePositions.get(nodeId) || seededPositions.get(nodeId) || { x: 0, y: 0, z: 0 };
      return {
        ...node,
        id: nodeId,
        x: position.x,
        y: position.y,
        z: position.z,
        vx: position.vx || 0,
        vy: position.vy || 0,
        vz: position.vz || 0,
        __size: size,
        __color: nodeColor(node.type, { isSelected, isHighlighted, isMuted }),
        __label: buildNodeTooltip(node),
      };
    });

    const validNodeIds = new Set(nodes.map((node) => Number(node.id)));
    const groupedPairs = new Map();
    snapshotEdges.forEach((edge, index) => {
      const source = Number(edge.source);
      const target = Number(edge.target);
      if (!validNodeIds.has(source) || !validNodeIds.has(target)) {
        return;
      }
      const pairKey = edgePairKey(source, target);
      if (!groupedPairs.has(pairKey)) {
        groupedPairs.set(pairKey, []);
      }
      groupedPairs.get(pairKey).push(index);
    });

    const links = snapshotEdges
      .map((edge, index) => {
        const source = Number(edge.source);
        const target = Number(edge.target);
        if (!validNodeIds.has(source) || !validNodeIds.has(target)) {
          return null;
        }

        const pairKey = edgePairKey(source, target);
        const siblings = groupedPairs.get(pairKey) || [index];
        const siblingIndex = siblings.indexOf(index);
        const centerOffset = (siblings.length - 1) / 2;
        const baseCurvature =
          siblings.length === 1
            ? source <= target
              ? 0.06
              : -0.06
            : (siblingIndex - centerOffset) * 0.18;
        const isActive =
          highlightedNodes.size === 0 ||
          (highlightedNodes.has(source) && highlightedNodes.has(target));
        const isMemoryHighlighted = highlightedMemories.has(Number(edge.memory_id));
        const relationBaseColor = relationColor(edge.relation_type);
        return {
          ...edge,
          id: `${pairKey}:${index}:${edge.memory_id || "na"}`,
          source,
          target,
          __color: linkColor(relationBaseColor, { isActive, isMemoryHighlighted }),
          __label: `${relationLabel(edge.relation_type)} · 记忆 #${edge.memory_id}`,
          __width: isMemoryHighlighted ? 2.6 : isActive ? 1.2 : 0.32,
          __curvature: baseCurvature,
          __arrowLength: isMemoryHighlighted ? 5.2 : isActive ? 3.2 : 0,
          __particles: isMemoryHighlighted ? 5 : isActive ? 2 : 0,
          __particleWidth: isMemoryHighlighted ? 3.6 : 2.2,
          __particleColor: toRGBA(mixHex(relationBaseColor, "#ffffff", isMemoryHighlighted ? 0.16 : 0.08), 0.92),
          __particleSpeed: isMemoryHighlighted ? 0.0105 : 0.0048,
        };
      })
      .filter(Boolean);

    return { nodes, links };
  }

  function buildSeedPositions(nodes, maxWeight) {
    const groupedNodes = { summary: [], topic: [], person: [], fact: [], other: [] };
    nodes.forEach((node) => {
      const groupKey = normalizeNodeType(node.type);
      groupedNodes[groupKey].push(node);
    });

    const positions = new Map();
    Object.entries(groupedNodes).forEach(([groupKey, items]) => {
      items.sort((left, right) => Number(right.weight || 0) - Number(left.weight || 0));
      items.forEach((node, index) => {
        positions.set(Number(node.id), seedNodePosition(node, groupKey, index, items.length, maxWeight));
      });
    });
    return positions;
  }

  function seedNodePosition(node, groupKey, index, total, maxWeight) {
    const orbit = TYPE_ORBITS[groupKey] || TYPE_ORBITS.other;
    const ratio = total <= 1 ? 0.5 : index / total;
    const weightRatio = clamp01(Number(node.weight || 0) / maxWeight);
    const radius = orbit.inner + (1 - weightRatio) * (orbit.outer - orbit.inner) + (index % 3) * 12;
    const theta = orbit.phase + ratio * Math.PI * 2.1;
    const phi = Math.PI * (orbit.polarStart + ratio * (orbit.polarEnd - orbit.polarStart));
    return {
      x: Math.cos(theta) * Math.sin(phi) * radius,
      y: Math.cos(phi) * radius * 0.78,
      z: Math.sin(theta) * Math.sin(phi) * radius,
    };
  }

  function queueCameraFocus() {
    window.clearTimeout(state.focusTimer);
    state.focusTimer = window.setTimeout(() => {
      focusGraphCameraOnSelection(900);
    }, 180);
  }

  function focusGraphCameraOnSelection(duration = 900) {
    if (!state.graph3d) {
      return;
    }
    if (state.selectedNodeId !== null) {
      focusGraphCameraOnNode(state.selectedNodeId, duration);
      return;
    }

    const selection = getSelectionContext();
    const centroid = computeSelectionCentroid(selection.highlightNodeIds);
    if (!centroid) {
      return;
    }

    const distance = Math.max(180, 120 + selection.highlightNodeIds.size * 10);
    const magnitude = Math.hypot(centroid.x, centroid.y, centroid.z);
    const ratio = magnitude > 1 ? 1 + distance / magnitude : 1;
    state.graph3d.cameraPosition(
      {
        x: centroid.x * ratio + distance * 0.34,
        y: centroid.y * ratio + distance * 0.18,
        z: centroid.z * ratio + distance * 0.62,
      },
      centroid,
      duration
    );
  }

  function focusGraphCameraOnNode(nodeId, duration = 900) {
    if (!state.graph3d || typeof state.graph3d.graphData !== "function") {
      return;
    }
    const graphData = state.graph3d.graphData();
    const node = (graphData?.nodes || []).find((item) => Number(item.id) === Number(nodeId));
    if (!node) {
      return;
    }

    const distance = Math.max(150, Number(node.__size || 0) * 18);
    const magnitude = Math.hypot(node.x || 0, node.y || 0, node.z || 0);
    const ratio = magnitude > 1 ? 1 + distance / magnitude : 1;
    state.graph3d.cameraPosition(
      {
        x: (node.x || 0) * ratio + distance * 0.18,
        y: (node.y || 0) * ratio + distance * 0.08,
        z: (node.z || 0) * ratio + distance * 0.42,
      },
      {
        x: node.x || 0,
        y: node.y || 0,
        z: node.z || 0,
      },
      duration
    );
  }

  function computeSelectionCentroid(nodeIds) {
    if (!state.graph3d || typeof state.graph3d.graphData !== "function" || !nodeIds.size) {
      return null;
    }
    const lookup = new Map(
      (state.graph3d.graphData()?.nodes || []).map((node) => [Number(node.id), node])
    );
    let total = 0;
    const sum = { x: 0, y: 0, z: 0 };
    nodeIds.forEach((nodeId) => {
      const node = lookup.get(Number(nodeId));
      if (
        !node ||
        !Number.isFinite(node.x) ||
        !Number.isFinite(node.y) ||
        !Number.isFinite(node.z)
      ) {
        return;
      }
      total += 1;
      sum.x += node.x;
      sum.y += node.y;
      sum.z += node.z;
    });
    if (!total) {
      return null;
    }
    return {
      x: sum.x / total,
      y: sum.y / total,
      z: sum.z / total,
    };
  }

  function buildNodeTooltip(node) {
    const typeKey = normalizeNodeType(node.type);
    const detailText = node.canonical_value || node.label || "未命名节点";
    return `
      <div class="graph-tooltip">
        <span class="graph-tooltip-badge graph-node-${escapeHTML(typeKey)}">${escapeHTML(typeLabel(node.type))}</span>
        <strong>${escapeHTML(node.label || "未命名节点")}</strong>
        <span>${escapeHTML(truncate(detailText, 72))}</span>
        <small>记忆 ${Number(node.memory_count || 0)} · 关系 ${Number(node.degree || 0)} · 条目 ${Number(node.entry_count || 0)}</small>
      </div>
    `;
  }

  function normalizeNodeType(type) {
    return Object.prototype.hasOwnProperty.call(NODE_TYPE_COLORS, type) ? type : "other";
  }

  function edgePairKey(source, target) {
    return [Math.min(Number(source), Number(target)), Math.max(Number(source), Number(target))].join(":");
  }

  function nodeColor(type, options = {}) {
    const baseColor = NODE_TYPE_COLORS[normalizeNodeType(type)] || NODE_TYPE_COLORS.other;
    if (options.isSelected) {
      return toRGBA(mixHex(baseColor, "#ffffff", 0.24), 0.98);
    }
    if (options.isHighlighted) {
      return toRGBA(mixHex(baseColor, "#ffffff", 0.12), 0.94);
    }
    if (options.isMuted) {
      return toRGBA("#334155", 0.34);
    }
    return toRGBA(baseColor, 0.88);
  }

  function relationColor(relationType) {
    const value = String(relationType || "related");
    const index = Math.abs(hashString(value)) % RELATION_PALETTE.length;
    return RELATION_PALETTE[index];
  }

  function linkColor(baseColor, options = {}) {
    if (options.isMemoryHighlighted) {
      return toRGBA(mixHex(baseColor, "#ffffff", 0.14), 0.88);
    }
    if (options.isActive) {
      return toRGBA(baseColor, 0.4);
    }
    return toRGBA("#334155", 0.14);
  }

  function mixHex(left, right, ratio) {
    const leftRgb = hexToRgb(left);
    const rightRgb = hexToRgb(right);
    const weight = clamp01(ratio);
    return rgbToHex({
      r: Math.round(leftRgb.r + (rightRgb.r - leftRgb.r) * weight),
      g: Math.round(leftRgb.g + (rightRgb.g - leftRgb.g) * weight),
      b: Math.round(leftRgb.b + (rightRgb.b - leftRgb.b) * weight),
    });
  }

  function toRGBA(hex, alpha) {
    const rgb = hexToRgb(hex);
    return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${clamp01(alpha)})`;
  }

  function hexToRgb(hex) {
    const normalized = String(hex || "#000000").replace("#", "").trim();
    const value = normalized.length === 3
      ? normalized.split("").map((part) => `${part}${part}`).join("")
      : normalized.padEnd(6, "0").slice(0, 6);
    return {
      r: Number.parseInt(value.slice(0, 2), 16),
      g: Number.parseInt(value.slice(2, 4), 16),
      b: Number.parseInt(value.slice(4, 6), 16),
    };
  }

  function rgbToHex(rgb) {
    return `#${[rgb.r, rgb.g, rgb.b]
      .map((channel) => clamp(Number(channel) || 0, 0, 255).toString(16).padStart(2, "0"))
      .join("")}`;
  }

  function hashString(value) {
    return [...String(value || "")].reduce(
      (accumulator, character) => accumulator * 31 + character.charCodeAt(0),
      7
    );
  }

  function setCanvasMessage(message, loading) {
    dom.canvasState.textContent = message || "";
    dom.canvasState.classList.toggle("is-visible", Boolean(message));
    dom.canvasState.classList.toggle("is-loading", Boolean(loading && message));
    dom.canvasShell?.classList.toggle("has-state", Boolean(message));
    dom.canvasShell?.classList.toggle("is-loading", Boolean(loading && message));
  }

  function typeLabel(type) {
    return NODE_TYPE_LABELS[type] || type || "节点";
  }

  function relationLabel(type) {
    return String(type || "related")
      .replace(/_/g, " ")
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function formatScore(value) {
    return `${(clamp01(Number(value || 0)) * 100).toFixed(0)}%`;
  }

  function formatImportance(value) {
    const number = Number(value || 0);
    return `${(number * 10).toFixed(1)} / 10`;
  }

  function truncate(text, maxLength) {
    const value = String(text || "");
    if (value.length <= maxLength) {
      return value;
    }
    return `${value.slice(0, Math.max(0, maxLength - 1))}…`;
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function clamp01(value) {
    return clamp(Number.isFinite(value) ? value : 0, 0, 1);
  }

  function emptyPanel(message) {
    return `<div class="graph-empty">${escapeHTML(message)}</div>`;
  }

  function escapeHTML(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
