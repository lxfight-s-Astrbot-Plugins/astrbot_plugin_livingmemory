/**
 * Memory Page - 记忆管理页面
 * 负责记忆列表展示、虚拟滚动、筛选和排序
 */

import { normalizeImportance, esc, statusPill, typeLabel } from "./utils.js";

export class MemoryPage {
  constructor(state, apiClient, peekPanel) {
    this.state = state;
    this.api = apiClient;
    this.peek = peekPanel;
    if (!(this.state.memory.selectedIds instanceof Set)) {
      this.state.memory.selectedIds = new Set();
    }

    // 虚拟滚动配置
    this.ROW_HEIGHT = 56;
    this.SCROLL_BUFFER = 15;
    this._fetchGeneration = 0;
    this._scrollFramePending = false;
  }

  /**
   * 获取记忆列表
   */
  async fetch() {
    clearTimeout(this._filterTimer);
    const fetchGeneration = ++this._fetchGeneration;
    this.state.memory.loading = true;
    this.state.memory.error = "";
    this.updateFeedback();
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
      if (fetchGeneration !== this._fetchGeneration) return;

      // 删除/筛选后当前页可能超出总页数，回退到最后一页重取，避免显示假空表
      const total = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(total / this.state.memory.pageSize));
      if (this.state.memory.page > totalPages && !(data.items || []).length) {
        this.state.memory.page = totalPages;
        return this.fetch();
      }

      this.state.memory.total = total;
      this.state.memory.hasMore = data.has_more || false;

      this.state.memory.items = (Array.isArray(data.items) ? data.items : []).map(item => ({
        memory_id: item.id,
        doc_id: item.doc_id,
        summary:
          (item.metadata && item.metadata.persona_summary) ||
          item.summary ||
          item.text ||
          item.content ||
          "",
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
        consolidated_count: (item.metadata && Array.isArray(item.metadata.consolidated_from))
          ? item.metadata.consolidated_from.length
          : 0,
        raw: item,
      }));
      this.state.memory.selectedIds.clear();
      this._visibleSlice = null;

      this.renderVirtual({ resetScroll: true });
      this.updatePagination();
    } catch (e) {
      if (fetchGeneration !== this._fetchGeneration) return;
      this.state.memory.error = e.message || window.t("misc.fetchMemoriesFail");
      this.showToast(e.message || window.t("misc.fetchMemoriesFail"), true);
    } finally {
      if (fetchGeneration === this._fetchGeneration) {
        this.state.memory.loading = false;
        this.updateFeedback();
      }
    }
  }

  updateFeedback() {
    const doc = globalThis.document;
    if (!doc) return;
    const { loading, error } = this.state.memory;
    const feedback = doc.getElementById("memory-feedback");
    const message = doc.getElementById("memory-feedback-message");
    const retry = doc.getElementById("memory-retry");
    const scroll = doc.getElementById("memories-scroll");
    if (feedback) {
      feedback.hidden = !loading && !error;
      feedback.classList.toggle("is-error", Boolean(error));
    }
    if (message) message.textContent = loading ? window.t("common.loading") : error;
    if (retry) retry.hidden = !error || loading;
    if (scroll) {
      scroll.setAttribute("aria-busy", String(Boolean(loading)));
      // Keep the last successful view visible, but disable actions on stale data.
      scroll.inert = Boolean(loading || error || this._bulkBusy || this._transferBusy);
    }
    this.updateSelectionControls();
    this.updatePagination();
  }

  /**
   * 虚拟滚动渲染
   * @param {Object} options - 渲染选项
   * @param {boolean} options.resetScroll - 是否重置滚动位置
   */
  renderVirtual(options = {}) {
    this._visibleSlice = null;
    const scrollEl = document.getElementById("memories-scroll");
    if (scrollEl && options.resetScroll) scrollEl.scrollTop = 0;

    if (!this.state.memory.items.length) {
      this.renderEmpty();
      return;
    }

    // 绑定滚动事件（仅绑定一次）
    if (scrollEl && !scrollEl._virtualScrollBound) {
      scrollEl._virtualScrollBound = true;
      scrollEl.addEventListener("scroll", () => {
        if (this._scrollFramePending) return;
        this._scrollFramePending = true;
        window.requestAnimationFrame(() => {
          this._scrollFramePending = false;
          this.renderVirtualSlice();
        });
      }, { passive: true });
      if (typeof ResizeObserver !== "undefined") {
        this._resizeObserver = new ResizeObserver(() => {
          if (!scrollEl.clientWidth) return;
          this._measuredRowHeight = null;
          this._visibleSlice = null;
          this.renderVirtualSlice();
        });
        this._resizeObserver.observe(scrollEl);
      }
    }

    this.renderVirtualSlice();
  }

  renderVirtualSlice() {
    const tbody = document.getElementById("memories-body");
    const scrollEl = document.getElementById("memories-scroll");
    if (!this.state.memory.items.length) {
      this.renderEmpty();
      return;
    }

    // 行高用实测值：CSS 的 td height 只是最小值，双行摘要单元格实际 ~63px，
    // 硬编码 56px 会让 spacer 逐行漂移、末尾行不可达
    const rowHeight = this._measuredRowHeight || this.ROW_HEIGHT;
    const totalHeight = this.state.memory.items.length * rowHeight;
    const scrollTop = scrollEl ? scrollEl.scrollTop : 0;
    const viewHeight = scrollEl ? scrollEl.clientHeight : 600;
    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - this.SCROLL_BUFFER);
    const end = Math.min(
      this.state.memory.items.length,
      Math.ceil((scrollTop + viewHeight) / rowHeight) + this.SCROLL_BUFFER
    );
    const padTop = start * rowHeight;
    const padBottom = totalHeight - end * rowHeight;
    const columns = window.matchMedia?.("(max-width: 768px)").matches ? 4 : 7;
    const slice = `${start}:${end}:${rowHeight}`;
    if (slice === this._visibleSlice) return;
    this._visibleSlice = slice;
    const spacerRow = (height) => height > 0
      ? '<tr class="virtual-spacer" aria-hidden="true" style="height:' + height + 'px"><td colspan="' + columns + '" style="height:' + height + 'px;padding:0;border:0"></td></tr>'
      : "";

    let html = spacerRow(padTop);
    for (let i = start; i < end; i++) {
      const item = this.state.memory.items[i];
      const key = "m:" + item.memory_id;
      const imp = item.importance != null ? Number(item.importance).toFixed(1) : "5.0";
      const impNum = Math.min(10, Math.max(0, parseFloat(imp) || 0));
      const impCls = impNum >= 7 ? "high" : impNum >= 4 ? "medium" : "low";

      const selected = this.state.memory.selectedIds.has(item.memory_id);
      html += '<tr data-key="' + key + '" class="' + (selected ? 'is-selected' : '') + '" style="height:' + this.ROW_HEIGHT + 'px">';
      html += '<td class="cell-select"><input type="checkbox" class="memory-select" data-memory-id="' + item.memory_id + '" ' + (selected ? 'checked' : '') + ' aria-label="' + esc(window.t("delete.selectOne", item.memory_id)) + '" /></td>';
      html += '<td class="cell-mono cell-id">' + item.memory_id + '</td>';
      const consBadge = item.consolidated_count > 0
        ? '<span class="type-tag cons-badge" title="' + esc(window.t("table.consolidatedTitle")) + '">' + window.t("table.consolidated", item.consolidated_count) + '</span> '
        : "";
      html += '<td class="cell-summary">' + consBadge + '<button class="memory-open memory-summary-text" type="button" aria-label="' + esc(window.t("table.openMemory", item.memory_id)) + '">' + esc(item.summary || "") + '</button><div class="memory-summary-meta">' + esc(window.t("table.updated", item.updated_at || "--")) + '</div></td>';
      html += '<td class="cell-type"><span class="type-tag">' + esc(typeLabel(item.memory_type)) + '</span></td>';
      html += '<td class="cell-importance"><div class="importance-bar"><div class="importance-bar-track">';
      html += '<div class="importance-bar-fill ' + impCls + '" style="width:' + (impNum * 10) + '%"></div></div>';
      html += '<span style="font-size:12px;color:var(--text-secondary)">' + imp + '</span></div></td>';
      html += '<td class="cell-status">' + statusPill(item.status) + '</td>';
      html += '<td class="cell-created text-secondary" style="font-size:12px">' + esc(item.created_at) + '</td>';
      html += '</tr>';
    }

    tbody.innerHTML = html + spacerRow(padBottom);
    tbody.style.paddingTop = "0";
    tbody.style.paddingBottom = "0";
    this._measureRowHeight(tbody, rowHeight);
    this.updateSelectionControls();
  }

  /**
   * 首个可见行渲染后实测行高，与假设不符时重算一次虚拟窗口
   * @param {HTMLElement} tbody - 表格主体
   * @param {number} assumed - 本次渲染假设的行高
   */
  _measureRowHeight(tbody, assumed) {
    if (this._remeasuring) return;
    if (!tbody || typeof tbody.querySelector !== "function") return;
    const row = tbody.querySelector("tr[data-key]");
    if (!row) return;
    const h = row.offsetHeight;
    if (h > 0 && Math.abs(h - assumed) > 0.5) {
      this._measuredRowHeight = h;
      this._remeasuring = true;
      try {
        this.renderVirtualSlice();
      } finally {
        this._remeasuring = false;
      }
    }
  }

  /**
   * 渲染空表格
   */
  renderEmpty() {
    const tbody = document.getElementById("memories-body");
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty">' + window.t("table.noData") + '</td></tr>';
    tbody.style.paddingTop = "0";
    tbody.style.paddingBottom = "0";
    this.updateSelectionControls();
  }

  updateSelectionControls() {
    const unavailable = Boolean(this.state.memory.loading || this.state.memory.error || this._bulkBusy || this._transferBusy);
    const selectedIds = this.state.memory.selectedIds;
    const pageIds = this.state.memory.items.map(item => item.memory_id);
    const selectedOnPage = pageIds.filter(id => selectedIds.has(id)).length;
    const selectAll = document.getElementById("mem-select-all");
    if (selectAll) {
      selectAll.checked = pageIds.length > 0 && selectedOnPage === pageIds.length;
      selectAll.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
      selectAll.disabled = unavailable || pageIds.length === 0;
    }

    const deleteButton = document.getElementById("mem-delete-selected");
    if (deleteButton) deleteButton.disabled = unavailable || selectedIds.size === 0;
    const deleteLabel = document.getElementById("mem-delete-selected-label");
    if (deleteLabel) deleteLabel.textContent = window.t("delete.selected", selectedIds.size);

    const selection = document.getElementById("memory-selection");
    if (selection) selection.hidden = selectedIds.size === 0;
    const count = document.getElementById("mem-selection-count");
    if (count) count.textContent = window.t("flow.selectedOnPage", selectedIds.size);
    const clear = document.getElementById("mem-clear-selection");
    if (clear) clear.disabled = unavailable;
    const scope = document.getElementById("mem-export-scope");
    if (scope) scope.textContent = window.t(selectedIds.size ? "flow.exportSelected" : "flow.exportAll", selectedIds.size);
    const exportButton = document.getElementById("mem-export");
    if (exportButton) exportButton.disabled = unavailable || Boolean(this._transferBusy);
    const importButton = document.getElementById("mem-import");
    if (importButton) importButton.disabled = Boolean(this._transferBusy || this._bulkBusy);
    const filters = document.getElementById("memory-filters");
    if (filters) filters.inert = Boolean(this._transferBusy || this._bulkBusy);
    const refresh = document.getElementById("mem-refresh");
    if (refresh) refresh.disabled = unavailable || Boolean(this._transferBusy);
    for (const id of ["mem-transfer-format", "mem-import-duplicates", "mem-page-size"]) {
      const input = document.getElementById(id);
      if (input) input.disabled = Boolean(this._bulkBusy || this._transferBusy);
    }

    const batchEditButton = document.getElementById("mem-batch-edit");
    if (batchEditButton) batchEditButton.disabled = unavailable || selectedIds.size === 0;
    const batchEditLabel = document.getElementById("mem-batch-edit-label");
    if (batchEditLabel) batchEditLabel.textContent = window.t("batchEdit.button", selectedIds.size);
  }

  toggleAllOnPage(checked) {
    for (const item of this.state.memory.items) {
      if (checked) this.state.memory.selectedIds.add(item.memory_id);
      else this.state.memory.selectedIds.delete(item.memory_id);
    }
    this.renderVirtual();
  }

  async deleteSelected() {
    if (this._bulkBusy || this._transferBusy || this.state.memory.loading || this.state.memory.error) return;
    const ids = Array.from(this.state.memory.selectedIds);
    if (!ids.length) return;

    this._bulkBusy = true;
    this.updateFeedback();
    try {
      this.peek.open();
      const confirmed = await this.peek.showConfirmDialog(
        window.t("delete.confirmTitle"),
        window.t("delete.confirmMsg", ids.length)
      );
      if (!confirmed) {
        this.peek.close();
        return;
      }

      const button = document.getElementById("mem-delete-selected");
      if (button) button.disabled = true;
      const result = await this.api.post(
        "memories/batch-delete",
        { memory_ids: ids },
        { retries: 0 }
      );
      const deleted = Number(result.deleted_count || 0);
      if (deleted !== ids.length) {
        const failed = ids.length - deleted;
        this.showToast(window.t("delete.partialFailed", deleted, failed, "--"), true);
      } else {
        this.showToast(window.t("delete.success", deleted));
      }
      this.state.memory.selectedIds.clear();
      this.peek.close();
      await this.fetch();
    } catch (error) {
      this.peek.close();
      this.showToast(error.message || window.t("delete.error"), true);
      this.updateSelectionControls();
    } finally {
      this._bulkBusy = false;
      this.updateFeedback();
    }
  }

  async batchEdit() {
    if (this._bulkBusy || this._transferBusy || this.state.memory.loading || this.state.memory.error) return;
    const ids = Array.from(this.state.memory.selectedIds);
    if (!ids.length) return;

    this._bulkBusy = true;
    this.updateFeedback();
    try {
      this.peek.open();
      const edit = await this.peek.showBatchEditDialog(ids.length);
      if (!edit) {
        this.peek.close();
        return;
      }

      const button = document.getElementById("mem-batch-edit");
      if (button) button.disabled = true;
      const payload = { memory_ids: ids, field: edit.field, value: edit.value };
      if (edit.value_scale) payload.value_scale = edit.value_scale;
      const result = await this.api.post(
        "memories/batch-update",
        payload,
        { retries: 0 }
      );
      const updated = Number(result.updated_count || 0);
      const failed = Number(result.failed_count || 0);
      if (failed > 0) {
        this.showToast(window.t("batchEdit.partialFailed", updated, failed), true);
      } else {
        this.showToast(window.t("batchEdit.success", updated));
      }
      this.state.memory.selectedIds.clear();
      this.peek.close();
      await this.fetch();
    } catch (error) {
      this.peek.close();
      this.showToast(error.message || window.t("batchEdit.error"), true);
      this.updateSelectionControls();
    } finally {
      this._bulkBusy = false;
      this.updateFeedback();
    }
  }

  async exportMemories() {
    if (this._transferBusy || this._bulkBusy || this.state.memory.loading || this.state.memory.error) return;
    this._transferBusy = true;
    this.updateFeedback();
    this.setTransferStatus(window.t("flow.exporting"));
    const button = document.getElementById("mem-export");
    const format = document.getElementById("mem-transfer-format").value || "json";
    const selectedIds = Array.from(this.state.memory.selectedIds);
    if (button) button.disabled = true;
    try {
      const payload = { format };
      if (selectedIds.length) payload.memory_ids = selectedIds;
      const result = await this.api.post("memories/export", payload, { retries: 0 });
      const blob = new Blob([result.content || ""], {
        type: result.mime_type || "application/octet-stream"
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename || ("livingmemory-export." + format);
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      this.setTransferStatus(window.t("transfer.exportSuccess", Number(result.memory_count || 0)));
    } catch (error) {
      this.setTransferStatus(error.message || window.t("transfer.failed"), true);
    } finally {
      this._transferBusy = false;
      this.updateFeedback();
    }
  }

  async importFile(file) {
    if (!file || this._transferBusy || this._bulkBusy) return;
    if (file.size > 50 * 1024 * 1024) {
      this.setTransferStatus(window.t("transfer.fileTooLarge"), true);
      return;
    }
    const format = file.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
    const duplicateStrategy = document.getElementById("mem-import-duplicates").value || "skip";
    this._transferBusy = true;
    this.updateFeedback();
    this.setTransferStatus(window.t("flow.importReading", file.name));
    try {
      const content = await file.text();
      const requestPayload = { format, content, duplicate_strategy: duplicateStrategy };
      this.setTransferStatus(window.t("flow.importPreviewing", file.name));
      const preview = await this.api.post(
        "memories/import",
        { ...requestPayload, dry_run: true },
        { retries: 0 }
      );
      if (!Number(preview.planned_import_count || 0)) {
        this.setTransferStatus(window.t("flow.nothingToImport", preview.invalid_count || 0, preview.duplicate_count || 0));
        return;
      }
      this.setTransferStatus(window.t("flow.importReview", file.name));
      this.peek.open();
      const confirmed = await this.peek.showConfirmDialog(
        window.t("transfer.importPreviewTitle"),
        window.t(
          "transfer.importPreview",
          preview.valid_count || 0,
          preview.planned_import_count || 0,
          preview.duplicate_count || 0,
          preview.invalid_count || 0,
          preview.summary_required_count || 0
        ),
        { destructive: false, confirmLabel: window.t("transfer.import") }
      );
      if (!confirmed) {
        this.peek.close();
        this.setTransferStatus(window.t("flow.importCancelled"));
        return;
      }
      this.peek.close();
      this.setTransferStatus(window.t("flow.importing", file.name));
      const result = await this.api.post(
        "memories/import",
        { ...requestPayload, dry_run: false },
        { retries: 0 }
      );
      this.peek.close();
      const failed = Number(result.failed_count || 0);
      this.setTransferStatus(
        window.t(
          "transfer.importSuccess",
          Number(result.imported_count || 0),
          Number(result.skipped_duplicate_count || 0),
          failed
        ),
        failed > 0
      );
      await this.fetch();
    } catch (error) {
      this.peek.close();
      this.setTransferStatus(error.message || window.t("transfer.failed"), true);
    } finally {
      this._transferBusy = false;
      this.updateFeedback();
    }
  }

  setTransferStatus(message, isError = false) {
    const feedback = document.getElementById("transfer-feedback");
    if (!feedback) return;
    feedback.hidden = false;
    feedback.textContent = message;
    feedback.classList.toggle("is-error", isError);
  }

  applyFilters({ reset = false, defer = false } = {}) {
    clearTimeout(this._filterTimer);
    this._fetchGeneration++;
    const fields = { keyword: "mem-keyword", session: "mem-session", status: "mem-status", type: "mem-type", sort: "mem-sort" };
    const defaults = { keyword: "", session: "", status: "all", type: "all", sort: "created_desc" };
    for (const [key, id] of Object.entries(fields)) {
      const input = document.getElementById(id);
      if (reset) input.value = defaults[key];
      this.state.memory[key] = input.value.trim();
    }
    this.state.memory.pageSize = Number(document.getElementById("mem-page-size").value) || 20;
    this.state.memory.page = 1;
    if (defer) {
      this.state.memory.loading = true;
      this.updateFeedback();
      this._filterTimer = setTimeout(() => this.fetch(), 300);
    } else {
      return this.fetch();
    }
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

    const info = document.getElementById("mem-pagination-info");
    const prev = document.getElementById("mem-prev");
    const next = document.getElementById("mem-next");
    if (info) info.textContent = window.t("common.page", p, tp, t);
    const unavailable = Boolean(this.state.memory.loading || this.state.memory.error || this._bulkBusy || this._transferBusy);
    if (prev) prev.disabled = unavailable || p <= 1;
    if (next) next.disabled = unavailable || !this.state.memory.hasMore;
  }

  /**
   * 初始化事件监听
   */
  initEventListeners() {
    document.getElementById("memory-retry")?.addEventListener("click", () => this.fetch());
    // 表格行点击事件
    const tbody = document.getElementById("memories-body");
    if (tbody) {
      tbody.addEventListener("click", (e) => {
        if (this.state.memory.loading || this.state.memory.error) return;
        const checkbox = e.target.closest(".memory-select");
        if (checkbox) {
          const id = Number(checkbox.dataset.memoryId);
          if (checkbox.checked) this.state.memory.selectedIds.add(id);
          else this.state.memory.selectedIds.delete(id);
          const row = checkbox.closest("tr");
          if (row) row.classList.toggle("is-selected", checkbox.checked);
          this.updateSelectionControls();
          return;
        }
        const tr = e.target.closest("tr");
        if (!tr || !tr.dataset.key) return;

        const item = this.getItemByKey(tr.dataset.key);
        if (item) this.peek.renderMemory(item);
      });
    }

    document.getElementById("mem-select-all").addEventListener("change", (e) => {
      this.toggleAllOnPage(e.target.checked);
    });

    document.getElementById("mem-delete-selected").addEventListener("click", () => {
      this.deleteSelected();
    });

    document.getElementById("mem-batch-edit").addEventListener("click", () => {
      this.batchEdit();
    });

    document.getElementById("mem-export").addEventListener("click", () => {
      this.exportMemories();
    });

    const importInput = document.getElementById("mem-import-file");
    document.getElementById("mem-import").addEventListener("click", () => {
      importInput.value = "";
      importInput.click();
    });
    importInput.addEventListener("change", () => {
      this.importFile(importInput.files && importInput.files[0]);
    });

    const filters = document.getElementById("memory-filters");
    filters.addEventListener("keydown", event => {
      if (event.key === "Enter" && event.isComposing) event.preventDefault();
    });
    filters.addEventListener("submit", event => {
      event.preventDefault();
      if (!event.isComposing) this.applyFilters();
    });
    for (const id of ["mem-keyword", "mem-session"]) {
      const input = document.getElementById(id);
      input.addEventListener("input", event => {
        if (!event.isComposing) this.applyFilters({ defer: true });
      });
      input.addEventListener("compositionend", () => this.applyFilters({ defer: true }));
    }
    for (const id of ["mem-status", "mem-type", "mem-sort", "mem-page-size"]) {
      document.getElementById(id).addEventListener("change", () => this.applyFilters());
    }
    document.getElementById("mem-reset-filters").addEventListener("click", () => this.applyFilters({ reset: true }));
    document.getElementById("mem-refresh").addEventListener("click", () => this.fetch());
    document.getElementById("mem-clear-selection").addEventListener("click", () => {
      this.state.memory.selectedIds.clear();
      this.renderVirtual();
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
