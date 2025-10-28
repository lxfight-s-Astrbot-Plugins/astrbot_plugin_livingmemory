(() => {
  const state = {
    token: localStorage.getItem("lmem_token") || "",
    page: 1,
    pageSize: 20,
    total: 0,
    hasMore: false,
    loadAll: false,
    filters: {
      status: "all",
      keyword: "",
    },
    items: [],
    selected: new Set(),
    nuke: {
      active: false,
      operationId: null,
      secondsLeft: 0,
      timer: null,
    },
    currentTab: "memories",
    currentMemoryItem: null, // 用于记忆编辑
  };

  const dom = {
    loginView: document.getElementById("login-view"),
    dashboardView: document.getElementById("dashboard-view"),
    loginForm: document.getElementById("login-form"),
    loginError: document.getElementById("login-error"),
    passwordInput: document.getElementById("password-input"),
    refreshButton: document.getElementById("refresh-button"),
    loadAllButton: document.getElementById("load-all-button"),
    nukeButton: document.getElementById("nuke-button"),
    nukeBanner: document.getElementById("nuke-banner"),
    nukeMessage: document.getElementById("nuke-message"),
    nukeCancel: document.getElementById("nuke-cancel"),
    logoutButton: document.getElementById("logout-button"),
    stats: {
      total: document.getElementById("stat-total"),
      active: document.getElementById("stat-active"),
      archived: document.getElementById("stat-archived"),
      deleted: document.getElementById("stat-deleted"),
      sessions: document.getElementById("stat-sessions"),
    },
    keywordInput: document.getElementById("keyword-input"),
    statusFilter: document.getElementById("status-filter"),
    applyFilter: document.getElementById("apply-filter"),
    selectAll: document.getElementById("select-all"),
    deleteSelected: document.getElementById("delete-selected"),
    tableBody: document.getElementById("memories-body"),
    paginationInfo: document.getElementById("pagination-info"),
    prevPage: document.getElementById("prev-page"),
    nextPage: document.getElementById("next-page"),
    pageSize: document.getElementById("page-size"),
    toast: document.getElementById("toast"),
    drawer: document.getElementById("detail-drawer"),
    drawerClose: document.getElementById("drawer-close"),
    detail: {
      memoryId: document.getElementById("detail-memory-id"),
      source: document.getElementById("detail-source"),
      status: document.getElementById("detail-status"),
      importance: document.getElementById("detail-importance"),
      type: document.getElementById("detail-type"),
      created: document.getElementById("detail-created"),
      access: document.getElementById("detail-access"),
      json: document.getElementById("detail-json"),
    },
  };

  function init() {
    dom.loginForm.addEventListener("submit", onLoginSubmit);
    dom.refreshButton.addEventListener("click", fetchAll);
    dom.loadAllButton.addEventListener("click", onLoadAll);
    // 暂时禁用核爆功能（API未实现）
    // dom.nukeButton.addEventListener("click", onNukeClick);
    dom.nukeButton.disabled = true;
    dom.nukeButton.title = "功能暂未实现";
    
    dom.logoutButton.addEventListener("click", logout);
    dom.prevPage.addEventListener("click", goPrevPage);
    dom.nextPage.addEventListener("click", goNextPage);
    dom.pageSize.addEventListener("change", onPageSizeChange);
    dom.applyFilter.addEventListener("click", applyFilters);
    dom.selectAll.addEventListener("change", toggleSelectAll);
    dom.deleteSelected.addEventListener("click", deleteSelectedMemories);
    dom.drawerClose.addEventListener("click", closeDetailDrawer);
    // dom.nukeCancel.addEventListener("click", onNukeCancel);
    dom.keywordInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        applyFilters();
      }
    });

    // 标签页切换
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // 记忆编辑功能（暂未实现后端API）
    const editBtn = document.getElementById("edit-memory-btn");
    if (editBtn) {
      editBtn.disabled = true;
      editBtn.title = "功能暂未实现";
    }

    // 系统管理功能（部分API未实现）
    const triggerForgetting = document.getElementById("trigger-forgetting");
    const rebuildIndex = document.getElementById("rebuild-index");
    const loadSessions = document.getElementById("load-sessions");

    if (triggerForgetting) {
      triggerForgetting.disabled = true;
      triggerForgetting.title = "功能暂未实现";
    }
    if (rebuildIndex) {
      rebuildIndex.disabled = true;
      rebuildIndex.title = "功能暂未实现";
    }
    if (loadSessions) loadSessions.addEventListener("click", loadSessionsInfo);

    // 调试工具功能（API未实现）
    const runSearchTest = document.getElementById("run-search-test");
    const runFusionCompare = document.getElementById("run-fusion-compare");
    const runMemoryAnalysis = document.getElementById("run-memory-analysis");

    if (runSearchTest) {
      runSearchTest.disabled = true;
      runSearchTest.title = "功能暂未实现";
    }
    if (runFusionCompare) {
      runFusionCompare.disabled = true;
      runFusionCompare.title = "功能暂未实现";
    }
    if (runMemoryAnalysis) {
      runMemoryAnalysis.disabled = true;
      runMemoryAnalysis.title = "功能暂未实现";
    }

    if (state.token) {
      switchView("dashboard");
      showToast("会话已恢复，正在验证...");
      // 先验证 token 是否有效，再加载数据
      fetchStats()
        .then(() => {
          showToast("验证成功，正在加载数据...");
          return fetchMemories();
        })
        .catch((error) => {
          // Token 无效，清除并返回登录页
          console.warn("Token 验证失败:", error.message);
          handleAuthFailure();
        });
    } else {
      switchView("login");
    }
  }

  async function onLoginSubmit(event) {
    event.preventDefault();
    const password = dom.passwordInput.value.trim();
    dom.loginError.textContent = "";

    if (!password) {
      dom.loginError.textContent = "Please enter the password";
      return;
    }

    try {
      const result = await apiRequest("/api/login", {
        method: "POST",
        body: { password },
        skipAuth: true,
      });
      state.token = result.token;
      localStorage.setItem("lmem_token", state.token);
      dom.passwordInput.value = "";
      switchView("dashboard");
      showToast("Login successful, loading data...");
      fetchAll();
    } catch (error) {
      dom.loginError.textContent = error.message || "Login failed, please try again";
    }
  }

  function switchView(view) {
    if (view === "dashboard") {
      dom.loginView.classList.remove("active");
      dom.dashboardView.classList.add("active");
    } else {
      dom.dashboardView.classList.remove("active");
      dom.loginView.classList.add("active");
    }
  }

  async function fetchAll() {
    await Promise.all([fetchStats(), fetchMemories()]);
    // 移除 initNukeStatus，该功能暂未实现
  }

  async function fetchStats() {
    try {
      const response = await apiRequest("/api/stats");
      if (!response.success) {
        throw new Error(response.error || "获取统计信息失败");
      }
      
      const stats = response.data || {};
      dom.stats.total.textContent = stats.total_memories ?? stats.total_count ?? "--";
      
      // 处理状态分布
      const status_breakdown = stats.status_breakdown || {};
      dom.stats.active.textContent = status_breakdown.active ?? 0;
      dom.stats.archived.textContent = status_breakdown.archived ?? 0;
      dom.stats.deleted.textContent = status_breakdown.deleted ?? 0;
      
      // 处理会话信息
      const sessions = stats.sessions || {};
      dom.stats.sessions.textContent = Object.keys(sessions).length || 0;
    } catch (error) {
      showToast(error.message || "无法获取统计信息", true);
    }
  }

  async function fetchMemories() {
    const params = new URLSearchParams();
    // 新API使用limit参数
    const limit = state.loadAll ? 200 : state.pageSize;
    params.set("limit", String(limit));
    
    // 添加会话筛选（可选）
    if (state.filters.session_id) {
      params.set("session_id", state.filters.session_id);
    }

    try {
      const response = await apiRequest(`/api/memories?${params.toString()}`);
      if (!response.success) {
        throw new Error(response.error || "获取记忆失败");
      }

      const data = response.data || {};
      const rawItems = Array.isArray(data.items) ? data.items : [];
      
      // 转换API返回的数据格式以匹配前端期望
      state.items = rawItems.map((item) => ({
        memory_id: item.doc_id || item.id,
        doc_id: item.doc_id || item.id,
        summary: item.content || item.text || "（无内容）",
        content: item.content || item.text,
        memory_type: item.metadata?.memory_type || item.metadata?.type || "GENERAL",
        importance: item.metadata?.importance ?? 5.0,
        status: item.metadata?.status || "active",
        created_at: item.metadata?.create_time ? new Date(item.metadata.create_time * 1000).toLocaleString() : "--",
        last_access: item.metadata?.last_access_time ? new Date(item.metadata.last_access_time * 1000).toLocaleString() : "--",
        source: "storage",
        raw: item,
        raw_json: JSON.stringify(item, null, 2),
      }));

      state.total = data.total ?? state.items.length;
      state.hasMore = state.items.length >= limit;
      
      // 应用关键词过滤（客户端侧）
      if (state.filters.keyword) {
        const keyword = state.filters.keyword.toLowerCase();
        state.items = state.items.filter((item) => 
          item.summary?.toLowerCase().includes(keyword) ||
          item.memory_id?.toString().includes(keyword)
        );
      }

      // 应用状态过滤（客户端侧）
      if (state.filters.status && state.filters.status !== "all") {
        state.items = state.items.filter((item) => 
          item.status === state.filters.status
        );
      }

      // 更新总数
      state.total = state.items.length;

      state.selected.clear();
      dom.selectAll.checked = false;
      dom.deleteSelected.disabled = true;
      renderTable();
      updatePagination();
    } catch (error) {
      renderEmptyTable(error.message || "加载失败");
      showToast(error.message || "获取记忆失败", true);
    }
  }

  function renderTable() {
    if (!state.items.length) {
      renderEmptyTable("暂无数据");
      return;
    }

    const rows = state.items
      .map((item) => {
        const key = getItemKey(item);
        const checked = state.selected.has(key) ? "checked" : "";
        const rowClass = state.selected.has(key) ? "selected" : "";
        const importance =
          item.importance !== undefined && item.importance !== null
            ? Number(item.importance).toFixed(2)
            : "--";
        const statusPill = formatStatus(item.status);

        return `
          <tr data-key="${escapeHTML(key)}" class="${rowClass}">
            <td>
              <input type="checkbox" class="row-select" data-key="${escapeHTML(
                key
              )}" ${checked} />
            </td>
            <td class="mono">${escapeHTML(item.memory_id || item.doc_id || "-")}</td>
            <td class="summary-cell" title="${escapeHTML(item.summary || "")}">
              ${escapeHTML(item.summary || "（无摘要）")}
            </td>
            <td>${escapeHTML(item.memory_type || "--")}</td>
            <td>${importance}</td>
            <td>${statusPill}</td>
            <td>${escapeHTML(item.created_at || "--")}</td>
            <td>${escapeHTML(item.last_access || "--")}</td>
            <td>
              <div class="table-actions">
                <button class="ghost detail-btn" data-key="${escapeHTML(
                  key
                )}">详情</button>
              </div>
            </td>
          </tr>
        `;
      })
      .join("");

    dom.tableBody.innerHTML = rows;

    dom.tableBody.querySelectorAll(".row-select").forEach((checkbox) => {
      checkbox.addEventListener("change", onRowSelect);
    });
    dom.tableBody.querySelectorAll(".detail-btn").forEach((btn) => {
      btn.addEventListener("click", onDetailClick);
    });
  }

  function renderEmptyTable(message) {
    dom.tableBody.innerHTML = `
      <tr>
        <td colspan="9" class="empty">${escapeHTML(message)}</td>
      </tr>
    `;
  }

  function onRowSelect(event) {
    const checkbox = event.target;
    const key = checkbox.dataset.key;
    if (!key) return;

    if (checkbox.checked) {
      state.selected.add(key);
    } else {
      state.selected.delete(key);
    }

    const row = checkbox.closest("tr");
    if (row) {
      row.classList.toggle("selected", checkbox.checked);
    }
    updateSelectionState();
  }

  function toggleSelectAll(event) {
    const checked = event.target.checked;
    if (!state.items.length) {
      event.target.checked = false;
      return;
    }
    state.items.forEach((item) => {
      const key = getItemKey(item);
      if (checked) {
        state.selected.add(key);
      } else {
        state.selected.delete(key);
      }
    });
    renderTable();
    updateSelectionState();
  }

  function updateSelectionState() {
    dom.deleteSelected.disabled = state.selected.size === 0;
    if (!state.items.length) {
      dom.selectAll.checked = false;
      return;
    }
    const allSelected = state.items.every((item) =>
      state.selected.has(getItemKey(item))
    );
    dom.selectAll.checked = allSelected;
  }

  function applyFilters() {
    state.filters.status = dom.statusFilter.value;
    state.filters.keyword = dom.keywordInput.value.trim();
    state.page = 1;
    state.loadAll = false;
    dom.loadAllButton.classList.remove("active");
    fetchMemories();
  }

  function onPageSizeChange() {
    state.pageSize = Number(dom.pageSize.value) || 20;
    state.page = 1;
    state.loadAll = false;
    dom.loadAllButton.classList.remove("active");
    fetchMemories();
  }

  function goPrevPage() {
    if (state.page > 1) {
      state.page -= 1;
      fetchMemories();
    }
  }

  function goNextPage() {
    if (state.hasMore) {
      state.page += 1;
      fetchMemories();
    }
  }

  function onLoadAll() {
    state.loadAll = !state.loadAll;
    dom.loadAllButton.classList.toggle("active", state.loadAll);
    state.page = 1;
    fetchMemories();
  }

  function updatePagination() {
    if (state.loadAll) {
      dom.paginationInfo.textContent = `共 ${state.items.length} 条记录`;
    } else {
      const totalPages = state.total
        ? Math.max(1, Math.ceil(state.total / state.pageSize))
        : 1;
      dom.paginationInfo.textContent = `第 ${state.page} / ${totalPages} 页 · 共 ${state.total} 条`;
    }
    dom.prevPage.disabled = state.loadAll || state.page <= 1;
    dom.nextPage.disabled = state.loadAll || !state.hasMore;
  }

  async function deleteSelectedMemories() {
    if (state.selected.size === 0) {
      return;
    }
    const count = state.selected.size;
    const confirmed = window.confirm(`确定删除 ${count} 条记忆？此操作无法撤销。`);
    if (!confirmed) {
      return;
    }

    const memoryIds = [];
    state.items.forEach((item) => {
      const key = getItemKey(item);
      if (state.selected.has(key)) {
        if (item.doc_id !== null && item.doc_id !== undefined) {
          memoryIds.push(item.doc_id);
        } else if (item.memory_id) {
          memoryIds.push(item.memory_id);
        }
      }
    });

    try {
      const response = await apiRequest("/api/memories/batch-delete", {
        method: "POST",
        body: {
          memory_ids: memoryIds,
        },
      });
      
      if (!response.success) {
        throw new Error(response.error || "删除失败");
      }

      const data = response.data || {};
      showToast(`已删除 ${data.deleted_count || count} 条记忆`);
      state.selected.clear();
      fetchMemories();
      fetchStats();
    } catch (error) {
      showToast(error.message || "删除失败", true);
    }
  }

  function logout() {
    state.token = "";
    state.selected.clear();
    localStorage.removeItem("lmem_token");
    switchView("login");
    showToast("Logged out");
  }

  function onDetailClick(event) {
    const key = event.target.dataset.key;
    if (!key) return;
    const item = state.items.find((record) => getItemKey(record) === key);
    if (!item) {
      showToast("未找到对应的记录", true);
      return;
    }
    openDetailDrawer(item);
  }

  function openDetailDrawer(item) {
    dom.detail.memoryId.textContent = item.memory_id || item.doc_id || "--";
    dom.detail.source.textContent =
      item.source === "storage" ? "自定义存储" : "向量存储";
    dom.detail.status.textContent = item.status || "--";
    dom.detail.importance.textContent =
      item.importance !== undefined && item.importance !== null
        ? Number(item.importance).toFixed(2)
        : "--";
    dom.detail.type.textContent = item.memory_type || "--";
    dom.detail.created.textContent = item.created_at || "--";
    dom.detail.access.textContent = item.last_access || "--";
    dom.detail.json.textContent = item.raw_json || JSON.stringify(item.raw, null, 2);
    dom.drawer.classList.remove("hidden");
  }

  function closeDetailDrawer() {
    dom.drawer.classList.add("hidden");
  }

  async function initNukeStatus() {
    if (!state.token) {
      return;
    }
    try {
      const status = await apiRequest("/api/memories/nuke");
      if (status && status.pending) {
        startNukeCountdown(status);
      } else {
      }
    } catch (_error) {
    }
  }

  async function onNukeClick() {
    if (state.nuke.active) {
      return;
    }
    dom.nukeButton.disabled = true;
    try {
      const result = await apiRequest("/api/memories/nuke", { method: "POST" });
      if (result && result.pending) {
        startNukeCountdown(result);
        showToast(result.detail || "Memory wipe scheduled");
      } else {
        dom.nukeButton.disabled = false;
      }
    } catch (error) {
      dom.nukeButton.disabled = false;
      showToast(error.message || "Unable to schedule memory wipe", true);
    }
  }

  async function onNukeCancel() {
    if (!state.nuke.active || !state.nuke.operationId) {
      return;
    }
    dom.nukeCancel.disabled = true;
    try {
      await apiRequest(`/api/memories/nuke/${state.nuke.operationId}`, {
        method: "DELETE",
      });
      stopNukeCountdown();
      showToast("Memory wipe cancelled");
    } catch (error) {
      dom.nukeCancel.disabled = false;
      showToast(error.message || "取消失败，请稍后重试", true);
    }
  }

  function startNukeCountdown(info) {
    const seconds = Number.isFinite(Number(info.seconds_left))
      ? Number(info.seconds_left)
      : 30;

    state.nuke.active = true;
    state.nuke.operationId = info.operation_id || null;
    state.nuke.secondsLeft = seconds;

    updateNukeBannerWithEffects(); // 使用新的视觉效果函数
    dom.nukeButton.disabled = true;

    state.nuke.timer = setInterval(() => {
      if (state.nuke.secondsLeft > 0) {
        state.nuke.secondsLeft -= 1;
        updateNukeBannerWithEffects(); // 使用新的视觉效果函数
        return;
      }

      clearInterval(state.nuke.timer);
      state.nuke.timer = null;
      updateNukeBannerWithEffects(); // 使用新的视觉效果函数
      dom.nukeCancel.disabled = true;

      setTimeout(async () => {
        // 核爆动画完成后,清理状态,但不自动加载数据
        stopNukeCountdown();
        // 清空当前显示的数据
        state.items = [];
        state.total = 0;
        state.page = 1;
        renderEmptyTable("所有记忆已被清除,点击「刷新」或「加载全部」查看结果");
        updatePagination();
        // 更新统计信息显示为 0
        dom.stats.total.textContent = "0";
        dom.stats.active.textContent = "0";
        dom.stats.archived.textContent = "0";
        dom.stats.deleted.textContent = "0";
        showToast("核爆完成!所有记忆已被清除");
      }, 4000); // 延长到4秒，等待核爆动画完成
    }, 1000);
  }

  function stopNukeCountdown() {
    if (state.nuke.timer) {
      clearInterval(state.nuke.timer);
      state.nuke.timer = null;
    }
    state.nuke.active = false;
    state.nuke.operationId = null;
    state.nuke.secondsLeft = 0;

    // 清除所有视觉效果
    clearNukeVisualEffects();

    if (dom.nukeBanner) {
      dom.nukeBanner.classList.add("hidden");
    }
    if (dom.nukeMessage) {
      dom.nukeMessage.textContent = "";
    }
    if (dom.nukeCancel) {
      dom.nukeCancel.disabled = false;
    }
    if (dom.nukeButton) {
      dom.nukeButton.disabled = false;
    }
  }

  async function apiRequest(path, options = {}) {
    const { method = "GET", body, skipAuth = false } = options;
    const headers = new Headers(options.headers || {});

    if (body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    if (!skipAuth) {
      if (!state.token) {
        handleAuthFailure();
        throw new Error("尚未登录");
      }
      headers.set("Authorization", `Bearer ${state.token}`);
    }

    const fetchOptions = {
      method,
      headers,
      credentials: "same-origin",
    };

    if (body !== undefined) {
      fetchOptions.body = typeof body === "string" ? body : JSON.stringify(body);
    }

    const response = await fetch(path, fetchOptions);

    if (response.status === 401) {
      handleAuthFailure();
      throw new Error("会话已过期，请重新登录");
    }

    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error("服务器返回格式错误");
    }

    if (!response.ok) {
      // 处理HTTP错误状态码
      const message =
        (data && (data.detail || data.message || data.error)) || "请求失败";
      throw new Error(message);
    }

    // API现在返回 {success: true/false, data: {...}, error: "..."} 格式
    // 但也要兼容直接返回数据的旧格式
    return data;
  }

  function handleAuthFailure() {
    state.token = "";
    localStorage.removeItem("lmem_token");
    switchView("login");
  }

  function showToast(message, isError = false) {
    dom.toast.textContent = message;
    dom.toast.classList.remove("hidden", "error");
    if (isError) {
      dom.toast.classList.add("error");
    }
    dom.toast.classList.add("visible");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
      dom.toast.classList.remove("visible");
    }, 3000);
  }

  function getItemKey(item) {
    if (item.doc_id !== null && item.doc_id !== undefined) {
      return `doc:${item.doc_id}`;
    }
    if (item.memory_id) {
      return `mem:${item.memory_id}`;
    }
    return `row:${state.items.indexOf(item)}`;
  }

  function formatStatus(status) {
    const value = (status || "active").toLowerCase();
    let text = "活跃";
    let cls = "status-pill";
    if (value === "archived") {
      text = "已归档";
      cls += " archived";
    } else if (value === "deleted") {
      text = "已删除";
      cls += " deleted";
    }
    return `<span class="${cls}">${text}</span>`;
  }

  function escapeHTML(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ============================================
  // 核爆视觉效果函数
  // ============================================

  /**
   * 触发完整的核爆视觉效果序列
   */
  function triggerNukeVisualEffects() {
    const overlay = document.getElementById("nuke-overlay");
    const app = document.getElementById("app");
    const tableBody = document.getElementById("memories-body");

    if (!overlay || !app) return;

    // 1. 激活核爆遮罩层
    overlay.classList.add("active");

    // 2. 添加屏幕震动效果
    app.classList.add("screen-shake");

    // 3. 数据表格粒子化消失
    if (tableBody) {
      const rows = tableBody.querySelectorAll("tr");
      rows.forEach((row, index) => {
        setTimeout(() => {
          row.classList.add("particle-fade");
        }, index * 50); // 每行延迟50ms
      });
    }

    // 4. 添加数据撕裂效果到所有卡片
    const cards = document.querySelectorAll(".card");
    setTimeout(() => {
      cards.forEach((card) => {
        card.classList.add("data-glitch");
      });
    }, 800);

    // 5. 生成灰烬飘落粒子
    setTimeout(() => {
      createAshParticles();
    }, 1500);

    // 6. 停止所有动画效果
    setTimeout(() => {
      app.classList.remove("screen-shake");
      cards.forEach((card) => {
        card.classList.remove("data-glitch");
      });
    }, 3000);

    // 7. 移除核爆遮罩层，添加界面恢复动画
    setTimeout(() => {
      overlay.classList.remove("active");
      app.classList.add("fade-in-recovery");

      // 移除恢复动画类
      setTimeout(() => {
        app.classList.remove("fade-in-recovery");
      }, 1500);
    }, 3500);
  }

  /**
   * 创建灰烬飘落粒子效果
   */
  function createAshParticles() {
    const overlay = document.getElementById("nuke-overlay");
    if (!overlay) return;

    const particleCount = 50; // 粒子数量

    for (let i = 0; i < particleCount; i++) {
      const particle = document.createElement("div");
      particle.className = "ash-particle";

      // 随机位置
      particle.style.left = `${Math.random() * 100}%`;
      particle.style.top = `${Math.random() * 20}%`;

      // 随机飘移距离
      const drift = (Math.random() - 0.5) * 200; // -100px 到 100px
      particle.style.setProperty("--drift", `${drift}px`);

      // 随机动画时长
      const duration = 2 + Math.random() * 3; // 2-5秒
      particle.style.animationDuration = `${duration}s`;

      // 随机延迟
      const delay = Math.random() * 0.5; // 0-0.5秒
      particle.style.animationDelay = `${delay}s`;

      overlay.appendChild(particle);

      // 动画结束后移除粒子
      setTimeout(() => {
        particle.remove();
      }, (duration + delay) * 1000);
    }
  }

  /**
   * 更新倒计时横幅 - 添加视觉警告效果
   */
  function updateNukeBannerWithEffects() {
    if (!state.nuke.active || !dom.nukeBanner) {
      return;
    }

    const overlay = document.getElementById("nuke-overlay");
    const seconds = Math.max(0, state.nuke.secondsLeft);

    // 显示横幅
    dom.nukeBanner.classList.remove("hidden");

    // 更新倒计时文本
    const message =
      seconds > 0
        ? `所有记忆将在 ${seconds} 秒后被抹除。立即取消以中止核爆！`
        : "正在抹除所有记忆... 请保持窗口打开。";
    dom.nukeMessage.textContent = message;

    // 禁用/启用取消按钮
    if (dom.nukeCancel) {
      dom.nukeCancel.disabled = seconds === 0;
    }

    // 添加视觉警告效果
    if (seconds > 0 && seconds <= 30) {
      // 倒计时阶段 - 红色闪烁警告
      if (!overlay.classList.contains("nuke-warning")) {
        overlay.classList.add("nuke-warning");
      }

      // 最后10秒 - 加强警告
      if (seconds <= 10) {
        dom.nukeBanner.classList.add("critical");

        // 最后5秒 - 震动效果
        if (seconds <= 5) {
          const app = document.getElementById("app");
          if (app && !app.classList.contains("screen-shake")) {
            app.classList.add("screen-shake");
          }
        }
      }
    }

    // 倒计时结束 - 触发核爆效果
    if (seconds === 0) {
      // 移除警告效果
      overlay.classList.remove("nuke-warning");
      dom.nukeBanner.classList.remove("critical");

      // 触发完整核爆视觉效果
      triggerNukeVisualEffects();
    }
  }

  /**
   * 清除所有核爆视觉效果
   */
  function clearNukeVisualEffects() {
    const overlay = document.getElementById("nuke-overlay");
    const app = document.getElementById("app");
    const cards = document.querySelectorAll(".card");

    if (overlay) {
      overlay.classList.remove("active", "nuke-warning");
    }

    if (app) {
      app.classList.remove("screen-shake", "fade-in-recovery");
    }

    cards.forEach((card) => {
      card.classList.remove("data-glitch");
    });

    if (dom.nukeBanner) {
      dom.nukeBanner.classList.remove("critical");
    }
  }

  // ============================================
  // 标签页切换功能
  // ============================================

  function switchTab(tabName) {
    state.currentTab = tabName;

    // 更新标签按钮状态
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      if (btn.dataset.tab === tabName) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });

    // 更新标签页内容
    document.querySelectorAll(".tab-content").forEach((content) => {
      if (content.dataset.tab === tabName) {
        content.classList.add("active");
      } else {
        content.classList.remove("active");
      }
    });
  }

  // ============================================
  // 记忆编辑功能
  // ============================================

  function openEditModal() {
    if (!state.currentMemoryItem) {
      showToast("未找到当前记忆信息", true);
      return;
    }

    const modal = document.getElementById("edit-modal");
    modal.classList.remove("hidden");

    // 设置默认值
    const item = state.currentMemoryItem;
    document.getElementById("edit-value-content").value = item.summary || "";
    document.getElementById("edit-value-importance").value = item.importance || 5;
    document.getElementById("edit-value-type").value = item.memory_type || "";
    document.getElementById("edit-value-status").value = item.status || "active";
    document.getElementById("edit-reason").value = "";

    // 显示内容字段
    onEditFieldChange();
  }

  function closeEditModal() {
    const modal = document.getElementById("edit-modal");
    modal.classList.add("hidden");
  }

  function onEditFieldChange() {
    const field = document.getElementById("edit-field").value;

    // 隐藏所有字段组
    document.getElementById("edit-content-group").classList.add("hidden");
    document.getElementById("edit-importance-group").classList.add("hidden");
    document.getElementById("edit-type-group").classList.add("hidden");
    document.getElementById("edit-status-group").classList.add("hidden");

    // 显示选中的字段组
    if (field === "content") {
      document.getElementById("edit-content-group").classList.remove("hidden");
    } else if (field === "importance") {
      document.getElementById("edit-importance-group").classList.remove("hidden");
    } else if (field === "type") {
      document.getElementById("edit-type-group").classList.remove("hidden");
    } else if (field === "status") {
      document.getElementById("edit-status-group").classList.remove("hidden");
    }
  }

  async function saveMemoryEdit() {
    if (!state.currentMemoryItem) {
      showToast("未找到当前记忆信息", true);
      return;
    }

    const field = document.getElementById("edit-field").value;
    let value;

    if (field === "content") {
      value = document.getElementById("edit-value-content").value.trim();
    } else if (field === "importance") {
      value = document.getElementById("edit-value-importance").value;
    } else if (field === "type") {
      value = document.getElementById("edit-value-type").value.trim();
    } else if (field === "status") {
      value = document.getElementById("edit-value-status").value;
    }

    if (!value) {
      showToast("请输入新值", true);
      return;
    }

    const reason = document.getElementById("edit-reason").value.trim();
    const memoryId = state.currentMemoryItem.memory_id || state.currentMemoryItem.doc_id;

    try {
      document.getElementById("save-edit").disabled = true;
      const result = await apiRequest(`/api/memories/${memoryId}`, {
        method: "PUT",
        body: { field, value, reason },
      });

      showToast(result.message || "更新成功");
      closeEditModal();
      closeDetailDrawer();
      fetchMemories(); // 刷新列表
    } catch (error) {
      showToast(error.message || "更新失败", true);
    } finally {
      document.getElementById("save-edit").disabled = false;
    }
  }

  function openDetailDrawer(item) {
    state.currentMemoryItem = item; // 保存当前项
    dom.detail.memoryId.textContent = item.memory_id || item.doc_id || "--";
    dom.detail.source.textContent =
      item.source === "storage" ? "自定义存储" : "向量存储";
    dom.detail.status.textContent = item.status || "--";
    dom.detail.importance.textContent =
      item.importance !== undefined && item.importance !== null
        ? Number(item.importance).toFixed(2)
        : "--";
    dom.detail.type.textContent = item.memory_type || "--";
    dom.detail.created.textContent = item.created_at || "--";
    dom.detail.access.textContent = item.last_access || "--";
    dom.detail.json.textContent = item.raw_json || JSON.stringify(item.raw, null, 2);
    dom.drawer.classList.remove("hidden");
  }

  // ============================================
  // 系统管理功能
  // ============================================

  async function triggerForgettingAgent() {
    const btn = document.getElementById("trigger-forgetting");
    const resultBox = document.getElementById("forgetting-result");

    try {
      btn.disabled = true;
      resultBox.classList.remove("hidden", "success", "error");
      resultBox.textContent = "正在运行遗忘代理...";

      const result = await apiRequest("/api/admin/forgetting-agent/trigger", {
        method: "POST",
      });

      resultBox.classList.add("success");
      resultBox.innerHTML = `
        <strong>执行成功!</strong><br>
        ${escapeHTML(result.message || "遗忘代理执行完成")}
      `;
      showToast(result.message || "遗忘代理执行完成");
      fetchStats(); // 刷新统计
    } catch (error) {
      resultBox.classList.add("error");
      resultBox.textContent = error.message || "执行失败";
      showToast(error.message || "执行失败", true);
    } finally {
      btn.disabled = false;
    }
  }

  async function rebuildSparseIndex() {
    const btn = document.getElementById("rebuild-index");
    const resultBox = document.getElementById("rebuild-result");

    try {
      btn.disabled = true;
      resultBox.classList.remove("hidden", "success", "error");
      resultBox.textContent = "正在重建索引...";

      const result = await apiRequest("/api/admin/sparse-index/rebuild", {
        method: "POST",
      });

      resultBox.classList.add("success");
      resultBox.innerHTML = `
        <strong>重建成功!</strong><br>
        索引文档数: ${result.indexed_count}
      `;
      showToast("稀疏索引重建完成");
    } catch (error) {
      resultBox.classList.add("error");
      resultBox.textContent = error.message || "重建失败";
      showToast(error.message || "重建失败", true);
    } finally {
      btn.disabled = false;
    }
  }

  async function loadSessionsInfo() {
    const infoBox = document.getElementById("sessions-info");
    const listBox = document.getElementById("sessions-list");

    try {
      // 使用新的 ConversationManager API
      const response = await apiRequest("/api/conversations/recent?limit=50");
      if (!response.success) {
        throw new Error(response.error || "获取会话列表失败");
      }

      const data = response.data || {};
      const sessions = data.sessions || [];

      infoBox.classList.remove("hidden");
      document.getElementById("total-sessions").textContent = data.total || sessions.length;
      // 这些字段需要从配置API获取
      document.getElementById("max-sessions").textContent = "100"; // 默认值
      document.getElementById("session-ttl").textContent = "3600"; // 默认值

      if (sessions.length > 0) {
        const html = `
          <table style="width: 100%; margin-top: 16px; border-collapse: collapse;">
            <thead>
              <tr style="background: var(--surface-alt); text-align: left;">
                <th style="padding: 8px; border: 1px solid var(--border);">会话 ID</th>
                <th style="padding: 8px; border: 1px solid var(--border);">平台</th>
                <th style="padding: 8px; border: 1px solid var(--border);">消息数</th>
                <th style="padding: 8px; border: 1px solid var(--border);">参与者</th>
                <th style="padding: 8px; border: 1px solid var(--border);">最后活跃</th>
              </tr>
            </thead>
            <tbody>
              ${sessions
                .map(
                  (s) => `
                <tr>
                  <td style="padding: 8px; border: 1px solid var(--border); font-family: monospace;">${escapeHTML(s.session_id)}</td>
                  <td style="padding: 8px; border: 1px solid var(--border);">${escapeHTML(s.platform || "--")}</td>
                  <td style="padding: 8px; border: 1px solid var(--border);">${s.message_count || 0}</td>
                  <td style="padding: 8px; border: 1px solid var(--border);">${(s.participants || []).length}</td>
                  <td style="padding: 8px; border: 1px solid var(--border);">${escapeHTML(s.last_active_at || "--")}</td>
                </tr>
              `
                )
                .join("")}
            </tbody>
          </table>
        `;
        listBox.innerHTML = html;
      } else {
        listBox.innerHTML = "<p>暂无活跃会话</p>";
      }

      showToast("会话信息加载完成");
    } catch (error) {
      showToast(error.message || "加载失败", true);
    }
  }

  // ============================================
  // 调试工具功能
  // ============================================

  async function runSearchTestFunc() {
    const resultBox = document.getElementById("search-test-result");
    const query = document.getElementById("debug-query").value.trim();

    if (!query) {
      showToast("请输入查询内容", true);
      return;
    }

    try {
      document.getElementById("run-search-test").disabled = true;
      resultBox.classList.remove("hidden", "success", "error");
      resultBox.textContent = "正在测试...";

      const payload = {
        query,
        mode: document.getElementById("debug-mode").value,
        top_k: parseInt(document.getElementById("debug-top-k").value),
      };

      const result = await apiRequest("/api/debug/search-test", {
        method: "POST",
        body: payload,
      });

      resultBox.classList.add("success");
      let html = `
        <strong>测试完成</strong><br>
        查询: ${escapeHTML(result.query)}<br>
        模式: ${result.mode}<br>
        耗时: ${result.elapsed_time}秒<br>
        结果数: ${result.result_count}<br><br>
        <strong>检索结果:</strong>
      `;

      if (result.results && result.results.length > 0) {
        html += "<pre>" + JSON.stringify(result.results, null, 2) + "</pre>";
      } else {
        html += "<p>无结果</p>";
      }

      resultBox.innerHTML = html;
      showToast("检索测试完成");
    } catch (error) {
      resultBox.classList.add("error");
      resultBox.textContent = error.message || "测试失败";
      showToast(error.message || "测试失败", true);
    } finally {
      document.getElementById("run-search-test").disabled = false;
    }
  }

  async function runFusionCompareFunc() {
    const resultBox = document.getElementById("fusion-compare-result");
    const query = document.getElementById("fusion-compare-query").value.trim();

    if (!query) {
      showToast("请输入查询内容", true);
      return;
    }

    try {
      document.getElementById("run-fusion-compare").disabled = true;
      resultBox.classList.remove("hidden", "success", "error");
      resultBox.textContent = "正在对比...";

      const selectElem = document.getElementById("fusion-compare-strategies");
      const strategies = Array.from(selectElem.selectedOptions).map((opt) => opt.value);

      if (strategies.length === 0) {
        showToast("请至少选择一个策略", true);
        document.getElementById("run-fusion-compare").disabled = false;
        return;
      }

      const payload = { query, strategies, top_k: 5 };

      const result = await apiRequest("/api/debug/fusion-comparison", {
        method: "POST",
        body: payload,
      });

      resultBox.classList.add("success");
      let html = `
        <strong>对比完成</strong><br>
        查询: ${escapeHTML(result.query)}<br>
        测试策略数: ${result.strategies_tested}<br><br>
        <strong>对比结果:</strong>
      `;

      if (result.comparison && result.comparison.length > 0) {
        html += "<pre>" + JSON.stringify(result.comparison, null, 2) + "</pre>";
      }

      resultBox.innerHTML = html;
      showToast("策略对比完成");
    } catch (error) {
      resultBox.classList.add("error");
      resultBox.textContent = error.message || "对比失败";
      showToast(error.message || "对比失败", true);
    } finally {
      document.getElementById("run-fusion-compare").disabled = false;
    }
  }

  async function runMemoryAnalysisFunc() {
    const resultBox = document.getElementById("memory-analysis-result");

    try {
      document.getElementById("run-memory-analysis").disabled = true;
      resultBox.classList.remove("hidden", "success", "error");
      resultBox.textContent = "正在分析...";

      const result = await apiRequest("/api/debug/memory-analysis");

      resultBox.classList.add("success");
      let html = `
        <strong>分析完成</strong><br>
        总记忆数: ${result.total_memories}<br>
        平均重要性: ${result.average_importance}<br><br>
        <strong>详细统计:</strong>
        <pre>${JSON.stringify(
          {
            importance_distribution: result.importance_distribution,
            type_distribution: result.type_distribution,
            status_distribution: result.status_distribution,
          },
          null,
          2
        )}</pre>
      `;

      resultBox.innerHTML = html;
      showToast("记忆分析完成");
    } catch (error) {
      resultBox.classList.add("error");
      resultBox.textContent = error.message || "分析失败";
      showToast(error.message || "分析失败", true);
    } finally {
      document.getElementById("run-memory-analysis").disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
