(() => {
  "use strict";

  /* ================================================================
     State
     ================================================================ */
  const state = {
    page: "graph",
    memory: {
      items: [],
      total: 0,
      page: 1,
      pageSize: 20,
      hasMore: false,
      selected: new Set(),
      keyword: "",
      session: "",
      status: "all",
    },
    selectedMemory: null,
    isEditing: false,
    _detailCache: null,
    _nodeDetailCache: null,
    _recallCache: null,
    _systemCache: null,
    pendingSearch: null,
  };

  /* ================================================================
     Bridge Helpers
     ================================================================ */
  function buildEndpoint(path) {
    var cleanPath = String(path).replace(/^\/+/, "");
    return "page/" + cleanPath.replace(/\/+/g, "/");
  }

  async function apiRequest(path, options) {
    options = options || {};
    var method = options.method || "GET";
    var body = options.body;
    var retries = options.retries || 2;
    var bridge = window.AstrBotPluginPage;
    if (!bridge) throw new Error(window.t("bridge.error"));

    var lastError;
    for (var attempt = 0; attempt <= retries; attempt++) {
      try {
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
        return await bridge.apiPost(buildEndpoint(path), body || {});
      } catch (e) {
        lastError = e;
        if (attempt === retries) throw e;
        await new Promise(function(r) { setTimeout(r, Math.min(1000 * Math.pow(2, attempt), 5000)); });
      }
    }
    throw lastError || new Error(window.t("misc.requestFailed"));
  }

  function unwrapApiData(response) {
    if (response && response.status === "ok" && Object.prototype.hasOwnProperty.call(response, "data")) {
      return response.data || {};
    }
    if (response && response.status === "error") {
      throw new Error(response.message || window.t("misc.requestFailed"));
    }
    return response || {};
  }

  function normalizeImportance(value) {
    var n = Number(value);
    if (!Number.isFinite(n)) n = 0.5;
    if (n <= 1) n *= 10;
    return Math.min(10, Math.max(0, n));
  }

  function getDetailText(detail) {
    return detail.text || detail.content || detail.summary || "";
  }

  /* ================================================================
     Theme
     ================================================================ */
  function readTheme() {
    try {
      var bridge = window.AstrBotPluginPage;
      if (bridge) {
        var ctx = bridge.getContext();
        if (ctx && typeof ctx.isDark === "boolean") return ctx.isDark ? "dark" : "light";
      }
    } catch (_) {}
    try {
      var stored = localStorage.getItem("lmem_theme");
      if (stored) return stored;
    } catch (_) {}
    var html = document.documentElement.getAttribute("data-theme");
    if (html) return html;
    return "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var darkIcon = document.getElementById("theme-icon-dark");
    var lightIcon = document.getElementById("theme-icon-light");
    if (darkIcon && lightIcon) {
      darkIcon.classList.toggle("hidden", theme === "light");
      lightIcon.classList.toggle("hidden", theme === "dark");
    }
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute("data-theme") || "light";
    var next = current === "light" ? "dark" : "light";
    applyTheme(next);
    try { localStorage.setItem("lmem_theme", next); } catch (_) {}
    showToast(window.t(next === "dark" ? "theme.darkToast" : "theme.lightToast"));
  }

  function listenBridgeTheme() {
    try {
      var bridge = window.AstrBotPluginPage;
      if (!bridge || typeof bridge.onContext !== "function") return;
      bridge.onContext(function(ctx) {
        if (!ctx || typeof ctx.isDark !== "boolean") return;
        var t = ctx.isDark ? "dark" : "light";
        if (t !== (document.documentElement.getAttribute("data-theme") || "light")) {
          applyTheme(t);
        }
      });
    } catch (_) {}
  }

  /* ================================================================
     Toast
     ================================================================ */
  var toastTimer;
  function showToast(msg, isError) {
    var el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("visible", "error");
    if (isError) el.classList.add("error");
    void el.offsetWidth;
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function() { el.classList.remove("visible"); }, 2500);
  }

  /* ================================================================
     Sidebar / Routing
     ================================================================ */
  function switchPage(name) {
    state.page = name;
    document.querySelectorAll(".nav-item[data-page]").forEach(function(item) {
      item.classList.toggle("active", item.dataset.page === name);
    });
    document.querySelectorAll(".page").forEach(function(p) {
      p.classList.toggle("active", p.id === "page-" + name);
    });
    if (name === "graph") { fetchGraphStats(); if (window.ensureGraphScene) window.ensureGraphScene(); }
    if (name === "memory") { fetchMemories(); }
    if (name === "system") { fetchSystemOverview(); }
  }

  function initSidebar() {
    document.querySelectorAll(".nav-item[data-page]").forEach(function(item) {
      item.addEventListener("click", function() { switchPage(item.dataset.page); });
    });
    document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
    var langMenu = document.getElementById("lang-menu");
    document.querySelectorAll(".lang-option[data-lang]").forEach(function(option) {
      option.addEventListener("click", function() {
        var next = option.dataset.lang;
        window.setLanguage(next);
        updateLanguageMenu();
        if (langMenu) langMenu.open = false;
        showToast(window.t("language.toast", next.toUpperCase()));
      });
    });
    document.addEventListener("click", function(e) {
      if (langMenu && langMenu.open && !langMenu.contains(e.target)) langMenu.open = false;
    });
    window.addEventListener("languagechange", function() {
      updateLanguageMenu();
      refreshDynamicI18n();
    });
    updateLanguageMenu();
  }

  function updateLanguageMenu() {
    var current = window.getLanguage ? window.getLanguage() : "zh";
    var label = document.getElementById("lang-label");
    var currentLabelKeys = {
      zh: "language.current.zh",
      en: "language.current.en",
      ru: "language.current.ru",
    };
    if (label) {
      label.textContent = window.t("header.lang") + " · " + window.t(currentLabelKeys[current] || currentLabelKeys.zh);
    }
    document.querySelectorAll(".lang-option[data-lang]").forEach(function(option) {
      option.classList.toggle("active", option.dataset.lang === current);
    });
  }

  function refreshDynamicI18n() {
    if (state.page === "memory") {
      if (state.memory.items.length) renderMemoriesVirtual();
      else renderEmptyTable();
      updateMemoryPagination();
      updateBatchBar();
    }
    if (state._detailCache) {
      if (state.isEditing) renderMemoryEditView(state._detailCache);
      else renderMemoryDetailView(state._detailCache);
    }
    if (state._nodeDetailCache) renderPeekNode(state._nodeDetailCache);
    if (state._recallCache) renderRecallResults(state._recallCache);
    if (state._systemCache) renderSystemOverview(state._systemCache);
  }

  /* ================================================================
     Peek Panel — Memory Detail & Edit View
     ================================================================ */
  function openPeek(isWide) {
    var panel = document.getElementById("peek-panel");
    panel.classList.add("visible");
    if (isWide) panel.classList.add("wide");
    else panel.classList.remove("wide");
    document.getElementById("peek-overlay").classList.add("visible");
  }

  function closePeek() {
    var panel = document.getElementById("peek-panel");
    panel.classList.remove("visible", "wide");
    document.getElementById("peek-overlay").classList.remove("visible");
    state.selectedMemory = null;
    state.isEditing = false;
    state._detailCache = null;
    state._nodeDetailCache = null;
  }

  async function renderPeekMemory(memory) {
    state.selectedMemory = memory;
    state.isEditing = false;
    state._nodeDetailCache = null;
    var memoryId = memory.memory_id || memory.id;
    state._detailCache = null;

    /* Fetch full detail from API */
    var detail = null;
    try {
      detail = unwrapApiData(await apiRequest("memories/detail?memory_id=" + memoryId));
      if (detail) state._detailCache = detail;
    } catch (_) {
      detail = null;
    }

    if (!detail) {
      var rawMeta = (memory.raw && memory.raw.metadata) || {};
      detail = {
        memory_id: parseInt(memoryId),
        text: memory.summary || memory.content || "",
        summary: memory.summary || "",
        memory_type: memory.memory_type || rawMeta.memory_type || "GENERAL",
        importance: memory.importance != null ? Number(memory.importance) : 5,
        status: memory.status || rawMeta.status || "active",
        session_id: rawMeta.session_id || "--",
        persona_id: rawMeta.persona_id || "--",
        created_at: memory.created_at || "--",
        updated_at: memory.updated_at || "--",
        key_facts: Array.isArray(rawMeta.key_facts) ? rawMeta.key_facts : [],
        topics: Array.isArray(rawMeta.topics) ? rawMeta.topics : [],
        update_history: Array.isArray(rawMeta.update_history) ? rawMeta.update_history : [],
        graph_context: null,
      };
    }

    /* Ensure numeric types */
    if (detail.memory_id != null) detail.memory_id = parseInt(detail.memory_id);
    detail.importance = normalizeImportance(detail.importance);

    renderMemoryDetailView(detail);
    openPeek(true);
  }

  function renderMemoryDetailView(detail) {
    state._detailCache = detail;
    state._nodeDetailCache = null;
    state.isEditing = false;
    var id = detail.memory_id;
    var type = detail.memory_type || "GENERAL";
    var status = detail.status || "active";
    var importance = normalizeImportance(detail.importance).toFixed(1);
    var content = getDetailText(detail);
    var created = detail.created_at || "--";
    var updated = detail.updated_at || "--";
    var sessionId = detail.session_id || "--";
    var personaId = detail.persona_id || "--";
    var keyFacts = detail.key_facts || [];
    var topics = detail.topics || [];
    var editHistory = detail.update_history || [];
    var graphCtx = detail.graph_context;

    document.getElementById("peek-badge").innerHTML = "";
    document.getElementById("peek-title").textContent = window.t("detail.memoryTitle", id);

    var html = "";

    /* Status + Type pill row */
    html += '<div class="memory-detail-header">';
    html += statusPill(status);
    html += '<span class="type-tag">' + esc(type) + '</span>';
    html += '<span class="memory-detail-importance">' + window.t("detail.importance") + ': ' + importance + '/10</span>';
    html += '</div>';

    /* Actions bar */
    html += '<div class="memory-detail-actions">';
    html += '<button class="btn btn-sm btn-secondary" id="peek-edit-btn">' + window.t("detail.editBtn") + '</button>';
    html += '<button class="btn btn-sm btn-danger" id="peek-delete-btn">' + window.t("detail.deleteBtn") + '</button>';
    html += '</div>';

    /* Content section */
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.content") + '</div>';
    html += '<div class="memory-detail-content" id="detail-content-display">' + esc(content) + '</div></div>';

    /* Graph Context mini view */
    if (graphCtx && graphCtx.nodes && graphCtx.nodes.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.graphContext") + '</div>';
      html += '<canvas id="peek-mini-graph" class="memory-detail-mini-graph" width="440" height="160" data-memory-id="' + id + '"></canvas></div>';
    }

    /* Metadata grid */
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.metadata") + '</div>';
    html += '<div class="memory-detail-meta-grid">';
    html += metaItem(window.t("detail.status"), statusPill(status));
    html += metaItem(window.t("detail.type"), '<span class="type-tag">' + esc(type) + '</span>');
    html += metaItem(window.t("detail.importance"), importance + ' / 10');
    html += metaItem(window.t("detail.sessionId"), '<span style="font-size:11px;font-family:monospace">' + esc(String(sessionId)) + '</span>');
    html += metaItem(window.t("detail.personaId"), '<span style="font-size:11px;font-family:monospace">' + esc(String(personaId)) + '</span>');
    html += metaItem(window.t("detail.created"), esc(created));
    html += metaItem(window.t("detail.updated"), esc(updated));
    html += '</div></div>';

    /* Key Facts */
    if (keyFacts.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.keyFacts") + '</div><div class="peek-fact-list">';
      keyFacts.forEach(function(f) { html += '<div class="peek-fact-item">' + esc(String(f)) + '</div>'; });
      html += '</div></div>';
    }

    /* Topics */
    if (topics.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.topics") + '</div>';
      html += topics.map(function(t) { return '<span class="type-tag" style="margin-right:4px">' + esc(String(t)) + '</span>'; }).join("");
      html += '</div>';
    }

    /* Edit History */
    if (editHistory.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.editHistory") + '</div><div class="edit-history-list">';
      editHistory.forEach(function(h) {
        var time = h.timestamp ? new Date(h.timestamp * 1000).toLocaleString() : (h.time || "--");
        html += '<div class="edit-history-item"><span class="edit-history-time">' + esc(time) + '</span>';
        html += '<span class="edit-history-desc">' + esc(h.description || h.field + ": " + h.old_value + " → " + h.new_value) + '</span></div>';
      });
      html += '</div></div>';
    }

    document.getElementById("peek-body").innerHTML = html;

    /* Bind buttons */
    var editBtn = document.getElementById("peek-edit-btn");
    var delBtn = document.getElementById("peek-delete-btn");
    if (editBtn) editBtn.addEventListener("click", function() { renderMemoryEditView(detail); });
    if (delBtn) delBtn.addEventListener("click", function() { deleteSingleMemory(parseInt(id)); });

    /* Load mini-graph if canvas exists */
    var miniCanvas = document.getElementById("peek-mini-graph");
    if (miniCanvas && graphCtx && graphCtx.nodes && graphCtx.nodes.length) {
      loadPeekMiniGraphFromData(miniCanvas, graphCtx.nodes, graphCtx.edges);
    }
  }

  function renderMemoryEditView(detail) {
    state.isEditing = true;
    state._detailCache = detail;
    state._nodeDetailCache = null;
    var id = detail.memory_id;
    var content = getDetailText(detail);
    var importance = normalizeImportance(detail.importance).toFixed(1);
    var type = detail.memory_type || "GENERAL";
    var status = detail.status || "active";

    var html = "";

    html += '<div class="memory-detail-header">';
    html += '<span style="font-size:12px;color:var(--text-secondary)">' + window.t("detail.editingTitle", id) + '</span>';
    html += '</div>';

    html += '<div class="memory-detail-actions">';
    html += '<button class="btn btn-sm btn-primary" id="peek-save-btn">' + window.t("detail.saveBtn") + '</button>';
    html += '<button class="btn btn-sm btn-ghost" id="peek-cancel-btn">' + window.t("detail.cancelBtn") + '</button>';
    html += '</div>';

    /* Editable Content */
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.content") + '</div>';
    html += '<textarea id="edit-content-area" class="memory-detail-edit-area" rows="6">' + esc(content) + '</textarea>';
    html += '<p class="form-hint" style="margin-top:4px">' + window.t("detail.contentHint") + '</p>';
    html += '</div>';

    /* Editable Metadata */
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.metadata") + '</div>';
    html += '<div class="memory-detail-meta-grid">';

    html += '<div class="memory-detail-meta-item">';
    html += '<span class="memory-detail-meta-label">' + window.t("detail.status") + '</span>';
    html += '<select id="edit-status" class="memory-detail-select">';
    html += '<option value="active"' + (status === "active" ? " selected" : "") + '>' + statusLabel("active") + '</option>';
    html += '<option value="archived"' + (status === "archived" ? " selected" : "") + '>' + statusLabel("archived") + '</option>';
    html += '<option value="deleted"' + (status === "deleted" ? " selected" : "") + '>' + statusLabel("deleted") + '</option>';
    html += '</select></div>';

    html += '<div class="memory-detail-meta-item">';
    html += '<span class="memory-detail-meta-label">' + window.t("detail.type") + '</span>';
    html += '<input type="text" id="edit-type" class="memory-detail-select" value="' + esc(type) + '" />';
    html += '</div>';

    html += '<div class="memory-detail-meta-item" style="grid-column:1/-1">';
    html += '<span class="memory-detail-meta-label">' + window.t("detail.importance") + '</span>';
    html += '<div class="memory-detail-slider">';
    html += '<input type="range" id="edit-importance" min="0" max="10" step="0.1" value="' + importance + '" />';
    html += '<span class="memory-detail-slider-value" id="importance-value">' + importance + '</span>';
    html += '</div></div>';

    html += '<div class="memory-detail-meta-item" style="grid-column:1/-1">';
    html += '<span class="memory-detail-meta-label">' + window.t("detail.updateReason") + '</span>';
    html += '<input type="text" id="peek-edit-reason" class="memory-detail-reason" placeholder="' + esc(window.t("detail.reasonPh")) + '" />';
    html += '</div>';

    html += '</div></div>';

    document.getElementById("peek-body").innerHTML = html;

    /* Bind slider */
    document.getElementById("edit-importance").addEventListener("input", function() {
      document.getElementById("importance-value").textContent = parseFloat(this.value).toFixed(1);
    });

    var saveBtn = document.getElementById("peek-save-btn");
    var cancelBtn = document.getElementById("peek-cancel-btn");
    if (saveBtn) saveBtn.addEventListener("click", function() { saveMemoryEdit(detail); });
    if (cancelBtn) cancelBtn.addEventListener("click", function() { renderMemoryDetailView(detail); });
  }

  async function saveMemoryEdit(detail) {
    var id = detail.memory_id;
    var newContent = document.getElementById("edit-content-area").value.trim();
    var newStatus = document.getElementById("edit-status").value;
    var newType = document.getElementById("edit-type").value.trim();
    var newImportance = parseFloat(document.getElementById("edit-importance").value);
    var reason = document.getElementById("peek-edit-reason").value.trim();

    var saveBtn = document.getElementById("peek-save-btn");
    if (saveBtn) saveBtn.disabled = true;
    var messages = [];

    try {
      if (!newContent) {
        showToast(window.t("detail.contentRequired"), true);
        return;
      }

      if (newContent !== getDetailText(detail)) {
        var result = unwrapApiData(await apiRequest("memories/update", {
          method: "POST",
          body: { memory_id: id, field: "content", value: newContent, reason: reason },
        }));
        if (result && result.new_memory_id) {
          messages.push(window.t("detail.contentUpdated", result.new_memory_id));
          id = parseInt(result.new_memory_id);
        }
      }

      if (newStatus !== detail.status) {
        await apiRequest("memories/update", {
          method: "POST", body: { memory_id: id, field: "status", value: newStatus, reason: reason },
        });
        messages.push(window.t("detail.statusUpdated", statusLabel(newStatus)));
      }

      if (newType !== detail.memory_type) {
        await apiRequest("memories/update", {
          method: "POST", body: { memory_id: id, field: "type", value: newType, reason: reason },
        });
        messages.push(window.t("detail.typeUpdated", newType));
      }

      if (Math.abs(newImportance - normalizeImportance(detail.importance)) > 0.01) {
        await apiRequest("memories/update", {
          method: "POST", body: { memory_id: id, field: "importance", value: newImportance, reason: reason },
        });
        messages.push(window.t("detail.importanceUpdated", newImportance.toFixed(1)));
      }

      showToast(messages.length ? messages.join("; ") : window.t("detail.noChanges"));
      closePeek();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || window.t("edit.updateFailed"), true);
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  function renderPeekNode(nodeData) {
    state._nodeDetailCache = nodeData;
    state._detailCache = null;
    state.isEditing = false;
    var panel = document.getElementById("peek-panel");
    panel.classList.remove("wide");
    document.getElementById("peek-badge").innerHTML = nodeBadge(nodeData.type);
    document.getElementById("peek-title").textContent = nodeData.label || window.t("graph.unnamedNode");

    var html = '<div class="peek-section">';
    html += '<div class="peek-meta-grid">';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeMemories") + '</span><span class="peek-meta-value">' + (nodeData.memory_count || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeDegree") + '</span><span class="peek-meta-value">' + (nodeData.degree || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeEntries") + '</span><span class="peek-meta-value">' + (nodeData.entry_count || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeWeight") + '</span><span class="peek-meta-value">' + Number(nodeData.weight || 0).toFixed(2) + '</span></div>';
    html += '</div></div>';

    document.getElementById("peek-body").innerHTML = html;
    openPeek(false);
  }

  function nodeBadge(type) {
    var t = String(type || "other").toLowerCase();
    return '<div class="peek-node-badge ' + t + '">' + typeLabel(t) + '</div>';
  }

  function statusPill(status) {
    var s = String(status || "active").toLowerCase();
    return '<span class="status-pill ' + s + '">' + statusLabel(s) + '</span>';
  }

  function statusLabel(status) {
    var s = String(status || "active").toLowerCase();
    var labels = { active: "status.active", archived: "status.archived", deleted: "status.deleted" };
    return labels[s] ? window.t(labels[s]) : s;
  }

  function typeLabel(type) {
    var t = String(type || "other").toLowerCase();
    var keys = { topic: "graph.nodeTopic", person: "graph.nodePerson", fact: "graph.nodeFact", summary: "graph.nodeSummary" };
    return window.t(keys[t] || "graph.nodeUnknown");
  }

  function metaItem(label, value) {
    return '<div class="memory-detail-meta-item"><span class="memory-detail-meta-label">' + esc(label) + '</span><span class="memory-detail-meta-value">' + value + '</span></div>';
  }

  function esc(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* Mini Graph Canvas */
  var MINI_NODE_COLORS = {
    topic: "#7950f2", person: "#20c997", fact: "#fcc419", summary: "#f06595", other: "#909296",
  };

  function loadPeekMiniGraphFromData(canvas, nodes, edges) {
    var ctx = canvas.getContext("2d");
    var rect = canvas.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var W = Math.max(220, Math.floor(rect.width || canvas.width || 440));
    var H = Math.max(140, Math.floor(rect.height || canvas.height || 160));
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!nodes.length) {
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--text-tertiary").trim() || "#8a8f98";
      ctx.font = "11px -apple-system, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(window.t("detail.noGraphData"), W / 2, H / 2);
      return;
    }
    drawMiniGraph(ctx, W, H, nodes, edges);

    canvas.style.cursor = "pointer";
    canvas.onclick = function() {
      document.querySelector('.nav-item[data-page="graph"]').click();
      var mid = canvas.dataset.memoryId;
      if (mid) {
        setTimeout(function() {
          var mi = document.getElementById("graph-memory-id");
          if (mi) { mi.value = mid; mi.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" })); }
        }, 300);
      }
    };
  }

  function drawMiniGraph(ctx, W, H, nodes, edges) {
    ctx.clearRect(0, 0, W, H);
    var pad = 20;
    var cx = W / 2, cy = H / 2;
    var radius = Math.min(W, H) / 2 - pad;

    var n = nodes.length;
    var nodePositions = [];
    nodes.forEach(function(node, i) {
      var angle = (2 * Math.PI * i) / n - Math.PI / 2;
      var x = cx + Math.cos(angle) * radius * 0.75;
      var y = cy + Math.sin(angle) * radius * 0.65;
      nodePositions.push({ id: node.id, x: x, y: y, type: node.type });
    });

    var nodeLookup = {};
    nodePositions.forEach(function(np) { nodeLookup[np.id] = np; });
    ctx.strokeStyle = "rgba(148,163,184,0.3)";
    ctx.lineWidth = 0.8;
    edges.forEach(function(edge) {
      var src = nodeLookup[edge.source];
      var tgt = nodeLookup[edge.target];
      if (!src || !tgt) return;
      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.stroke();
    });

    nodePositions.forEach(function(np) {
      var color = MINI_NODE_COLORS[np.type] || MINI_NODE_COLORS.other;
      ctx.beginPath();
      ctx.arc(np.x, np.y, 4, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.5)";
      ctx.lineWidth = 1;
      ctx.stroke();
    });
  }

  /* ================================================================
     Memory Page — Virtual Scrolling
     ================================================================ */
  var ROW_HEIGHT = 56;
  var SCROLL_BUFFER = 15;

  async function fetchMemories() {
    var params = new URLSearchParams();
    params.set("page", String(state.memory.page));
    params.set("page_size", String(state.memory.pageSize));
    if (state.memory.session) params.set("session_id", state.memory.session);
    if (state.memory.keyword) params.set("keyword", state.memory.keyword);
    if (state.memory.status && state.memory.status !== "all") params.set("status", state.memory.status);

    try {
      var data = unwrapApiData(await apiRequest("memories?" + params.toString())) || {};
      state.memory.total = data.total || 0;
      state.memory.hasMore = data.has_more || false;
      state.memory.selected.clear();

      state.memory.items = (Array.isArray(data.items) ? data.items : []).map(function(item) {
        return {
          memory_id: item.id,
          doc_id: item.doc_id,
          summary: item.text || item.content || "",
          content: item.text || item.content,
          memory_type: (item.metadata && item.metadata.memory_type) || "GENERAL",
          importance: normalizeImportance(item.metadata && item.metadata.importance),
          status: (item.metadata && item.metadata.status) || "active",
          created_at: (item.metadata && item.metadata.create_time)
            ? new Date(item.metadata.create_time * 1000).toLocaleString()
            : "--",
          updated_at: (item.metadata && item.metadata.updated_at)
            ? new Date(item.metadata.updated_at * 1000).toLocaleString()
            : item.updated_at || "--",
          last_access: (item.metadata && item.metadata.last_access_time)
            ? new Date(item.metadata.last_access_time * 1000).toLocaleString()
            : "--",
          raw: item,
        };
      });

      renderMemoriesVirtual({ resetScroll: true });
      updateMemoryPagination();
      updateBatchBar();
      syncSelectAllCheckbox();
    } catch (e) {
      showToast(e.message || window.t("misc.fetchMemoriesFail"), true);
      renderEmptyTable();
    }
  }

  function renderMemoriesVirtual(options) {
    options = options || {};
    var tbody = document.getElementById("memories-body");
    var scrollEl = document.getElementById("memories-scroll");
    if (!state.memory.items.length) { renderEmptyTable(); return; }

    var totalHeight = state.memory.items.length * ROW_HEIGHT;
    if (scrollEl && options.resetScroll) scrollEl.scrollTop = 0;

    function renderSlice() {
      var scrollTop = scrollEl ? scrollEl.scrollTop : 0;
      var viewHeight = scrollEl ? scrollEl.clientHeight : 600;
      var start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - SCROLL_BUFFER);
      var end = Math.min(state.memory.items.length, Math.ceil((scrollTop + viewHeight) / ROW_HEIGHT) + SCROLL_BUFFER);
      var padTop = start * ROW_HEIGHT;
      var padBottom = totalHeight - end * ROW_HEIGHT;

      var html = "";
      for (var i = start; i < end; i++) {
        var item = state.memory.items[i];
        var key = "m:" + item.memory_id;
        var sel = state.memory.selected.has(key) ? " selected" : "";
        var imp = item.importance != null ? Number(item.importance).toFixed(1) : "5.0";
        var impNum = Math.min(10, Math.max(0, parseFloat(imp) || 0));
        var impCls = impNum >= 7 ? "high" : impNum >= 4 ? "medium" : "low";
        html += '<tr data-key="' + key + '" class="' + sel + '" style="height:' + ROW_HEIGHT + 'px">' +
          '<td class="cell-check"><input type="checkbox" class="memory-row-check" data-key="' + key + '"' + (sel ? " checked" : "") + ' aria-label="' + esc(window.t("table.selectMemory", item.memory_id)) + '" /></td>' +
          '<td class="cell-mono cell-id">' + item.memory_id + '</td>' +
          '<td class="cell-summary"><div class="memory-summary-text">' + esc(item.summary || "") + '</div><div class="memory-summary-meta">' + esc(window.t("table.updated", item.updated_at || "--")) + '</div></td>' +
          '<td class="cell-type"><span class="type-tag">' + esc(item.memory_type || "GENERAL") + '</span></td>' +
          '<td class="cell-importance"><div class="importance-bar"><div class="importance-bar-track">' +
          '<div class="importance-bar-fill ' + impCls + '" style="width:' + (impNum * 10) + '%"></div></div>' +
          '<span style="font-size:12px;color:var(--text-secondary)">' + imp + '</span></div></td>' +
          '<td class="cell-status">' + statusPill(item.status) + '</td>' +
          '<td class="cell-created text-secondary" style="font-size:12px">' + esc(item.created_at) + '</td>' +
          '</tr>';
      }

      tbody.innerHTML = html;
      tbody.style.paddingTop = padTop + "px";
      tbody.style.paddingBottom = padBottom + "px";
    }

    if (scrollEl && !scrollEl._virtualScrollBound) {
      scrollEl._virtualScrollBound = true;
      scrollEl.addEventListener("scroll", function() {
        window.requestAnimationFrame(renderSlice);
      }, { passive: true });
    }

    renderSlice();
  }

  function renderEmptyTable() {
    var tbody = document.getElementById("memories-body");
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty">' + window.t("table.noData") + '</td></tr>';
    tbody.style.paddingTop = "0";
    tbody.style.paddingBottom = "0";
  }

  function getMemoryItemByKey(key) {
    return state.memory.items.find(function(i) { return ("m:" + i.memory_id) === key; });
  }

  function onMemoryCheckboxClick(row, event) {
    event.stopPropagation();
    var key = row.dataset.key;
    if (event.shiftKey && state._lastClickedKey) {
      var keys = state.memory.items.map(function(i) { return "m:" + i.memory_id; });
      var a = keys.indexOf(state._lastClickedKey);
      var b = keys.indexOf(key);
      if (a !== -1 && b !== -1) {
        var lo = Math.min(a, b);
        var hi = Math.max(a, b);
        for (var i = lo; i <= hi; i++) state.memory.selected.add(keys[i]);
      }
    } else {
      if (state.memory.selected.has(key)) {
        state.memory.selected.delete(key);
      } else {
        state.memory.selected.add(key);
      }
    }
    state._lastClickedKey = key;
    renderMemoriesVirtual();
    updateBatchBar();
    syncSelectAllCheckbox();
  }

  function updateBatchBar() {
    var bar = document.getElementById("batch-bar");
    var count = state.memory.selected.size;
    document.getElementById("batch-count").textContent = window.t("filter.selectedCount", count);
    bar.classList.toggle("visible", count > 0);
    syncSelectAllCheckbox();
  }

  function updateMemoryPagination() {
    var p = state.memory.page;
    var ps = state.memory.pageSize;
    var t = state.memory.total;
    var tp = Math.max(1, Math.ceil(t / ps));
    document.getElementById("mem-pagination-info").textContent = window.t("common.page", p, tp, t);
    document.getElementById("mem-prev").disabled = p <= 1;
    document.getElementById("mem-next").disabled = !state.memory.hasMore;
  }

  async function deleteSingleMemory(id) {
    if (!id) return;
    if (!window.confirm(window.t("delete.confirmMsg", 1))) {
      showToast(window.t("delete.cancelled"));
      return;
    }
    try {
      await apiRequest("memories/batch-delete", { method: "POST", body: { memory_ids: [id] } });
      showToast(window.t("delete.successOne", id));
      closePeek();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || window.t("delete.error"), true);
    }
  }

  async function batchDelete() {
    if (!state.memory.selected.size) return;
    var ids = [];
    state.memory.selected.forEach(function(k) {
      var id = parseInt(k.replace("m:", ""));
      if (!isNaN(id)) ids.push(id);
    });
    if (!ids.length) return;
    if (!window.confirm(window.t("delete.confirmMsg", ids.length))) {
      showToast(window.t("delete.cancelled"));
      return;
    }
    try {
      await apiRequest("memories/batch-delete", { method: "POST", body: { memory_ids: ids } });
      showToast(window.t("delete.success", ids.length));
      state.memory.selected.clear();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || window.t("delete.error"), true);
    }
  }

  async function batchArchive() {
    if (!state.memory.selected.size) return;
    var ids = [];
    state.memory.selected.forEach(function(k) {
      var id = parseInt(k.replace("m:", ""));
      if (!isNaN(id)) ids.push(id);
    });
    if (!ids.length) return;
    try {
      var result = unwrapApiData(await apiRequest("memories/batch-update", {
        method: "POST",
        body: { memory_ids: ids, field: "status", value: "archived" },
      }));
      var updated = (result && result.updated_count) || ids.length;
      showToast(window.t("archive.success", updated));
      state.memory.selected.clear();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || window.t("archive.fail"), true);
    }
  }

  function syncSelectAllCheckbox() {
    var checkbox = document.getElementById("mem-select-page");
    if (!checkbox) return;
    var total = state.memory.items.length;
    var selected = state.memory.items.filter(function(item) {
      return state.memory.selected.has("m:" + item.memory_id);
    }).length;
    checkbox.checked = total > 0 && selected === total;
    checkbox.indeterminate = selected > 0 && selected < total;
  }

  function initMemoryPage() {
    var tbody = document.getElementById("memories-body");
    if (tbody) {
      tbody.addEventListener("click", function(e) {
        var tr = e.target.closest("tr");
        if (!tr || !tr.dataset.key) return;
        var checkbox = e.target.closest(".memory-row-check");
        if (checkbox) {
          onMemoryCheckboxClick(tr, e);
          return;
        }
        var item = getMemoryItemByKey(tr.dataset.key);
        if (item) renderPeekMemory(item);
      });
    }

    var selectPage = document.getElementById("mem-select-page");
    if (selectPage) {
      selectPage.addEventListener("change", function() {
        state.memory.items.forEach(function(item) {
          var key = "m:" + item.memory_id;
          if (selectPage.checked) state.memory.selected.add(key);
          else state.memory.selected.delete(key);
        });
        renderMemoriesVirtual();
        updateBatchBar();
      });
    }

    document.getElementById("mem-keyword").addEventListener("input", debounce(function() {
      state.memory.keyword = this.value.trim();
      state.memory.page = 1;
      fetchMemories();
    }, 300));

    document.getElementById("mem-session").addEventListener("input", debounce(function() {
      state.memory.session = this.value.trim();
      state.memory.page = 1;
      fetchMemories();
    }, 300));

    document.getElementById("mem-status").addEventListener("change", function() {
      state.memory.status = this.value;
      state.memory.page = 1;
      fetchMemories();
    });

    document.getElementById("mem-page-size").addEventListener("change", function() {
      state.memory.pageSize = parseInt(this.value) || 20;
      state.memory.page = 1;
      fetchMemories();
    });

    document.getElementById("mem-prev").addEventListener("click", function() {
      if (state.memory.page > 1) { state.memory.page--; fetchMemories(); }
    });
    document.getElementById("mem-next").addEventListener("click", function() {
      if (state.memory.hasMore) { state.memory.page++; fetchMemories(); }
    });
    document.getElementById("batch-delete").addEventListener("click", batchDelete);
    document.getElementById("batch-archive").addEventListener("click", batchArchive);
    document.getElementById("batch-clear").addEventListener("click", function() {
      state.memory.selected.clear();
      renderMemoriesVirtual();
      updateBatchBar();
    });
  }

  function debounce(fn, ms) {
    var timer;
    return function() {
      var self = this, args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function() { fn.apply(self, args); }, ms || 300);
    };
  }

  /* ================================================================
     Recall Page
     ================================================================ */
  async function runRecall() {
    var query = document.getElementById("recall-query").value.trim();
    if (!query) return showToast(window.t("recall.enterQuery"), true);
    var k = parseInt(document.getElementById("recall-k").value) || 5;
    var session = document.getElementById("recall-session").value.trim() || null;
    var btn = document.getElementById("recall-search-btn");
    btn.disabled = true;

    try {
      var data = unwrapApiData(await apiRequest("recall/test", {
        method: "POST",
        body: { query: query, k: k, session_id: session },
      }));
      renderRecallResults(data);
    } catch (e) {
      showToast(e.message || window.t("recall.fail"), true);
    } finally {
      btn.disabled = false;
    }
  }

  function renderRecallResults(data) {
    state._recallCache = data;
    var stats = document.getElementById("recall-stats");
    var container = document.getElementById("recall-results");
    var total = data.total || 0;
    var time = data.elapsed_time_ms != null ? data.elapsed_time_ms.toFixed(1) + "ms" : "";

    if (total) {
      stats.classList.remove("hidden");
      document.getElementById("recall-count-text").textContent = window.t("recall.resultsCount", total);
      document.getElementById("recall-time-text").textContent = time;
    } else {
      stats.classList.add("hidden");
    }

    if (!total) {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary)">' + window.t("recall.noMatch") + '</div>';
      return;
    }

    container.innerHTML = (data.results || []).map(function(r, i) {
      var pct = r.score_percentage || 0;
      var badgeCls = pct >= 70 ? "high" : pct >= 45 ? "medium" : "low";
      var scoreMeta = Object.assign({}, r.metadata || {}, r.score_breakdown || {});
      var scores = [
        { label: "Doc-KW", val: scoreMeta.document_keyword_score, cls: "doc-kw" },
        { label: "Doc-Vec", val: scoreMeta.document_vector_score, cls: "doc-vec" },
        { label: "Graph-KW", val: scoreMeta.graph_keyword_score, cls: "graph-kw" },
        { label: "Graph-Vec", val: scoreMeta.graph_vector_score, cls: "graph-vec" },
      ];
      return '<div class="result-card" data-memory-id="' + r.memory_id + '">' +
        '<div class="result-card-header">' +
          '<span class="result-rank">#' + (i + 1) + '</span>' +
          '<span class="result-score-badge ' + badgeCls + '">' + pct.toFixed(1) + '%</span>' +
        '</div>' +
        '<div class="result-content">' + esc(r.content || "") + '</div>' +
        '<div class="result-scores">' + scores.map(function(s) {
          var v = s.val != null ? Math.min(1, Math.max(0, parseFloat(s.val) || 0)) : 0;
          var w = (v * 100).toFixed(0);
          return '<div class="result-score-row">' +
            '<span class="result-score-row-label">' + s.label + '</span>' +
            '<div class="result-score-row-track"><div class="result-score-row-fill ' + s.cls + '" style="width:' + w + '%"></div></div>' +
            '<span class="result-score-row-value">' + v.toFixed(2) + '</span>' +
          '</div>';
        }).join("") + '</div>' +
      '</div>';
    });

    container.querySelectorAll(".result-card").forEach(function(card) {
      card.addEventListener("click", function() {
        var mid = parseInt(card.dataset.memoryId);
        var item = state.memory.items.find(function(i) { return i.memory_id === mid; });
        if (item) renderPeekMemory(item);
      });
    });
  }

  function initRecallPage() {
    document.getElementById("recall-search-btn").addEventListener("click", runRecall);
    document.getElementById("recall-query").addEventListener("keydown", function(e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runRecall();
    });
    document.getElementById("recall-k").addEventListener("input", function() {
      document.getElementById("recall-k-value").textContent = this.value;
    });
  }

  /* ================================================================
     System Page
     ================================================================ */
  async function fetchSystemOverview() {
    try {
      var data = unwrapApiData(await apiRequest("stats")) || {};
      renderSystemOverview(data);

      /* Backups */
      try {
        var backups = unwrapApiData(await apiRequest("backups")) || {};
        data.backups = backups.backups || [];
        renderSystemOverview(data);
      } catch (_) {
        data.backupsUnavailable = true;
        renderSystemOverview(data);
      }
    } catch (e) {
      showToast(e.message || window.t("misc.systemFail"), true);
    }
  }

  function renderSystemOverview(data) {
    state._systemCache = data;
    document.getElementById("ss-total").textContent = data.total_memories || data.total_count || "0";
    var sb = data.status_breakdown || {};
    document.getElementById("ss-active").textContent = sb.active || 0;
    document.getElementById("ss-archived").textContent = sb.archived || 0;
    document.getElementById("ss-deleted").textContent = sb.deleted || 0;
    document.getElementById("ss-nodes").textContent = data.graph_nodes || 0;
    document.getElementById("ss-atoms").textContent = data.atom_count || 0;

    var dist = data.importance_distribution || {};
    var bins = ["0-1","1-2","2-3","3-4","4-5","5-6","6-7","7-8","8-9","9-10"];
    var maxV = 1;
    bins.forEach(function(b) { maxV = Math.max(maxV, dist[b] || 0); });
    document.getElementById("importance-chart").innerHTML = bins.map(function(b) {
      var v = dist[b] || 0;
      var w = maxV ? (v / maxV * 100).toFixed(0) : 0;
      return '<div class="bar-row"><span class="bar-row-label">' + b + '</span>' +
        '<div class="bar-row-track"><div class="bar-row-fill" style="width:' + w + '%"></div></div>' +
        '<span class="bar-row-value">' + v + '</span></div>';
    }).join("");

    var atoms = data.atom_breakdown || {};
    var atomTypes = ["factual","episodic","preference","relational","planned"];
    var maxA = 1;
    atomTypes.forEach(function(t) { maxA = Math.max(maxA, atoms[t] || 0); });
    document.getElementById("atom-chart").innerHTML = atomTypes.map(function(t) {
      var v = atoms[t] || 0;
      var w = maxA ? (v / maxA * 100).toFixed(0) : 0;
      return '<div class="bar-row"><span class="bar-row-label" style="width:80px">' + atomLabel(t) + '</span>' +
        '<div class="bar-row-track"><div class="bar-row-fill" style="width:' + w + '%"></div></div>' +
        '<span class="bar-row-value">' + v + '</span></div>';
    }).join("");

    var sessions = data.recent_sessions || [];
    if (!sessions.length && data.sessions) {
      sessions = Object.keys(data.sessions).map(function(k) {
        return { session_id: k, message_count: data.sessions[k] };
      }).sort(function(a, b) { return b.message_count - a.message_count; }).slice(0, 10);
    }
    document.getElementById("session-list").innerHTML = sessions.length
      ? sessions.map(function(s) {
        return '<div class="session-item"><span class="session-id">' + esc(s.session_id || s) + '</span>' +
          '<span class="session-meta">' + (s.message_count || "") + (s.last_active ? ' · ' + esc(s.last_active) : '') + '</span></div>';
      }).join("")
      : '<div style="color:var(--text-tertiary);text-align:center;padding:20px">' + window.t("system.noActiveSessions") + '</div>';

    if (data.backupsUnavailable) {
      document.getElementById("backup-list").innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:20px">' + window.t("common.unavailable") + '</div>';
      return;
    }

    var list = data.backups || [];
    document.getElementById("backup-list").innerHTML = list.length
      ? list.map(function(b) {
        return '<div class="backup-item"><span class="backup-version">' + esc(b.name || b.directory || "") + '</span>' +
          '<span class="backup-date">' + esc(b.backup_timestamp || "") + '</span>' +
          '<span class="backup-files">' + esc(window.t("system.files", b.file_count || b.files_copied || 0)) + '</span></div>';
      }).join("")
      : '<div style="color:var(--text-tertiary);text-align:center;padding:20px">' + window.t("system.noBackups") + '</div>';
  }

  function atomLabel(type) {
    var keys = {
      factual: "system.atomFactual",
      episodic: "system.atomEpisodic",
      preference: "system.atomPreference",
      relational: "system.atomRelational",
      planned: "system.atomPlanned",
    };
    return window.t(keys[type] || type);
  }

  /* ================================================================
     Graph Page
     ================================================================ */
  async function fetchGraphStats() {
    try {
      var data = unwrapApiData(await apiRequest("stats")) || {};
      document.getElementById("gs-total").textContent = data.total_memories || data.total_count || "0";
      document.getElementById("gs-nodes").textContent = data.graph_nodes || "0";
      document.getElementById("gs-edges").textContent = data.graph_edges || "0";
      var sessions = data.sessions || {};
      document.getElementById("gs-sessions").textContent = Object.keys(sessions).length || "0";
    } catch (_) {}
  }

  /* ================================================================
     Init
     ================================================================ */
  async function init() {
    applyTheme(readTheme());
    listenBridgeTheme();

    var bridge = window.AstrBotPluginPage;
    if (bridge && typeof bridge.ready === "function") {
      try { await bridge.ready(); } catch (_) {}
    }

    initSidebar();
    initMemoryPage();
    initRecallPage();

    document.getElementById("peek-close").addEventListener("click", closePeek);
    document.getElementById("peek-overlay").addEventListener("click", closePeek);
    document.addEventListener("keydown", function(e) {
      if (e.key === "Escape") {
        closePeek();
        state.memory.selected.clear();
        renderMemoriesVirtual();
        updateBatchBar();
      }
    });

    /* Initial data */
    fetchGraphStats();
    switchPage("graph");
  }

  /* Expose to graph-ui */
  window.lmState = state;
  window.lmShowToast = showToast;
  window.lmApiRequest = apiRequest;
  window.lmOpenPeekNode = renderPeekNode;
  window.lmOpenPeekMemory = renderPeekMemory;
  window.lmClosePeek = closePeek;
  window.lmFetchGraphStats = fetchGraphStats;
  window.lmEsc = esc;
  window.lmStatusPill = statusPill;
  window.lmNodeBadge = nodeBadge;

  document.addEventListener("DOMContentLoaded", init);
})();
