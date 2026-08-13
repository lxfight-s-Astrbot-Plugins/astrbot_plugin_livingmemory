/**
 * Peek Panel - 侧边详情面板
 * 负责记忆详情展示、编辑、图节点查看等功能
 */

import {
  normalizeImportance,
  getDetailText,
  esc,
  statusPill,
  statusLabel,
  typeLabel,
  nodeBadge,
  metaItem
} from "./utils.js";

export class PeekPanel {
  constructor(state, apiClient) {
    this.state = state;
    this.api = apiClient;
    this._confirmResolve = null;
    this._prevPeekContent = null;
  }

  /**
   * 打开侧边面板
   * @param {boolean} isWide - 是否使用宽模式
   */
  open(isWide = false) {
    const panel = document.getElementById("peek-panel");
    panel.removeAttribute("inert");
    panel.setAttribute("aria-hidden", "false");
    panel.classList.add("visible");
    if (isWide) {
      panel.classList.add("wide");
    } else {
      panel.classList.remove("wide");
    }
    document.getElementById("peek-overlay").classList.add("visible");
  }

  /**
   * 关闭侧边面板
   */
  close() {
    // 如果有确认对话框待处理，先取消
    if (this._confirmResolve) {
      this._closeConfirmDialog(false);
    }
    const panel = document.getElementById("peek-panel");
    panel.classList.remove("visible", "wide");
    panel.setAttribute("inert", "");
    panel.setAttribute("aria-hidden", "true");
    document.getElementById("peek-overlay").classList.remove("visible");
    this.state.selectedMemory = null;
    this.state.isEditing = false;
    this.state._detailCache = null;
    this.state._nodeDetailCache = null;
  }

  /**
   * 渲染记忆详情
   * @param {Object} memory - 记忆对象
   */
  async renderMemory(memory) {
    this.state.selectedMemory = memory;
    this.state.isEditing = false;
    this.state._nodeDetailCache = null;
    const memoryId = memory.memory_id || memory.id;
    this.state._detailCache = null;

    // 从 API 获取完整详情
    let detail = null;
    try {
      detail = await this.api.get("memories/detail", { memory_id: memoryId });
      if (detail) this.state._detailCache = detail;
    } catch (_) {
      detail = null;
    }

    // Fallback: 使用传入的 memory 数据
    if (!detail) {
      const rawMeta = (memory.raw && memory.raw.metadata) || {};
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

    // 确保数值类型正确
    if (detail.memory_id != null) detail.memory_id = parseInt(detail.memory_id);
    detail.importance = normalizeImportance(detail.importance);

    this.renderDetailView(detail);
    this.open(true);
  }

  /**
   * 渲染记忆详情视图
   * @param {Object} detail - 记忆详情
   */
  renderDetailView(detail) {
    this.state._detailCache = detail;
    this.state._nodeDetailCache = null;
    this.state.isEditing = false;

    const id = detail.memory_id;
    const type = detail.memory_type || "GENERAL";
    const status = detail.status || "active";
    const importance = normalizeImportance(detail.importance).toFixed(1);
    const content = getDetailText(detail);
    const created = detail.created_at || "--";
    const updated = detail.updated_at || "--";
    const sessionId = detail.session_id || "--";
    const personaId = detail.persona_id || "--";
    const keyFacts = detail.key_facts || [];
    const topics = detail.topics || [];
    const editHistory = detail.update_history || [];
    const sourceMessages = Array.isArray(detail.source_messages) ? detail.source_messages : [];
    const consolidatedFrom = Array.isArray(detail.consolidated_from)
      ? detail.consolidated_from
      : (detail.metadata && Array.isArray(detail.metadata.consolidated_from))
        ? detail.metadata.consolidated_from
        : [];
    const graphCtx = detail.graph_context;

    document.getElementById("peek-badge").innerHTML = "";
    document.getElementById("peek-title").textContent = window.t("detail.memoryTitle", id);

    let html = "";

    // 状态 + 类型标签行
    html += '<div class="memory-detail-header">';
    html += statusPill(status);
    html += '<span class="type-tag">' + esc(type) + '</span>';
    html += '<span class="memory-detail-importance">' + window.t("detail.importance") + ': ' + importance + '/10</span>';
    html += '</div>';

    // 操作按钮
    html += '<div class="memory-detail-actions">';
    html += '<button class="btn btn-sm btn-secondary" id="peek-edit-btn"><i data-lucide="square-pen" aria-hidden="true"></i><span>' + window.t("detail.editBtn") + '</span></button>';
    if (sourceMessages.length >= 2) {
      html += '<button class="btn btn-sm btn-secondary" id="peek-resummarize-btn"><i data-lucide="refresh-cw" aria-hidden="true"></i><span>' + window.t("detail.resummarizeBtn") + '</span></button>';
    }
    html += '<button class="btn btn-sm btn-danger" id="peek-delete-btn"><i data-lucide="trash-2" aria-hidden="true"></i><span>' + window.t("detail.deleteBtn") + '</span></button>';
    html += '</div>';

    // 内容区域
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.content") + '</div>';
    html += '<div class="memory-detail-content" id="detail-content-display">' + esc(content) + '</div></div>';

    if (sourceMessages.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.sourceMessages", sourceMessages.length) + '</div><div class="edit-history-list">';
      sourceMessages.forEach(message => {
        const sender = message.sender_name || message.sender_id || message.role || "--";
        const time = message.timestamp ? new Date(message.timestamp * 1000).toLocaleString() : "--";
        html += '<div class="edit-history-item"><span class="edit-history-time">' + esc(time) + '</span>';
        html += '<span class="edit-history-desc"><strong>' + esc(String(sender)) + '</strong> ' + esc(String(message.content || "")) + '</span></div>';
      });
      html += '</div></div>';
    }

    // 图谱上下文小视图
    if (graphCtx && graphCtx.nodes && graphCtx.nodes.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.graphContext") + '</div>';
      html += '<canvas id="peek-mini-graph" class="memory-detail-mini-graph" width="440" height="160" data-memory-id="' + id + '"></canvas></div>';
    }

    // 元数据网格
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

    // 来源记忆（整合产生）
    if (consolidatedFrom.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.consolidatedFrom", consolidatedFrom.length) + '</div>';
      html += '<div class="peek-fact-list">';
      consolidatedFrom.forEach(id => { html += '<div class="peek-fact-item"># ' + esc(String(id)) + '</div>'; });
      html += '</div></div>';
    }

    // 关键事实
    if (keyFacts.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.keyFacts") + '</div><div class="peek-fact-list">';
      keyFacts.forEach(f => { html += '<div class="peek-fact-item">' + esc(String(f)) + '</div>'; });
      html += '</div></div>';
    }

    // 主题标签
    if (topics.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.topics") + '</div>';
      html += topics.map(t => '<span class="type-tag" style="margin-right:4px">' + esc(String(t)) + '</span>').join("");
      html += '</div>';
    }

    // 编辑历史
    if (editHistory.length) {
      html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.editHistory") + '</div><div class="edit-history-list">';
      editHistory.forEach(h => {
        const time = h.timestamp ? new Date(h.timestamp * 1000).toLocaleString() : (h.time || "--");
        html += '<div class="edit-history-item"><span class="edit-history-time">' + esc(time) + '</span>';
        html += '<span class="edit-history-desc">' + esc(h.description || h.field + ": " + h.old_value + " → " + h.new_value) + '</span></div>';
      });
      html += '</div></div>';
    }

    const peekBody = document.getElementById("peek-body");
    peekBody.innerHTML = html;
    if (window.lmHydrateIcons) window.lmHydrateIcons();
    peekBody.scrollTop = 0;

    // 绑定按钮事件
    const editBtn = document.getElementById("peek-edit-btn");
    const resummarizeBtn = document.getElementById("peek-resummarize-btn");
    const delBtn = document.getElementById("peek-delete-btn");
    if (editBtn) editBtn.addEventListener("click", () => this.renderEditView(detail));
    if (resummarizeBtn) resummarizeBtn.addEventListener("click", () => this.resummarizeMemory(id));
    if (delBtn) delBtn.addEventListener("click", () => this.deleteSingleMemory(parseInt(id)));

    // 加载图谱小视图
    const miniCanvas = document.getElementById("peek-mini-graph");
    if (miniCanvas && graphCtx && graphCtx.nodes && graphCtx.nodes.length) {
      this.loadMiniGraph(miniCanvas, graphCtx.nodes, graphCtx.edges);
    }
  }

  async resummarizeMemory(memoryId) {
    try {
      const result = await this.api.post(
        "memories/resummarize",
        { memory_id: Number(memoryId) },
        { retries: 0 }
      );
      this.showToast(window.t("detail.resummarizeSuccess", result.new_memory_id));
      await this.renderMemory({ memory_id: result.new_memory_id });
    } catch (error) {
      this.showToast(error.message || window.t("detail.resummarizeFailed"), true);
    }
  }

  /**
   * 渲染记忆编辑视图
   * @param {Object} detail - 记忆详情
   */
  renderEditView(detail) {
    this.state.isEditing = true;
    this.state._detailCache = detail;
    this.state._nodeDetailCache = null;

    const id = detail.memory_id;
    const content = getDetailText(detail);
    const importance = normalizeImportance(detail.importance).toFixed(1);
    const type = detail.memory_type || "GENERAL";
    const status = detail.status || "active";
    const topics = Array.isArray(detail.topics) ? detail.topics : [];
    const keyFacts = Array.isArray(detail.key_facts) ? detail.key_facts : [];

    let html = "";

    html += '<div class="memory-detail-header">';
    html += '<span style="font-size:12px;color:var(--text-secondary)">' + window.t("detail.editingTitle", id) + '</span>';
    html += '</div>';

    html += '<div class="memory-detail-actions">';
    html += '<button class="btn btn-sm btn-primary" id="peek-save-btn"><i data-lucide="save" aria-hidden="true"></i><span>' + window.t("detail.saveBtn") + '</span></button>';
    html += '<button class="btn btn-sm btn-ghost" id="peek-cancel-btn"><i data-lucide="x" aria-hidden="true"></i><span>' + window.t("detail.cancelBtn") + '</span></button>';
    html += '</div>';

    // 可编辑内容
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.content") + '</div>';
    html += '<textarea id="edit-content-area" class="memory-detail-edit-area" rows="6">' + esc(content) + '</textarea>';
    html += '<p class="form-hint" style="margin-top:4px">' + window.t("detail.contentHint") + '</p>';
    html += '</div>';

    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.topics") + '</div>';
    html += '<textarea id="edit-topics-area" class="memory-detail-edit-area compact" rows="3">' + esc(topics.join("\n")) + '</textarea>';
    html += '</div>';

    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.keyFacts") + '</div>';
    html += '<textarea id="edit-key-facts-area" class="memory-detail-edit-area compact" rows="5">' + esc(keyFacts.join("\n")) + '</textarea>';
    html += '</div>';

    // 可编辑元数据
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

    const peekBody = document.getElementById("peek-body");
    peekBody.innerHTML = html;
    if (window.lmHydrateIcons) window.lmHydrateIcons();
    peekBody.scrollTop = 0;

    // 绑定滑块事件
    document.getElementById("edit-importance").addEventListener("input", function() {
      document.getElementById("importance-value").textContent = parseFloat(this.value).toFixed(1);
    });

    const saveBtn = document.getElementById("peek-save-btn");
    const cancelBtn = document.getElementById("peek-cancel-btn");
    if (saveBtn) saveBtn.addEventListener("click", () => this.saveEdit(detail));
    if (cancelBtn) cancelBtn.addEventListener("click", () => this.renderDetailView(detail));
  }

  /**
   * 保存记忆编辑
   * @param {Object} detail - 原始记忆详情
   */
  async saveEdit(detail) {
    let id = detail.memory_id;
    const newContent = document.getElementById("edit-content-area").value.trim();
    const normalizeLines = (value) => Array.from(new Set(
      String(value || "").split(/\r?\n/).map(item => item.trim()).filter(Boolean)
    ));
    const sameList = (left, right) => (
      left.length === right.length && left.every((item, index) => item === right[index])
    );
    const newTopics = normalizeLines(document.getElementById("edit-topics-area").value);
    const newKeyFacts = normalizeLines(document.getElementById("edit-key-facts-area").value);
    const newStatus = document.getElementById("edit-status").value;
    const newType = document.getElementById("edit-type").value.trim();
    const newImportance = parseFloat(document.getElementById("edit-importance").value);
    const reason = document.getElementById("peek-edit-reason").value.trim();

    const saveBtn = document.getElementById("peek-save-btn");
    if (saveBtn) saveBtn.disabled = true;
    const messages = [];

    try {
      if (!newContent) {
        this.showToast(window.t("detail.contentRequired"), true);
        return;
      }

      const oldTopics = Array.isArray(detail.topics) ? detail.topics : [];
      const oldKeyFacts = Array.isArray(detail.key_facts) ? detail.key_facts : [];
      const rebuildRequired = newContent !== getDetailText(detail)
        || !sameList(newTopics, oldTopics)
        || !sameList(newKeyFacts, oldKeyFacts);

      // 内容、主题和关键事实在一次物理替换中更新，避免派生索引错位。
      if (rebuildRequired) {
        const result = await this.api.post("memories/update", {
          memory_id: id,
          field: "structured",
          value: {
            content: newContent,
            topics: newTopics,
            key_facts: newKeyFacts,
            status: newStatus,
            type: newType,
            importance: newImportance
          },
          value_scale: "display",
          reason: reason
        });
        if (result && result.new_memory_id) {
          id = parseInt(result.new_memory_id);
        }
        if (newContent !== getDetailText(detail)) messages.push(window.t("detail.contentUpdated", id));
        if (!sameList(newTopics, oldTopics)) messages.push(window.t("detail.topicsUpdated"));
        if (!sameList(newKeyFacts, oldKeyFacts)) messages.push(window.t("detail.keyFactsUpdated"));
        if (newStatus !== detail.status) messages.push(window.t("detail.statusUpdated", statusLabel(newStatus)));
        if (newType !== detail.memory_type) messages.push(window.t("detail.typeUpdated", newType));
        if (Math.abs(newImportance - normalizeImportance(detail.importance)) > 0.01) {
          messages.push(window.t("detail.importanceUpdated", newImportance.toFixed(1)));
        }
      } else {
        // 不影响派生数据的字段继续原位更新。
        if (newStatus !== detail.status) {
          await this.api.post("memories/update", {
            memory_id: id, field: "status", value: newStatus, reason: reason
          });
          messages.push(window.t("detail.statusUpdated", statusLabel(newStatus)));
        }
        if (newType !== detail.memory_type) {
          await this.api.post("memories/update", {
            memory_id: id, field: "type", value: newType, reason: reason
          });
          messages.push(window.t("detail.typeUpdated", newType));
        }
        if (Math.abs(newImportance - normalizeImportance(detail.importance)) > 0.01) {
          await this.api.post("memories/update", {
            memory_id: id,
            field: "importance",
            value: newImportance,
            value_scale: "display",
            reason: reason
          });
          messages.push(window.t("detail.importanceUpdated", newImportance.toFixed(1)));
        }
      }

      this.showToast(messages.length ? messages.join("; ") : window.t("detail.noChanges"));
      this.close();

      // 通知刷新记忆列表（通过全局回调）
      if (window.lmRefreshMemories) {
        await window.lmRefreshMemories();
      }
    } catch (e) {
      this.showToast(e.message || window.t("edit.updateFailed"), true);
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  /**
   * 删除单个记忆
   * @param {number} id - 记忆 ID
   */
  async deleteSingleMemory(id) {
    const confirmed = await this.showConfirmDialog(
      window.t("confirm.deleteTitle"),
      window.t("confirm.deleteMessage", id)
    );

    if (!confirmed) return;

    try {
      await this.api.post("memories/batch-delete", { memory_ids: [id] });
      this.showToast(window.t("memory.deleted"));
      this.close();

      // 通知刷新记忆列表
      if (window.lmRefreshMemories) {
        await window.lmRefreshMemories();
      }
    } catch (e) {
      this.showToast(e.message || window.t("memory.deleteFailed"), true);
    }
  }

  /**
   * 渲染图节点详情
   * @param {Object} nodeData - 节点数据
   */
  renderNode(nodeData) {
    this.state._nodeDetailCache = nodeData;
    this.state._detailCache = null;
    this.state.isEditing = false;

    const panel = document.getElementById("peek-panel");
    panel.classList.remove("wide");
    document.getElementById("peek-badge").innerHTML = nodeBadge(nodeData.type);
    document.getElementById("peek-title").textContent = nodeData.label || window.t("graph.unnamedNode");

    let html = '<div class="peek-section">';
    html += '<div class="peek-meta-grid">';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeMemories") + '</span><span class="peek-meta-value">' + (nodeData.memory_count || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeDegree") + '</span><span class="peek-meta-value">' + (nodeData.degree || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeEntries") + '</span><span class="peek-meta-value">' + (nodeData.entry_count || 0) + '</span></div>';
    html += '<div class="peek-meta-item"><span class="peek-meta-label">' + window.t("detail.nodeWeight") + '</span><span class="peek-meta-value">' + Number(nodeData.weight || 0).toFixed(2) + '</span></div>';
    html += '</div></div>';

    document.getElementById("peek-body").innerHTML = html;
    this.open(false);
  }

  /**
   * 加载图谱小视图
   * @param {HTMLCanvasElement} canvas - Canvas 元素
   * @param {Array} nodes - 节点列表
   * @param {Array} edges - 边列表
   */
  loadMiniGraph(canvas, nodes, edges) {
    if (!canvas || !nodes || !nodes.length) return;

    // 调用全局的图谱绘制函数（如果存在）
    if (window.lmDrawMiniGraph) {
      window.lmDrawMiniGraph(canvas, nodes, edges);
    }
  }

  /**
   * 显示确认对话框
   * @param {string} title - 标题
   * @param {string} message - 消息
   * @returns {Promise<boolean>} 用户是否确认
   */
  showConfirmDialog(title, message, options = {}) {
    return new Promise((resolve) => {
      const destructive = options.destructive !== false;
      this._confirmResolve = resolve;
      this._prevPeekContent = document.getElementById("peek-body").innerHTML;

      let html = '<div class="confirm-dialog">';
      html += '<div class="confirm-dialog-title">' + esc(title) + '</div>';
      html += '<div class="confirm-dialog-message">' + esc(message) + '</div>';
      html += '<div class="confirm-dialog-actions">';
      html += '<button class="btn btn-secondary" id="confirm-cancel-btn"><i data-lucide="x" aria-hidden="true"></i><span>' + window.t("common.cancel") + '</span></button>';
      html += '<button class="btn ' + (destructive ? 'btn-danger' : 'btn-primary') + '" id="confirm-ok-btn"><i data-lucide="' + (destructive ? 'trash-2' : 'check') + '" aria-hidden="true"></i><span>' + window.t("common.confirm") + '</span></button>';
      html += '</div></div>';

      document.getElementById("peek-body").innerHTML = html;
      if (window.lmHydrateIcons) window.lmHydrateIcons();

      const okBtn = document.getElementById("confirm-ok-btn");
      const cancelBtn = document.getElementById("confirm-cancel-btn");
      if (okBtn) okBtn.addEventListener("click", () => this._closeConfirmDialog(true));
      if (cancelBtn) cancelBtn.addEventListener("click", () => this._closeConfirmDialog(false));
    });
  }

  /**
   * 关闭确认对话框
   * @param {boolean} result - 确认结果
   */
  _closeConfirmDialog(result) {
    const peekBody = document.getElementById("peek-body");

    // 如果取消，恢复之前的内容
    if (!result && this._prevPeekContent && peekBody) {
      peekBody.innerHTML = this._prevPeekContent;
      // 重新绑定详情视图按钮
      if (this.state._detailCache && !this.state.isEditing) {
        this.renderDetailView(this.state._detailCache);
      }
    }
    this._prevPeekContent = null;

    if (this._confirmResolve) {
      this._confirmResolve(!!result);
      this._confirmResolve = null;
    }
  }

  /**
   * 显示批量编辑对话框
   *
   * 复用 peek-body 渲染表单，供 MemoryPage 批量更新
   * importance / status / type 字段。
   *
   * @param {number} count - 选中记忆数量
   * @returns {Promise<{field: string, value: any, value_scale: string}|null>}
   *   用户点击应用时 resolve 编辑参数，取消时 resolve null
   */
  showBatchEditDialog(count) {
    return new Promise((resolve) => {
      this._batchEditResolve = resolve;
      this._prevPeekContent = document.getElementById("peek-body").innerHTML;

      let html = '<div class="confirm-dialog">';
      html += '<div class="confirm-dialog-title">' + esc(window.t("batchEdit.title", count)) + '</div>';
      html += '<div class="batch-edit-form">';
      html += '<label class="batch-edit-label" for="batch-edit-field">' + esc(window.t("batchEdit.field")) + '</label>';
      html += '<select id="batch-edit-field" class="input">';
      html += '<option value="importance" selected>' + esc(window.t("batchEdit.importance")) + '</option>';
      html += '<option value="status">' + esc(window.t("batchEdit.status")) + '</option>';
      html += '<option value="type">' + esc(window.t("batchEdit.type")) + '</option>';
      html += '</select>';
      html += '<div id="batch-edit-value-wrap"></div>';
      html += '</div>';
      html += '<div class="confirm-dialog-actions">';
      html += '<button class="btn btn-secondary" id="batch-edit-cancel-btn"><i data-lucide="x" aria-hidden="true"></i><span>' + window.t("common.cancel") + '</span></button>';
      html += '<button class="btn btn-primary" id="batch-edit-apply-btn"><i data-lucide="check" aria-hidden="true"></i><span>' + window.t("batchEdit.apply") + '</span></button>';
      html += '</div></div>';

      document.getElementById("peek-body").innerHTML = html;
      if (window.lmHydrateIcons) window.lmHydrateIcons();

      const fieldSelect = document.getElementById("batch-edit-field");
      if (fieldSelect) {
        fieldSelect.addEventListener("change", () => this._renderBatchEditValueInput(fieldSelect.value));
      }
      this._renderBatchEditValueInput("importance");

      const applyBtn = document.getElementById("batch-edit-apply-btn");
      const cancelBtn = document.getElementById("batch-edit-cancel-btn");
      if (applyBtn) applyBtn.addEventListener("click", () => this._submitBatchEdit());
      if (cancelBtn) cancelBtn.addEventListener("click", () => this._closeBatchEditDialog(null));
    });
  }

  /**
   * 按所选字段渲染对应的值输入控件
   * @param {string} field - importance | status | type
   */
  _renderBatchEditValueInput(field) {
    const wrap = document.getElementById("batch-edit-value-wrap");
    if (!wrap) return;

    let html = '<label class="batch-edit-label" for="batch-edit-value">' + esc(window.t("batchEdit.value")) + '</label>';
    if (field === "status") {
      html += '<select id="batch-edit-value" class="input">';
      for (const status of ["active", "archived", "deleted"]) {
        html += '<option value="' + status + '">' + esc(window.t("status." + status)) + '</option>';
      }
      html += '</select>';
    } else if (field === "importance") {
      html += '<input type="number" id="batch-edit-value" class="input" min="0" max="10" step="0.1" placeholder="0-10" />';
    } else {
      html += '<input type="text" id="batch-edit-value" class="input" placeholder="' + esc(window.t("batchEdit.typePlaceholder")) + '" />';
    }
    wrap.innerHTML = html;
  }

  /**
   * 收集并校验批量编辑输入，成功则关闭对话框并返回编辑参数
   */
  _submitBatchEdit() {
    const fieldSelect = document.getElementById("batch-edit-field");
    const valueEl = document.getElementById("batch-edit-value");
    const field = fieldSelect ? fieldSelect.value : "importance";
    const rawValue = valueEl ? String(valueEl.value).trim() : "";

    if (!rawValue) {
      this.showToast(window.t("batchEdit.valueRequired"), true);
      return;
    }

    let value = rawValue;
    let value_scale = "auto";
    if (field === "importance") {
      const parsed = Number(rawValue);
      if (!Number.isFinite(parsed) || parsed < 0 || parsed > 10) {
        this.showToast(window.t("batchEdit.importanceRange"), true);
        return;
      }
      value = parsed;
      value_scale = "display";
    }

    this._closeBatchEditDialog({ field, value, value_scale });
  }

  /**
   * 关闭批量编辑对话框并恢复原 peek 内容
   * @param {{field: string, value: any, value_scale: string}|null} result
   */
  _closeBatchEditDialog(result) {
    const peekBody = document.getElementById("peek-body");

    if (this._prevPeekContent && peekBody) {
      peekBody.innerHTML = this._prevPeekContent;
      // 重新绑定详情视图按钮
      if (this.state._detailCache && !this.state.isEditing) {
        this.renderDetailView(this.state._detailCache);
      }
    }
    this._prevPeekContent = null;

    if (this._batchEditResolve) {
      this._batchEditResolve(result);
      this._batchEditResolve = null;
    }
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
