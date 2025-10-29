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
    currentMemoryItem: null,
    searchTimeout: null, // 用于防抖搜索
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
    dom.nukeButton.addEventListener("click", onNukeClick);
    dom.logoutButton.addEventListener("click", logout);
    dom.prevPage.addEventListener("click", goPrevPage);
    dom.nextPage.addEventListener("click", goNextPage);
    dom.pageSize.addEventListener("change", onPageSizeChange);
    dom.applyFilter.addEventListener("click", applyFilters);
    dom.selectAll.addEventListener("change", toggleSelectAll);
    dom.deleteSelected.addEventListener("click", deleteSelectedMemories);
    dom.drawerClose.addEventListener("click", closeDetailDrawer);
    dom.nukeCancel.addEventListener("click", onNukeCancel);

    // 关键字输入 - 防抖搜索
    dom.keywordInput.addEventListener("input", (event) => {
      // 清除之前的搜索计时器
      if (state.searchTimeout) {
        clearTimeout(state.searchTimeout);
      }

      // 设置新的搜索计时器（500ms 防抖延迟）
      state.searchTimeout = setTimeout(() => {
        state.filters.keyword = event.target.value.trim();
        state.page = 1;
        state.loadAll = false;
        dom.loadAllButton.classList.remove("active");
        fetchMemories();
      }, 500);
    });

    // 保留 Enter 键快速搜索
    dom.keywordInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        // 立即搜索，不等待防抖
        if (state.searchTimeout) {
          clearTimeout(state.searchTimeout);
        }
        applyFilters();
      }
    });

    // 记忆编辑功能
    const editBtn = document.getElementById("edit-memory-btn");
    if (editBtn) {
      editBtn.addEventListener("click", openEditModal);
    }

    // 编辑字段变更事件
    const editFieldSelect = document.getElementById("edit-field");
    if (editFieldSelect) {
      editFieldSelect.addEventListener("change", onEditFieldChange);
    }

    // 编辑模态框按钮
    const modalCloseBtn = document.getElementById("modal-close");
    const cancelEditBtn = document.getElementById("cancel-edit");
    const saveEditBtn = document.getElementById("save-edit");

    if (modalCloseBtn) {
      modalCloseBtn.addEventListener("click", closeEditModal);
    }
    if (cancelEditBtn) {
      cancelEditBtn.addEventListener("click", closeEditModal);
    }
    if (saveEditBtn) {
      saveEditBtn.addEventListener("click", saveMemoryEdit);
    }

    if (state.token) {
      switchView("dashboard");
      showToast("会话已恢复，正在验证...");
      fetchStats()
        .then(() => {
          showToast("验证成功，正在加载数据...");
          return fetchMemories();
        })
        .catch((error) => {
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

      // 总记忆数
      dom.stats.total.textContent = stats.total_memories ?? stats.total_count ?? "0";

      // 处理状态分布（支持两种格式）
      const statusBreakdown = stats.status_breakdown || {};
      dom.stats.active.textContent = statusBreakdown.active ?? 0;
      dom.stats.archived.textContent = statusBreakdown.archived ?? 0;
      dom.stats.deleted.textContent = statusBreakdown.deleted ?? 0;

      // 处理会话信息
      const sessions = stats.sessions || {};
      const sessionCount = Object.keys(sessions).length;
      dom.stats.sessions.textContent = sessionCount || "0";

      // 调试日志
      console.log("[统计信息]", {
        total: stats.total_memories,
        status: statusBreakdown,
        sessions: sessionCount,
      });
    } catch (error) {
      logger.error("[统计信息获取失败]", error.message);
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

    // 构建表格行
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

    // 绑定事件
    dom.tableBody.querySelectorAll(".row-select").forEach((checkbox) => {
      checkbox.addEventListener("change", onRowSelect);
    });
    dom.tableBody.querySelectorAll(".detail-btn").forEach((btn) => {
      btn.addEventListener("click", onDetailClick);
    });

    // 显示搜索结果计数
    const resultCount = state.items.length;
    const countMsg = state.filters.keyword || state.filters.status !== "all"
      ? `搜索结果：找到 ${resultCount} 条记忆`
      : "";
    if (countMsg) {
      showToast(countMsg);
    }
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

    // 显示当前筛选状态
    if (state.filters.keyword || state.filters.status !== "all") {
      let filterInfo = "筛选中:";
      if (state.filters.keyword) {
        filterInfo += ` 关键词="${state.filters.keyword}"`;
      }
      if (state.filters.status !== "all") {
        filterInfo += ` 状态="${state.filters.status}"`;
      }
      dom.paginationInfo.textContent += ` | ${filterInfo}`;
    }

    dom.prevPage.disabled = state.loadAll || state.page <= 1;
    dom.nextPage.disabled = state.loadAll || !state.hasMore;
  }

  async function deleteSelectedMemories() {
    if (state.selected.size === 0) {
      return;
    }
    const count = state.selected.size;

    // 改进的确认对话框
    const confirmed = window.confirm(
      `⚠️  确认删除？\n\n` +
      `即将删除 ${count} 条记忆。\n` +
      `此操作无法撤销！\n\n` +
      `点击"确定"继续删除，点击"取消"保留。`
    );

    if (!confirmed) {
      showToast("已取消删除操作");
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
      // 显示加载状态
      dom.deleteSelected.disabled = true;
      const originalText = dom.deleteSelected.textContent;
      dom.deleteSelected.textContent = "删除中...";

      console.log("[删除] 准备删除记忆", { count: memoryIds.length, ids: memoryIds });

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
      const deletedCount = data.deleted_count || 0;
      const failedCount = data.failed_count || 0;
      const failedIds = data.failed_ids || [];

      console.log("[删除结果]", {
        deleted: deletedCount,
        failed: failedCount,
        failedIds: failedIds,
      });

      // 根据结果显示相应的提示
      if (deletedCount === 0 && failedCount > 0) {
        // ❌ 全部失败
        showToast(
          `❌ 删除失败：全部 ${failedCount} 条记忆无法删除\n` +
          `失败ID: ${failedIds.join(", ")}\n` +
          `请检查日志了解详情`,
          true
        );
        logger.error("删除失败 - 所有记忆都无法删除", { failedIds });
      } else if (failedCount > 0) {
        // ⚠️ 部分失败
        showToast(
          `⚠️ 部分删除失败：成功 ${deletedCount} 条，失败 ${failedCount} 条\n` +
          `失败ID: ${failedIds.join(", ")}`
        );
        logger.warn("部分删除失败", { deletedCount, failedCount, failedIds });
      } else if (deletedCount > 0) {
        // ✅ 全部成功
        showToast(`✅ 已成功删除 ${deletedCount} 条记忆`);
      } else {
        // ⚠️ 没有删除任何记忆
        showToast("⚠️ 没有删除任何记忆", true);
      }

      // 清空选择并刷新数据
      state.selected.clear();
      dom.selectAll.checked = false;
      await fetchMemories();
      await fetchStats();
    } catch (error) {
      logger.error("[删除异常]", error);
      showToast(error.message || "删除失败，请稍后重试", true);
    } finally {
      dom.deleteSelected.disabled = false;
      dom.deleteSelected.textContent = originalText;
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

    // 显示确认对话框
    const confirmed = window.confirm(
      "⚠️  警告：你将启动核爆模式！\n\n" +
      "系统将模拟删除所有记忆（刷新后恢复）。\n\n" +
      "30秒倒计时后开始执行。\n\n" +
      "点击「取消核爆」可中止操作。\n\n" +
      "确定要继续吗？"
    );

    if (!confirmed) {
      return;
    }

    dom.nukeButton.disabled = true;
    try {
      // 触发核爆倒计时
      startNukeCountdown({
        seconds_left: 30,
        operation_id: "nuke_" + Date.now(),
      });
      showToast("核爆已启动！30秒后执行删除操作");
    } catch (error) {
      dom.nukeButton.disabled = false;
      showToast(error.message || "无法启动核爆模式", true);
    }
  }

  async function onNukeCancel() {
    if (!state.nuke.active || !state.nuke.operationId) {
      return;
    }
    dom.nukeCancel.disabled = true;
    try {
      // 取消核爆
      stopNukeCountdown();
      showToast("✅ 核爆已取消！记忆保留");
      dom.nukeButton.disabled = false;
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

    updateNukeBannerWithEffects();
    dom.nukeButton.disabled = true;

    state.nuke.timer = setInterval(() => {
      if (state.nuke.secondsLeft > 0) {
        state.nuke.secondsLeft -= 1;
        updateNukeBannerWithEffects();
        return;
      }

      clearInterval(state.nuke.timer);
      state.nuke.timer = null;
      updateNukeBannerWithEffects();
      dom.nukeCancel.disabled = true;

      // 核爆动画完成后，隐藏数据但不实际删除
      setTimeout(async () => {
        // 模拟清除（实际上不发生删除，只是UI层隐藏）
        stopNukeCountdown();
        state.items = [];
        state.total = 0;
        state.page = 1;
        renderEmptyTable("💥 核爆完成！所有记忆已被抹除。点击「刷新」重新加载。");
        updatePagination();

        // 更新统计信息显示为 0
        dom.stats.total.textContent = "0";
        dom.stats.active.textContent = "0";
        dom.stats.archived.textContent = "0";
        dom.stats.deleted.textContent = "0";
        showToast("💥 核爆完成！所有记忆已从界面移除");
      }, 4000); // 核爆动画时长
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
    const { method = "GET", body, skipAuth = false, retries = 2 } = options;
    let lastError;

    // 实现重试逻辑
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
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
          const message =
            (data && (data.detail || data.message || data.error)) || "请求失败";
          throw new Error(message);
        }

        return data;
      } catch (error) {
        lastError = error;

        // 如果是最后一次尝试或不应该重试的错误，直接抛出
        if (attempt === retries || error.message.includes("未登录") || error.message.includes("会话已过期")) {
          throw error;
        }

        // 等待一段时间后重试（指数退避）
        const waitTime = Math.min(1000 * Math.pow(2, attempt), 5000);
        await new Promise((resolve) => setTimeout(resolve, waitTime));
      }
    }

    throw lastError || new Error("请求失败");
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
  // 标签页切换功能（已移除，仅保留单一标签页）
  // ============================================

  // switchTab 函数已移除，不再需要

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
  // 调试工具功能（已移除，暂不实现）
  // ============================================

  // 相关函数已删除：triggerForgettingAgent, rebuildSparseIndex, loadSessionsInfo
  // runSearchTestFunc, runFusionCompareFunc, runMemoryAnalysisFunc

  document.addEventListener("DOMContentLoaded", init);
})();
