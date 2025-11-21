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

  // 简单的日志记录器
  const logger = {
    error: (...args) => console.error(...args),
    warn: (...args) => console.warn(...args),
    info: (...args) => console.info(...args),
    debug: (...args) => console.debug(...args),
  };

  const dom = {
    loginView: document.getElementById("login-view"),
    dashboardView: document.getElementById("dashboard-view"),
    loginForm: document.getElementById("login-form"),
    loginError: document.getElementById("login-error"),
    passwordInput: document.getElementById("password-input"),
    refreshButton: document.getElementById("refresh-button"),
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
    // 初始化主题
    initTheme();
    
    dom.loginForm.addEventListener("submit", onLoginSubmit);
    dom.refreshButton.addEventListener("click", fetchAll);
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

    // 标签页切换
    const tabButtons = document.querySelectorAll(".tab-btn");
    tabButtons.forEach((btn) => {
      btn.addEventListener("click", onTabClick);
    });

    // 召回测试功能
    const recallSearchBtn = document.getElementById("recall-search-btn");
    const recallClearBtn = document.getElementById("recall-clear-btn");
    if (recallSearchBtn) {
      recallSearchBtn.addEventListener("click", performRecallTest);
    }
    if (recallClearBtn) {
      recallClearBtn.addEventListener("click", clearRecallResults);
    }

    // 主题切换按钮
    const themeToggle = document.getElementById("theme-toggle");
    const loginThemeToggle = document.getElementById("login-theme-toggle");
    if (themeToggle) {
      themeToggle.addEventListener("click", toggleTheme);
    }
    if (loginThemeToggle) {
      loginThemeToggle.addEventListener("click", toggleTheme);
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

  // 存储所有加载的记忆数据
  let allMemories = [];

  async function fetchMemories() {
    const params = new URLSearchParams();
    
    // 根据loadAll状态决定请求参数
    if (state.loadAll) {
      // 加载全部模式：请求所有数据
      params.set("page", "1");
      params.set("page_size", "999999"); // 大数值以获取所有数据
    } else {
      // 正常分页模式：只加载当前页数据
      params.set("page", state.page.toString());
      params.set("page_size", state.pageSize.toString());
    }
    
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
      
      // 更新总数和分页状态
      state.total = data.total || 0;
      
      // 在loadAll模式下，设置hasMore为false
      if (state.loadAll) {
        state.hasMore = false;
      } else {
        state.hasMore = data.has_more || false;
      }
      
      // 转换API返回的数据格式以匹配前端期望
      state.items = rawItems.map((item) => {
        // 确保使用正确的ID字段
        const actualId = item.id !== undefined ? item.id : (item.doc_id || item.memory_id);
        
        return {
          memory_id: actualId,  // 使用实际的整数ID
          doc_id: actualId,     // 使用实际的整数ID
          uuid: item.doc_id,    // 保存UUID以供显示（如果存在）
          summary: item.text || item.content || "（无内容）",
          content: item.text || item.content,
          memory_type: item.metadata?.memory_type || item.metadata?.type || "GENERAL",
          importance: item.metadata?.importance ?? 5.0,
          status: item.metadata?.status || "active",
          created_at: item.metadata?.create_time ? new Date(item.metadata.create_time * 1000).toLocaleString() : "--",
          last_access: item.metadata?.last_access_time ? new Date(item.metadata.last_access_time * 1000).toLocaleString() : "--",
          source: "storage",
          raw: item,
          raw_json: JSON.stringify(item, null, 2),
        };
      });
      
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

  // 客户端过滤和分页
  function applyClientSideFilters() {
    let filtered = [...allMemories];

    // 应用关键词过滤
    if (state.filters.keyword) {
      const keyword = state.filters.keyword.toLowerCase();
      filtered = filtered.filter((item) =>
        item.summary?.toLowerCase().includes(keyword) ||
        item.memory_id?.toString().includes(keyword)
      );
    }

    // 应用状态过滤
    if (state.filters.status && state.filters.status !== "all") {
      filtered = filtered.filter((item) =>
        item.status === state.filters.status
      );
    }

    // 更新总数
    state.total = filtered.length;

    // 应用分页（除非"加载全部"模式）
    if (state.loadAll) {
      state.items = filtered;
      state.hasMore = false;
    } else {
      const startIndex = (state.page - 1) * state.pageSize;
      const endIndex = startIndex + state.pageSize;
      state.items = filtered.slice(startIndex, endIndex);
      state.hasMore = endIndex < filtered.length;
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
    if (state.filters.keyword || state.filters.status !== "all") {
      showToast(`搜索结果：找到 ${state.total} 条记忆，当前显示第 ${state.items.length} 条`);
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
    
    // 服务端分页：重新请求数据
    fetchMemories();
  }

  function onPageSizeChange() {
    state.pageSize = Number(dom.pageSize.value) || 20;
    state.page = 1;
    
    // 服务端分页：重新请求数据
    fetchMemories();
  }

  function goPrevPage() {
    if (state.page > 1) {
      state.page -= 1;
      // 服务端分页：重新请求数据
      fetchMemories();
    }
  }

  function goNextPage() {
    if (state.hasMore) {
      state.page += 1;
      // 服务端分页：重新请求数据
      fetchMemories();
    }
  }

  function updatePagination() {
    if (state.loadAll) {
      dom.paginationInfo.textContent = `共 ${state.items.length} 条记录（已加载全部）`;
      // 加载全部模式：禁用翻页按钮
      dom.prevPage.disabled = true;
      dom.nextPage.disabled = true;
    } else {
      const totalPages = state.total
        ? Math.max(1, Math.ceil(state.total / state.pageSize))
        : 1;
      dom.paginationInfo.textContent = `第 ${state.page} / ${totalPages} 页 · 共 ${state.total} 条`;
      
      // 正常分页模式：根据实际情况启用/禁用翻页按钮
      dom.prevPage.disabled = state.page <= 1;
      dom.nextPage.disabled = !state.hasMore;
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
  }

  async function deleteSelectedMemories() {
    if (state.selected.size === 0) {
      return;
    }
    const count = state.selected.size;

    // 改进的确认对话框
    const confirmed = window.confirm(
      `️  确认删除？\n\n` +
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
        // 使用整数ID（而非UUID）
        const id = item.memory_id || item.doc_id;
        if (id !== null && id !== undefined) {
          // 确保是整数
          const intId = parseInt(id, 10);
          if (!isNaN(intId)) {
            memoryIds.push(intId);
          } else {
            console.error(`[删除] 无效的memory_id: ${id}, item:`, item);
          }
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
        //  全部失败
        showToast(
          ` 删除失败：全部 ${failedCount} 条记忆无法删除\n` +
          `失败ID: ${failedIds.join(", ")}\n` +
          `请检查日志了解详情`,
          true
        );
        logger.error("删除失败 - 所有记忆都无法删除", { failedIds });
      } else if (failedCount > 0) {
        // ️ 部分失败
        showToast(
          `️ 部分删除失败：成功 ${deletedCount} 条，失败 ${failedCount} 条\n` +
          `失败ID: ${failedIds.join(", ")}`
        );
        logger.warn("部分删除失败", { deletedCount, failedCount, failedIds });
      } else if (deletedCount > 0) {
        //  全部成功
        showToast(` 已成功删除 ${deletedCount} 条记忆`);
      } else {
        // ️ 没有删除任何记忆
        showToast("️ 没有删除任何记忆", true);
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
      // 直接触发核爆倒计时，不需要确认
      startNukeCountdown({
        seconds_left: 10,  // 缩短到10秒，更刺激
        operation_id: "nuke_" + Date.now(),
      });
      showToast("💥 核爆倒计时启动！");
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
      showToast(" 核爆已取消！记忆保留");
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

      // 核爆动画完成后，清空视觉效果
      setTimeout(async () => {
        // 停止核爆倒计时
        stopNukeCountdown();
        
        // 清空所有数据的视觉显示
        state.items = [];
        state.total = 0;
        state.page = 1;
        state.selected.clear();
        
        // 更新表格显示
        renderEmptyTable(" 核爆完成！所有记忆已被抹除。点击「刷新」重新加载。");
        updatePagination();

        // 清空统计信息显示
        dom.stats.total.textContent = "0";
        dom.stats.active.textContent = "0";
        dom.stats.archived.textContent = "0";
        dom.stats.deleted.textContent = "0";
        dom.stats.sessions.textContent = "0";
        
        // 重置选择状态
        dom.selectAll.checked = false;
        dom.deleteSelected.disabled = true;
        
        showToast(" 核爆完成！所有记忆已从界面移除（仅视觉效果）");
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
   * 触发完整的核爆视觉效果序列 - 粒子系统
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

    // 3. 创建核心爆炸粒子
    setTimeout(() => {
      createExplosionParticles();
    }, 100);

    // 4. 创建火焰粒子
    setTimeout(() => {
      createFireParticles();
    }, 200);

    // 5. 创建冲击波粒子
    setTimeout(() => {
      createShockwaveParticles();
    }, 400);

    // 6. 数据表格粒子化消失
    if (tableBody) {
      const rows = tableBody.querySelectorAll("tr");
      rows.forEach((row, index) => {
        setTimeout(() => {
          row.classList.add("particle-fade");
        }, index * 50);
      });
    }

    // 7. 添加数据撕裂效果到所有卡片
    const cards = document.querySelectorAll(".card");
    setTimeout(() => {
      cards.forEach((card) => {
        card.classList.add("data-glitch");
      });
    }, 800);

    // 8. 生成灰烬飘落粒子
    setTimeout(() => {
      createAshParticles();
    }, 1500);

    // 9. 停止所有动画效果
    setTimeout(() => {
      app.classList.remove("screen-shake");
      cards.forEach((card) => {
        card.classList.remove("data-glitch");
      });
    }, 3000);

    // 10. 移除核爆遮罩层，添加界面恢复动画
    setTimeout(() => {
      overlay.classList.remove("active");
      app.classList.add("fade-in-recovery");

      // 清理粒子容器
      const container = document.getElementById("nuke-particles-container");
      if (container) {
        container.innerHTML = "";
      }

      // 移除恢复动画类
      setTimeout(() => {
        app.classList.remove("fade-in-recovery");
      }, 1500);
    }, 3500);
  }

  /**
   * 创建核心爆炸粒子 - 快速向外扩散的白色粒子
   */
  function createExplosionParticles() {
    const container = document.getElementById("nuke-particles-container");
    if (!container) return;

    const particleCount = 200; // 爆炸粒子数量
    const centerX = window.innerWidth / 2;
    const centerY = window.innerHeight / 2;

    for (let i = 0; i < particleCount; i++) {
      const particle = document.createElement("div");
      particle.className = "explosion-particle";

      // 随机角度和速度
      const angle = (Math.PI * 2 * i) / particleCount;
      const speed = 100 + Math.random() * 400; // 100-500px
      const size = 2 + Math.random() * 6; // 2-8px

      const endX = Math.cos(angle) * speed;
      const endY = Math.sin(angle) * speed;

      particle.style.left = `${centerX}px`;
      particle.style.top = `${centerY}px`;
      particle.style.width = `${size}px`;
      particle.style.height = `${size}px`;
      particle.style.setProperty("--end-x", `${endX}px`);
      particle.style.setProperty("--end-y", `${endY}px`);

      // 随机延迟
      particle.style.animationDelay = `${Math.random() * 0.1}s`;

      container.appendChild(particle);

      // 动画结束后移除
      setTimeout(() => {
        particle.remove();
      }, 1500);
    }
  }

  /**
   * 创建火焰粒子 - 橙红色向外扩散
   */
  function createFireParticles() {
    const container = document.getElementById("nuke-particles-container");
    if (!container) return;

    const particleCount = 150;
    const centerX = window.innerWidth / 2;
    const centerY = window.innerHeight / 2;

    for (let i = 0; i < particleCount; i++) {
      const particle = document.createElement("div");
      particle.className = "fire-particle";

      const angle = Math.random() * Math.PI * 2;
      const speed = 80 + Math.random() * 300;
      const size = 3 + Math.random() * 8;

      const endX = Math.cos(angle) * speed;
      const endY = Math.sin(angle) * speed;

      particle.style.left = `${centerX}px`;
      particle.style.top = `${centerY}px`;
      particle.style.width = `${size}px`;
      particle.style.height = `${size}px`;
      particle.style.setProperty("--end-x", `${endX}px`);
      particle.style.setProperty("--end-y", `${endY}px`);
      particle.style.animationDelay = `${Math.random() * 0.2}s`;

      container.appendChild(particle);

      setTimeout(() => {
        particle.remove();
      }, 2000);
    }
  }

  /**
   * 创建冲击波粒子 - 环形扩散的小粒子
   */
  function createShockwaveParticles() {
    const container = document.getElementById("nuke-particles-container");
    if (!container) return;

    const waves = 5; // 5层冲击波
    const particlesPerWave = 80;

    for (let wave = 0; wave < waves; wave++) {
      setTimeout(() => {
        const centerX = window.innerWidth / 2;
        const centerY = window.innerHeight / 2;

        for (let i = 0; i < particlesPerWave; i++) {
          const particle = document.createElement("div");
          particle.className = "shockwave-particle";

          const angle = (Math.PI * 2 * i) / particlesPerWave;
          const speed = 200 + wave * 100 + Math.random() * 100;
          const size = 2 + Math.random() * 4;

          const endX = Math.cos(angle) * speed;
          const endY = Math.sin(angle) * speed;

          particle.style.left = `${centerX}px`;
          particle.style.top = `${centerY}px`;
          particle.style.width = `${size}px`;
          particle.style.height = `${size}px`;
          particle.style.setProperty("--end-x", `${endX}px`);
          particle.style.setProperty("--end-y", `${endY}px`);

          container.appendChild(particle);

          setTimeout(() => {
            particle.remove();
          }, 1500);
        }
      }, wave * 150);
    }
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
    
    // 调试：输出将要使用的ID
    console.log('[编辑] 准备更新记忆:', {
      memory_id: memoryId,
      field: field,
      value: value,
      currentItem: state.currentMemoryItem
    });

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

  // ============================================
  // 标签页切换功能
  // ============================================

  function onTabClick(event) {
    const tabName = event.target.dataset.tab;
    if (!tabName) return;

    // 更新状态
    state.currentTab = tabName;

    // 更新标签页按钮状态
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    // 更新内容显示
    document.querySelectorAll(".tab-content").forEach((content) => {
      content.classList.toggle("active", content.dataset.tab === tabName);
    });
  }

  // ============================================
  // 召回测试功能
  // ============================================

  async function performRecallTest() {
    const query = document.getElementById("recall-query").value.trim();
    const k = parseInt(document.getElementById("recall-k").value) || 5;
    const session_id = document.getElementById("recall-session-id").value.trim() || null;

    if (!query) {
      showToast("请输入查询内容", true);
      return;
    }

    const recallSearchBtn = document.getElementById("recall-search-btn");
    recallSearchBtn.disabled = true;
    recallSearchBtn.textContent = "执行中...";

    try {
      const payload = {
        query,
        k,
      };

      if (session_id) {
        payload.session_id = session_id;
      }

      const startTime = performance.now();
      const response = await apiRequest("/api/recall/test", {
        method: "POST",
        body: payload,
      });

      if (!response.success) {
        throw new Error(response.error || "召回失败");
      }

      const data = response.data;
      displayRecallResults(data);
      showToast(`成功召回 ${data.total} 条记忆`);
    } catch (error) {
      logger.error("[召回测试失败]", error.message);
      showToast(error.message || "召回失败", true);
    } finally {
      recallSearchBtn.disabled = false;
      recallSearchBtn.textContent = "执行召回";
    }
  }

  function displayRecallResults(data) {
    const resultsContainer = document.getElementById("recall-results");
    const statsContainer = document.getElementById("recall-stats");

    // 更新统计信息
    if (data.total > 0) {
      document.getElementById("recall-count").textContent = data.total;
      document.getElementById("recall-time").textContent = `${data.elapsed_time_ms.toFixed(2)}ms`;
      statsContainer.classList.remove("hidden");
    } else {
      statsContainer.classList.add("hidden");
    }

    // 清空结果容器
    resultsContainer.innerHTML = "";

    if (data.total === 0) {
      resultsContainer.innerHTML =
        '<div class="empty-state"><p>未找到匹配的记忆</p></div>';
      return;
    }

    // 构建结果卡片
    const resultsHTML = data.results
      .map((result, index) => {
        const scorePercentage = result.score_percentage;
        const scoreColor = getScoreColor(scorePercentage);

        return `
          <div class="recall-result-card">
            <div class="result-header">
              <h4>结果 #${index + 1}</h4>
              <span class="score-badge ${scoreColor}">
                ${scorePercentage.toFixed(1)}%
              </span>
            </div>

            <div class="result-content">
              <p class="content-text">${escapeHTML(result.content)}</p>
            </div>

            <div class="result-metadata">
              <div class="meta-item">
                <span class="meta-label">记忆 ID:</span>
                <span class="meta-value mono">${result.memory_id}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">相似度得分:</span>
                <span class="meta-value">${result.similarity_score}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">会话 UUID:</span>
                <span class="meta-value mono">${result.metadata.session_id || "--"}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">重要性:</span>
                <span class="meta-value">${(result.metadata.importance * 10).toFixed(1)}/10</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">类型:</span>
                <span class="meta-value">${result.metadata.memory_type}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">状态:</span>
                <span class="meta-value">${formatStatus(result.metadata.status)}</span>
              </div>
            </div>
          </div>
        `;
      })
      .join("");

    resultsContainer.innerHTML = resultsHTML;
  }

  function getScoreColor(percentage) {
    if (percentage >= 80) return "score-high";
    if (percentage >= 60) return "score-medium";
    if (percentage >= 40) return "score-low";
    return "score-very-low";
  }

  function clearRecallResults() {
    document.getElementById("recall-query").value = "";
    document.getElementById("recall-session-id").value = "";
    document.getElementById("recall-results").innerHTML =
      '<div class="empty-state"><p>暂无召回结果 · 请输入查询内容并执行召回</p></div>';
    document.getElementById("recall-stats").classList.add("hidden");
  }

  // ============================================
  // 主题切换功能
  // ============================================

  function initTheme() {
    // 从 localStorage 读取主题设置，默认为浅色
    const savedTheme = localStorage.getItem("lmem_theme") || "light";
    applyTheme(savedTheme);
  }

  function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute("data-theme") || "light";
    const newTheme = currentTheme === "light" ? "dark" : "light";
    applyTheme(newTheme);
    localStorage.setItem("lmem_theme", newTheme);
    showToast(newTheme === "dark" ? "🌙 已切换到深色模式" : "☀️ 已切换到浅色模式");
  }

  function applyTheme(theme) {
    // 添加过渡类以实现平滑切换
    document.documentElement.classList.add("theme-transitioning");
    
    // 设置主题属性
    document.documentElement.setAttribute("data-theme", theme);
    
    // 更新图标
    updateThemeIcons(theme);
    
    // 移除过渡类
    setTimeout(() => {
      document.documentElement.classList.remove("theme-transitioning");
    }, 300);
  }

  function updateThemeIcons(theme) {
    const themeIcon = document.getElementById("theme-icon");
    const loginThemeIcon = document.getElementById("login-theme-icon");
    
    if (themeIcon) {
      themeIcon.setAttribute("data-lucide", theme === "dark" ? "sun" : "moon");
    }
    if (loginThemeIcon) {
      loginThemeIcon.setAttribute("data-lucide", theme === "dark" ? "sun" : "moon");
    }
    
    // 重新初始化图标
    if (typeof lucide !== "undefined" && lucide.createIcons) {
      lucide.createIcons();
    }
  }

  // ============================================
  // 调试工具功能（已移除，暂不实现）
  // ============================================

  // 相关函数已删除：triggerForgettingAgent, rebuildSparseIndex, loadSessionsInfo
  // runSearchTestFunc, runFusionCompareFunc, runMemoryAnalysisFunc

  document.addEventListener("DOMContentLoaded", init);
})();
