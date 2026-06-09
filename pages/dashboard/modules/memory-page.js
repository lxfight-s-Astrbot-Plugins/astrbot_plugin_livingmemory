/**
 * Memory Page - 记忆管理页面
 * 负责记忆列表展示、虚拟滚动、筛选和排序
 */

import { normalizeImportance, esc, statusPill, typeLabel, debounce } from "./utils.js";

export class MemoryPage {
  constructor(state, apiClient, peekPanel) {
    this.state = state;
    this.api = apiClient;
    this.peek = peekPanel;

    // 虚拟滚动配置
    this.ROW_HEIGHT = 56;
    this.SCROLL_BUFFER = 15;
  }

  /**
   * 获取记忆列表
   */
  async fetch() {
    const params = {
      page: String(this.state.memory.page),
      page_size: String(this.state.memory.pageSize)
    };

    if (this.state.memory.session) params.session_id = this.state.memory.session;
    if (this.state.memory.keyword) params.keyword = this.state.memory.keyword;
    if (this.state.memory.status && this.state.memory.status !== "all") {
      params.status = this.state.memory.status;
    }
    if (this.state.memory.type && this.state.memory.type !== "all") {
      params.type = this.state.memory.type;
    }
    if (this.state.memory.sort) {
      params.sort = this.state.memory.sort;
    }

    try {
      const data = await this.api.get("memories", params);

      this.state.memory.total = data.total || 0;
      this.state.memory.hasMore = data.has_more || false;

      this.state.memory.items = (Array.isArray(data.items) ? data.items : []).map(item => ({
        memory_id: item.id,
        doc_id: item.doc_id,
        summary: item.text || item.content || "",
        content: item.text || item.content,
        memory_type: (item.metadata && item.metadata.memory_type) || "GENERAL",
        importance: normalizeImportance(item.metadata && item.metadata.importance),
        status: (item.metadata && item.metadata.status) || "active",
        created_at: (item.metadata && item.metadata.create_time)
          ? new Date(item.metadata.create_time * 1000).toLocaleString()
          : item.created_at || "--",
        updated_at: (item.metadata && item.metadata.updated_at)
          ? new Date(item.metadata.updated_at * 1000).toLocaleString()
          : (item.metadata && item.metadata.create_time)
            ? new Date(item.metadata.create_time * 1000).toLocaleString()
            : item.updated_at || "--",
        last_access: (item.metadata && item.metadata.last_access_time)
          ? new Date(item.metadata.last_access_time * 1000).toLocaleString()
          : "--",
        raw: item,
      }));

      this.renderVirtual({ resetScroll: true });
      this.updatePagination();
    } catch (e) {
      this.showToast(e.message || window.t("misc.fetchMemoriesFail"), true);
      this.renderEmpty();
    }
  }

  /**
   * 虚拟滚动渲染
   * @param {Object} options - 渲染选项
   * @param {boolean} options.resetScroll - 是否重置滚动位置
   */
  renderVirtual(options = {}) {
    const tbody = document.getElementById("memories-body");
    const scrollEl = document.getElementById("memories-scroll");

    if (!this.state.memory.items.length) {
      this.renderEmpty();
      return;
    }

    const totalHeight = this.state.memory.items.length * this.ROW_HEIGHT;
    if (scrollEl && options.resetScroll) scrollEl.scrollTop = 0;

    const renderSlice = () => {
      const scrollTop = scrollEl ? scrollEl.scrollTop : 0;
      const viewHeight = scrollEl ? scrollEl.clientHeight : 600;
      const start = Math.max(0, Math.floor(scrollTop / this.ROW_HEIGHT) - this.SCROLL_BUFFER);
      const end = Math.min(
        this.state.memory.items.length,
        Math.ceil((scrollTop + viewHeight) / this.ROW_HEIGHT) + this.SCROLL_BUFFER
      );
      const padTop = start * this.ROW_HEIGHT;
      const padBottom = totalHeight - end * this.ROW_HEIGHT;

      let html = "";
      for (let i = start; i < end; i++) {
        const item = this.state.memory.items[i];
        const key = "m:" + item.memory_id;
        const imp = item.importance != null ? Number(item.importance).toFixed(1) : "5.0";
        const impNum = Math.min(10, Math.max(0, parseFloat(imp) || 0));
        const impCls = impNum >= 7 ? "high" : impNum >= 4 ? "medium" : "low";

        html += '<tr data-key="' + key + '" style="height:' + this.ROW_HEIGHT + 'px">';
        html += '<td class="cell-mono cell-id">' + item.memory_id + '</td>';
        html += '<td class="cell-summary"><div class="memory-summary-text">' + esc(item.summary || "") + '</div><div class="memory-summary-meta">' + esc(window.t("table.updated", item.updated_at || "--")) + '</div></td>';
        html += '<td class="cell-type"><span class="type-tag">' + esc(typeLabel(item.memory_type)) + '</span></td>';
        html += '<td class="cell-importance"><div class="importance-bar"><div class="importance-bar-track">';
        html += '<div class="importance-bar-fill ' + impCls + '" style="width:' + (impNum * 10) + '%"></div></div>';
        html += '<span style="font-size:12px;color:var(--text-secondary)">' + imp + '</span></div></td>';
        html += '<td class="cell-status">' + statusPill(item.status) + '</td>';
        html += '<td class="cell-created text-secondary" style="font-size:12px">' + esc(item.created_at) + '</td>';
        html += '</tr>';
      }

      tbody.innerHTML = html;
      tbody.style.paddingTop = padTop + "px";
      tbody.style.paddingBottom = padBottom + "px";
    };

    // 绑定滚动事件（仅绑定一次）
    if (scrollEl && !scrollEl._virtualScrollBound) {
      scrollEl._virtualScrollBound = true;
      scrollEl.addEventListener("scroll", () => {
        window.requestAnimationFrame(renderSlice);
      }, { passive: true });
    }

    renderSlice();
  }

  /**
   * 渲染空表格
   */
  renderEmpty() {
    const tbody = document.getElementById("memories-body");
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">' + window.t("table.noData") + '</td></tr>';
    tbody.style.paddingTop = "0";
    tbody.style.paddingBottom = "0";
  }

  /**
   * 根据 key 获取记忆项
   * @param {string} key - 记忆键（格式：m:id）
   * @returns {Object|undefined} 记忆对象
   */
  getItemByKey(key) {
    return this.state.memory.items.find(i => ("m:" + i.memory_id) === key);
  }

  /**
   * 更新分页信息
   */
  updatePagination() {
    const p = this.state.memory.page;
    const ps = this.state.memory.pageSize;
    const t = this.state.memory.total;
    const tp = Math.max(1, Math.ceil(t / ps));

    document.getElementById("mem-pagination-info").textContent = window.t("common.page", p, tp, t);
    document.getElementById("mem-prev").disabled = p <= 1;
    document.getElementById("mem-next").disabled = !this.state.memory.hasMore;
  }

  /**
   * 初始化事件监听
   */
  initEventListeners() {
    // 表格行点击事件
    const tbody = document.getElementById("memories-body");
    if (tbody) {
      tbody.addEventListener("click", (e) => {
        const tr = e.target.closest("tr");
        if (!tr || !tr.dataset.key) return;

        const item = this.getItemByKey(tr.dataset.key);
        if (item) this.peek.renderMemory(item);
      });
    }

    // 筛选：关键词
    document.getElementById("mem-keyword").addEventListener("input", debounce(() => {
      this.state.memory.keyword = document.getElementById("mem-keyword").value.trim();
      this.state.memory.page = 1;
      this.fetch();
    }, 300));

    // 筛选：会话 ID
    document.getElementById("mem-session").addEventListener("input", debounce(() => {
      this.state.memory.session = document.getElementById("mem-session").value.trim();
      this.state.memory.page = 1;
      this.fetch();
    }, 300));

    // 筛选：状态
    document.getElementById("mem-status").addEventListener("change", () => {
      this.state.memory.status = document.getElementById("mem-status").value;
      this.state.memory.page = 1;
      this.fetch();
    });

    // 筛选：类型
    document.getElementById("mem-type").addEventListener("change", () => {
      this.state.memory.type = document.getElementById("mem-type").value;
      this.state.memory.page = 1;
      this.fetch();
    });

    // 排序
    document.getElementById("mem-sort").addEventListener("change", () => {
      this.state.memory.sort = document.getElementById("mem-sort").value;
      this.state.memory.page = 1;
      this.fetch();
    });

    // 筛选：每页数量
    document.getElementById("mem-page-size").addEventListener("change", () => {
      this.state.memory.pageSize = parseInt(document.getElementById("mem-page-size").value) || 20;
      this.state.memory.page = 1;
      this.fetch();
    });

    // 分页：上一页
    document.getElementById("mem-prev").addEventListener("click", () => {
      if (this.state.memory.page > 1) {
        this.state.memory.page--;
        this.fetch();
      }
    });

    // 分页：下一页
    document.getElementById("mem-next").addEventListener("click", () => {
      if (this.state.memory.hasMore) {
        this.state.memory.page++;
        this.fetch();
      }
    });
  }

  /**
   * 显示 Toast 提示
   * @param {string} message - 提示消息
   * @param {boolean} isError - 是否为错误
   */
  showToast(message, isError = false) {
    if (window.lmShowToast) {
      window.lmShowToast(message, isError);
    }
  }
}
