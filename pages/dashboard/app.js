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
    showToast(next === "dark" ? "Dark mode" : "Light mode");
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
    document.getElementById("lang-toggle").addEventListener("click", function() {
      var langs = ["zh", "en", "ru"];
      var cur = window.getLanguage();
      var idx = langs.indexOf(cur);
      var next = langs[(idx + 1) % langs.length];
      window.setLanguage(next);
      document.getElementById("lang-label").textContent = next.toUpperCase();
      showToast("Language: " + next.toUpperCase());
    });
  }

  /* ================================================================
     Peek Panel
     ================================================================ */
  function openPeek() {
    document.getElementById("peek-panel").classList.add("visible");
    document.getElementById("peek-overlay").classList.add("visible");
  }

  function closePeek() {
    document.getElementById("peek-panel").classList.remove("visible");
    document.getElementById("peek-overlay").classList.remove("visible");
    state.selectedMemory = null;
  }

  function renderPeekMemory(memory) {
    state.selectedMemory = memory;
    document.getElementById("peek-badge").innerHTML = "";
    document.getElementById("peek-title").textContent = "#" + (memory.memory_id || memory.id || "-");
    var type = (memory.memory_type || "").toLowerCase();
    var status = memory.status || "active";
    var importance = memory.importance != null ? Number(memory.importance).toFixed(1) : "--";
    var content = memory.summary || memory.content || memory.text || "";
    var created = memory.created_at || "--";
    var lastAccess = memory.last_access || "--";
    var sessionId = (memory.raw && memory.raw.metadata && memory.raw.metadata.session_id) || "--";
    var keyFacts = [];
    var topics = [];
    if (memory.raw && memory.raw.metadata) {
      var m = memory.raw.metadata;
      if (Array.isArray(m.key_facts)) keyFacts = m.key_facts;
      if (Array.isArray(m.topics)) topics = m.topics;
    }

    var html = "";
    if (type) { html += '<div class="peek-section"><span class="type-tag">' + esc(type) + '</span></div>'; }
    html += '<div class="peek-section"><p style="font-size:14px;line-height:1.6">' + esc(content) + '</p></div>';
    html += '<div class="peek-section"><div class="peek-section-title">Details</div>';
    html += '<div class="peek-meta-grid">';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Importance</span><span class="peek-meta-value">' + importance + ' / 10</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Status</span><span class="peek-meta-value">' + statusPill(status) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Session</span><span class="peek-meta-value text-mono" style="font-size:11px">' + esc(String(sessionId)) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Created</span><span class="peek-meta-value">' + esc(created) + '</span></div>';
    html += '</div></div>';

    if (keyFacts.length) {
      html += '<div class="peek-section"><div class="peek-section-title">Key Facts</div><div class="peek-fact-list">';
      keyFacts.forEach(function(f) { html += '<div class="peek-fact-item">' + esc(String(f)) + '</div>'; });
      html += '</div></div>';
    }

    if (topics.length) {
      html += '<div class="peek-section"><div class="peek-section-title">Topics</div>';
      html += topics.map(function(t) { return '<span class="type-tag" style="margin-right:4px">' + esc(String(t)) + '</span>'; }).join("");
      html += '</div>';
    }

    html += '<div class="peek-section" style="display:flex;gap:var(--space-2)">';
    html += '<button class="btn btn-sm btn-secondary" id="peek-edit-btn">Edit</button>';
    html += '<button class="btn btn-sm btn-danger" id="peek-delete-btn">Delete</button>';
    html += '</div>';

    document.getElementById("peek-body").innerHTML = html;

    var editBtn = document.getElementById("peek-edit-btn");
    var delBtn = document.getElementById("peek-delete-btn");
    if (editBtn) editBtn.addEventListener("click", openEditModal);
    if (delBtn) delBtn.addEventListener("click", function() {
      if (state.selectedMemory) {
        deleteSingleMemory(parseInt(state.selectedMemory.memory_id || state.selectedMemory.id));
      }
    });
    openPeek();
  }

  function renderPeekNode(nodeData) {
    document.getElementById("peek-badge").innerHTML = nodeBadge(nodeData.type);
    document.getElementById("peek-title").textContent = nodeData.label || "Unnamed Node";

    var html = '<div class="peek-section">';
    html += '<div class="peek-meta-grid">';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Memories</span><span class="peek-meta-value">' + (nodeData.memory_count || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Degree</span><span class="peek-meta-value">' + (nodeData.degree || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Entries</span><span class="peek-meta-value">' + (nodeData.entry_count || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">Weight</span><span class="peek-meta-value">' + Number(nodeData.weight || 0).toFixed(2) + '</span></div>';
    html += '</div></div>';

    document.getElementById("peek-body").innerHTML = html;
    openPeek();
  }

  function nodeBadge(type) {
    var t = String(type || "other").toLowerCase();
    var labels = { topic: "Topic", person: "Person", fact: "Fact", summary: "Summary" };
    var label = labels[t] || t;
    return '<div class="peek-node-badge ' + t + '">' + label + '</div>';
  }

  function statusPill(status) {
    var s = String(status || "active").toLowerCase();
    return '<span class="status-pill ' + s + '">' + s.charAt(0).toUpperCase() + s.slice(1) + '</span>';
  }

  function esc(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ================================================================
     Memory Page
     ================================================================ */
  async function fetchMemories() {
    var params = new URLSearchParams();
    params.set("page", String(state.memory.page));
    params.set("page_size", String(state.memory.pageSize));
    if (state.memory.session) params.set("session_id", state.memory.session);
    if (state.memory.keyword) params.set("keyword", state.memory.keyword);
    if (state.memory.status && state.memory.status !== "all") params.set("status", state.memory.status);

    try {
      var data = await apiRequest("memories?" + params.toString()) || {};
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
          importance: (item.metadata && item.metadata.importance) || 5,
          status: (item.metadata && item.metadata.status) || "active",
          created_at: (item.metadata && item.metadata.create_time)
            ? new Date(item.metadata.create_time * 1000).toLocaleString()
            : "--",
          last_access: (item.metadata && item.metadata.last_access_time)
            ? new Date(item.metadata.last_access_time * 1000).toLocaleString()
            : "--",
          raw: item,
        };
      });

      renderMemoriesTable();
      updateMemoryPagination();
      updateBatchBar();
    } catch (e) {
      showToast(e.message || window.t("misc.fetchMemoriesFail"), true);
      renderEmptyTable();
    }
  }

  function renderMemoriesTable() {
    var tbody = document.getElementById("memories-body");
    if (!state.memory.items.length) {
      renderEmptyTable();
      return;
    }

    tbody.innerHTML = state.memory.items.map(function(item) {
      var key = "m:" + item.memory_id;
      var sel = state.memory.selected.has(key) ? " selected" : "";
      var imp = item.importance != null ? Number(item.importance).toFixed(1) : "5.0";
      var impNum = Math.min(10, Math.max(0, parseFloat(imp) || 0));
      var impCls = impNum >= 7 ? "high" : impNum >= 4 ? "medium" : "low";
      return '<tr data-key="' + key + '" class="' + sel + '">' +
        '<td class="cell-mono">' + item.memory_id + '</td>' +
        '<td>' + esc((item.summary || "").substring(0, 120)) + '</td>' +
        '<td><span class="type-tag">' + esc(item.memory_type || "GENERAL") + '</span></td>' +
        '<td><div class="importance-bar"><div class="importance-bar-track">' +
        '<div class="importance-bar-fill ' + impCls + '" style="width:' + (impNum * 10) + '%"></div></div>' +
        '<span style="font-size:12px;color:var(--text-secondary)">' + imp + '</span></div></td>' +
        '<td>' + statusPill(item.status) + '</td>' +
        '<td class="text-secondary" style="font-size:12px">' + esc(item.created_at) + '</td>' +
        '</tr>';
    }).join("");

    tbody.querySelectorAll("tr").forEach(function(row) {
      row.addEventListener("click", function(e) { onMemoryRowClick(row, e); });
      row.addEventListener("dblclick", function() {
        var k = row.dataset.key;
        var item = state.memory.items.find(function(i) { return ("m:" + i.memory_id) === k; });
        if (item) renderPeekMemory(item);
      });
    });
  }

  function renderEmptyTable() {
    document.getElementById("memories-body").innerHTML =
      '<tr><td colspan="6" class="table-empty">' + window.t("table.noData") + '</td></tr>';
  }

  function onMemoryRowClick(row, event) {
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
    renderMemoriesTable();
    updateBatchBar();
  }

  function updateBatchBar() {
    var bar = document.getElementById("batch-bar");
    var count = state.memory.selected.size;
    document.getElementById("batch-count").textContent = count + " selected";
    bar.classList.toggle("visible", count > 0);
  }

  function updateMemoryPagination() {
    var p = state.memory.page;
    var ps = state.memory.pageSize;
    var t = state.memory.total;
    var tp = Math.max(1, Math.ceil(t / ps));
    document.getElementById("mem-pagination-info").textContent = "Page " + p + " / " + tp + " · " + t + " total";
    document.getElementById("mem-prev").disabled = p <= 1;
    document.getElementById("mem-next").disabled = !state.memory.hasMore;
  }

  async function deleteSingleMemory(id) {
    if (!id) return;
    try {
      await apiRequest("memories/batch-delete", { method: "POST", body: { memory_ids: [id] } });
      showToast("Deleted #" + id);
      closePeek();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || "Delete failed", true);
    }
  }

  async function batchDelete() {
    if (!state.memory.selected.size) return;
    var ids = [];
    state.memory.selected.forEach(function(k) {
      var id = parseInt(k.replace("m:", ""));
      if (!isNaN(id)) ids.push(id);
    });
    try {
      await apiRequest("memories/batch-delete", { method: "POST", body: { memory_ids: ids } });
      showToast("Deleted " + ids.length + " memories");
      state.memory.selected.clear();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || "Delete failed", true);
    }
  }

  async function batchArchive() {
    if (!state.memory.selected.size) return;
    var keys = Array.from(state.memory.selected);
    var first = state.memory.items.find(function(i) { return ("m:" + i.memory_id) === keys[0]; });
    var id = first ? first.memory_id : parseInt(keys[0].replace("m:", ""));
    try {
      await apiRequest("memories/update", {
        method: "POST",
        body: { memory_id: id, field: "status", value: "archived" },
      });
      showToast("Archived memory #" + id);
      state.memory.selected.clear();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || "Archive failed", true);
    }
  }

  function initMemoryPage() {
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
      renderMemoriesTable();
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
     Edit Modal
     ================================================================ */
  function openEditModal() {
    var mem = state.selectedMemory;
    if (!mem) return showToast("No memory selected", true);

    document.getElementById("edit-value-content").value = mem.summary || "";
    document.getElementById("edit-value-importance").value = mem.importance || 5;
    document.getElementById("edit-value-type").value = mem.memory_type || "";
    document.getElementById("edit-value-status").value = mem.status || "active";
    document.getElementById("edit-reason").value = "";
    document.getElementById("edit-field").value = "content";
    onEditFieldChange();
    document.getElementById("modal-overlay").classList.add("visible");
    document.getElementById("edit-value-content").focus();
  }

  function closeEditModal() {
    document.getElementById("modal-overlay").classList.remove("visible");
  }

  function onEditFieldChange() {
    var field = document.getElementById("edit-field").value;
    ["content", "importance", "type", "status"].forEach(function(f) {
      var el = document.getElementById("edit-group-" + f);
      if (el) el.classList.toggle("hidden", field !== f);
    });
  }

  async function saveEdit() {
    var mem = state.selectedMemory;
    if (!mem) return;
    var field = document.getElementById("edit-field").value;
    var value;
    if (field === "content") value = document.getElementById("edit-value-content").value.trim();
    else if (field === "importance") value = document.getElementById("edit-value-importance").value;
    else if (field === "type") value = document.getElementById("edit-value-type").value.trim();
    else if (field === "status") value = document.getElementById("edit-value-status").value;

    if (!value) return showToast("Please enter a value", true);
    var reason = document.getElementById("edit-reason").value.trim();
    var id = mem.memory_id || mem.id;

    try {
      document.getElementById("save-edit").disabled = true;
      var result = await apiRequest("memories/update", {
        method: "POST",
        body: { memory_id: id, field: field, value: value, reason: reason },
      });
      showToast((result && result.message) || "Updated");
      closeEditModal();
      closePeek();
      await fetchMemories();
    } catch (e) {
      showToast(e.message || "Update failed", true);
    } finally {
      document.getElementById("save-edit").disabled = false;
    }
  }

  function initEditModal() {
    document.getElementById("edit-field").addEventListener("change", onEditFieldChange);
    document.getElementById("modal-close").addEventListener("click", closeEditModal);
    document.getElementById("cancel-edit").addEventListener("click", closeEditModal);
    document.getElementById("save-edit").addEventListener("click", saveEdit);
    document.getElementById("modal-overlay").addEventListener("click", function(e) {
      if (e.target === this) closeEditModal();
    });
  }

  /* ================================================================
     Recall Page
     ================================================================ */
  async function runRecall() {
    var query = document.getElementById("recall-query").value.trim();
    if (!query) return showToast("Enter a query", true);
    var k = parseInt(document.getElementById("recall-k").value) || 5;
    var session = document.getElementById("recall-session").value.trim() || null;
    var btn = document.getElementById("recall-search-btn");
    btn.disabled = true;

    try {
      var data = await apiRequest("recall/test", {
        method: "POST",
        body: { query: query, k: k, session_id: session },
      }) || {};
      renderRecallResults(data);
    } catch (e) {
      showToast(e.message || "Recall failed", true);
    } finally {
      btn.disabled = false;
    }
  }

  function renderRecallResults(data) {
    var stats = document.getElementById("recall-stats");
    var container = document.getElementById("recall-results");
    var total = data.total || 0;
    var time = data.elapsed_time_ms != null ? data.elapsed_time_ms.toFixed(1) + "ms" : "";

    if (total) {
      stats.classList.remove("hidden");
      document.getElementById("recall-count-text").textContent = total + " results";
      document.getElementById("recall-time-text").textContent = time;
    } else {
      stats.classList.add("hidden");
    }

    if (!total) {
      container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-tertiary)">No matching memories found</div>';
      return;
    }

    container.innerHTML = (data.results || []).map(function(r, i) {
      var pct = r.score_percentage || 0;
      var badgeCls = pct >= 70 ? "high" : pct >= 45 ? "medium" : "low";
      var scores = [
        { label: "Doc-KW", val: r.metadata && r.metadata.document_keyword_score, cls: "doc-kw" },
        { label: "Doc-Vec", val: r.metadata && r.metadata.document_vector_score, cls: "doc-vec" },
        { label: "Graph-KW", val: r.metadata && r.metadata.graph_keyword_score, cls: "graph-kw" },
        { label: "Graph-Vec", val: r.metadata && r.metadata.graph_vector_score, cls: "graph-vec" },
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
      var data = await apiRequest("stats") || {};
      document.getElementById("ss-total").textContent = data.total_memories || data.total_count || "0";
      var sb = data.status_breakdown || {};
      document.getElementById("ss-active").textContent = sb.active || 0;
      document.getElementById("ss-archived").textContent = sb.archived || 0;
      document.getElementById("ss-deleted").textContent = sb.deleted || 0;
      document.getElementById("ss-nodes").textContent = data.graph_nodes || 0;
      document.getElementById("ss-atoms").textContent = data.atom_count || 0;

      /* Importance chart */
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

      /* Atom chart */
      var atoms = data.atom_breakdown || {};
      var atomTypes = ["factual","episodic","preference","relational","planned"];
      var maxA = 1;
      atomTypes.forEach(function(t) { maxA = Math.max(maxA, atoms[t] || 0); });
      document.getElementById("atom-chart").innerHTML = atomTypes.map(function(t) {
        var v = atoms[t] || 0;
        var w = maxA ? (v / maxA * 100).toFixed(0) : 0;
        return '<div class="bar-row"><span class="bar-row-label" style="width:80px">' + t.charAt(0).toUpperCase() + t.slice(1) + '</span>' +
          '<div class="bar-row-track"><div class="bar-row-fill" style="width:' + w + '%"></div></div>' +
          '<span class="bar-row-value">' + v + '</span></div>';
      }).join("");

      /* Sessions */
      var sessions = data.recent_sessions || [];
      if (!sessions.length && data.sessions) {
        sessions = Object.keys(data.sessions).map(function(k) {
          return { session_id: k, message_count: data.sessions[k] };
        }).sort(function(a, b) { return b.message_count - a.message_count; }).slice(0, 10);
      }
      document.getElementById("session-list").innerHTML = sessions.length
        ? sessions.map(function(s) {
          return '<div class="session-item"><span class="session-id">' + esc(s.session_id || s) + '</span>' +
            '<span class="session-meta">' + (s.message_count || "") + (s.last_active ? ' · ' + s.last_active : '') + '</span></div>';
        }).join("")
        : '<div style="color:var(--text-tertiary);text-align:center;padding:20px">No active sessions</div>';

      /* Backups */
      try {
        var backups = await apiRequest("backups") || {};
        var list = backups.backups || [];
        document.getElementById("backup-list").innerHTML = list.length
          ? list.map(function(b) {
            return '<div class="backup-item"><span class="backup-version">' + esc(b.name || b.directory || "") + '</span>' +
              '<span class="backup-date">' + esc(b.backup_timestamp || "") + '</span>' +
              '<span class="backup-files">' + (b.file_count || b.files_copied || 0) + ' files</span></div>';
          }).join("")
          : '<div style="color:var(--text-tertiary);text-align:center;padding:20px">No backups</div>';
      } catch (_) {
        document.getElementById("backup-list").innerHTML = '<div style="color:var(--text-tertiary);text-align:center;padding:20px">Unavailable</div>';
      }
    } catch (e) {
      showToast("Failed to load system overview", true);
    }
  }

  /* ================================================================
     Graph Page (stat pills + delegation to graph-ui.js)
     ================================================================ */
  async function fetchGraphStats() {
    try {
      var data = await apiRequest("stats") || {};
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
    initEditModal();

    document.getElementById("peek-close").addEventListener("click", closePeek);
    document.getElementById("peek-overlay").addEventListener("click", closePeek);
    document.addEventListener("keydown", function(e) {
      if (e.key === "Escape") {
        closePeek();
        closeEditModal();
        state.memory.selected.clear();
        renderMemoriesTable();
        updateBatchBar();
      }
    });

    /* Initial data */
    fetchGraphStats();
    switchPage("graph"); /* trigger graph lazy-load */
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
