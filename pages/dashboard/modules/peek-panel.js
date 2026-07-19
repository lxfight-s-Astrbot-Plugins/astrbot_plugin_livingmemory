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
    this._saveJobRunning = false;
  }

  /**
   * 打开侧边面板
   * @param {boolean} isWide - 是否使用宽模式
   */
  open(isWide = false) {
    const panel = document.getElementById("peek-panel");
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
    const saveOverlay = document.getElementById("memory-save-overlay");
    if (saveOverlay && !this._saveJobRunning) saveOverlay.remove();
    const panel = document.getElementById("peek-panel");
    panel.classList.remove("visible", "wide");
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
    html += '<button class="btn btn-sm btn-secondary" id="peek-edit-btn">' + window.t("detail.editBtn") + '</button>';
    html += '<button class="btn btn-sm btn-danger" id="peek-delete-btn">' + window.t("detail.deleteBtn") + '</button>';
    html += '</div>';

    // 内容区域
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.content") + '</div>';
    html += '<div class="memory-detail-content" id="detail-content-display">' + esc(content) + '</div></div>';

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

    document.getElementById("peek-body").innerHTML = html;

    // 绑定按钮事件
    const editBtn = document.getElementById("peek-edit-btn");
    const delBtn = document.getElementById("peek-delete-btn");
    if (editBtn) editBtn.addEventListener("click", () => this.renderEditView(detail));
    if (delBtn) delBtn.addEventListener("click", () => this.deleteSingleMemory(parseInt(id)));

    // 加载图谱小视图
    const miniCanvas = document.getElementById("peek-mini-graph");
    if (miniCanvas && graphCtx && graphCtx.nodes && graphCtx.nodes.length) {
      this.loadMiniGraph(miniCanvas, graphCtx.nodes, graphCtx.edges);
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
    const metadata = detail.metadata || {};
    const content = detail.persona_summary || metadata.persona_summary || detail.summary || getDetailText(detail);
    const topics = Array.isArray(detail.topics) ? detail.topics : [];
    const keyFacts = Array.isArray(detail.key_facts) ? detail.key_facts : [];
    const participants = Array.isArray(detail.participants) ? detail.participants : [];
    const sentiment = detail.sentiment || metadata.sentiment || "neutral";
    const importance = normalizeImportance(detail.importance).toFixed(1);
    const type = detail.memory_type || "GENERAL";
    const status = detail.status || "active";

    let html = "";

    html += '<div class="memory-detail-header">';
    html += '<span style="font-size:12px;color:var(--text-secondary)">' + window.t("detail.editingTitle", id) + '</span>';
    html += '</div>';

    html += '<div class="memory-detail-actions">';
    html += '<button class="btn btn-sm btn-primary" id="peek-save-btn">' + window.t("detail.saveBtn") + '</button>';
    html += '<button class="btn btn-sm btn-ghost" id="peek-cancel-btn">' + window.t("detail.cancelBtn") + '</button>';
    html += '</div>';

    // 可编辑结构化记忆
    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.summary") + '</div>';
    html += '<textarea id="edit-content-area" class="memory-detail-edit-area" rows="6">' + esc(content) + '</textarea>';
    html += '<p class="form-hint" style="margin-top:4px">' + window.t("detail.contentHint") + '</p>';
    html += '</div>';

    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.topics") + '</div>';
    html += this.renderEditableMemoryItems("edit-topics-list", topics);
    html += '<button type="button" class="btn btn-sm btn-ghost memory-item-add" data-target="edit-topics-list">' + esc(window.t("detail.addItem")) + '</button>';
    html += '<p class="form-hint">' + window.t("detail.itemEditHint") + '</p></div>';

    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.keyFacts") + '</div>';
    html += this.renderEditableMemoryItems("edit-key-facts-list", keyFacts);
    html += '<button type="button" class="btn btn-sm btn-ghost memory-item-add" data-target="edit-key-facts-list">' + esc(window.t("detail.addItem")) + '</button>';
    html += '<p class="form-hint">' + window.t("detail.itemEditHint") + '</p></div>';

    html += '<div class="peek-section"><div class="peek-section-title">' + window.t("detail.participants") + '</div>';
    html += '<textarea id="edit-participants-area" class="memory-detail-edit-area" rows="3">' + esc(participants.join("\n")) + '</textarea>';
    html += '<p class="form-hint">' + window.t("detail.onePerLine") + '</p></div>';

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

    html += '<div class="memory-detail-meta-item">';
    html += '<span class="memory-detail-meta-label">' + window.t("detail.sentiment") + '</span>';
    html += '<select id="edit-sentiment" class="memory-detail-select">';
    ["positive", "neutral", "negative"].forEach(value => {
      html += '<option value="' + value + '"' + (sentiment === value ? " selected" : "") + '>' + window.t("detail.sentiment." + value) + '</option>';
    });
    html += '</select></div>';

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

    // 绑定滑块事件
    document.getElementById("edit-importance").addEventListener("input", function() {
      document.getElementById("importance-value").textContent = parseFloat(this.value).toFixed(1);
    });
    document.querySelectorAll(".memory-item-add").forEach(button => {
      button.addEventListener("click", () => this.addEditableMemoryItem(button.dataset.target));
    });
    document.querySelectorAll(".memory-item-remove").forEach(button => {
      button.addEventListener("click", () => this.removeEditableMemoryItem(button));
    });

    const saveBtn = document.getElementById("peek-save-btn");
    const cancelBtn = document.getElementById("peek-cancel-btn");
    if (saveBtn) saveBtn.addEventListener("click", () => this.saveEdit(detail));
    if (cancelBtn) cancelBtn.addEventListener("click", () => this.renderDetailView(detail));
  }

  renderEditableMemoryItems(containerId, values) {
    const rows = values.map(value =>
      '<div class="memory-item-row" data-original="' + esc(String(value)) + '">' +
      '<input type="text" class="memory-detail-select memory-item-input" value="' + esc(String(value)) + '" />' +
      '<button type="button" class="btn btn-sm btn-ghost memory-item-remove" title="' + esc(window.t("detail.removeItem")) + '">×</button></div>'
    ).join("");
    return '<div class="memory-item-list" id="' + containerId + '">' + rows + '</div>';
  }

  addEditableMemoryItem(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const row = document.createElement("div");
    row.className = "memory-item-row";
    row.dataset.original = "";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "memory-detail-select memory-item-input";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-sm btn-ghost memory-item-remove";
    remove.title = window.t("detail.removeItem");
    remove.textContent = "×";
    remove.addEventListener("click", () => this.removeEditableMemoryItem(remove));
    row.appendChild(input);
    row.appendChild(remove);
    container.appendChild(row);
    input.focus();
  }

  removeEditableMemoryItem(button) {
    const row = button.closest(".memory-item-row");
    if (!row) return;
    if (row.dataset.original) {
      row.dataset.deleted = "true";
      row.hidden = true;
      const input = row.querySelector(".memory-item-input");
      if (input) input.value = "";
    } else {
      row.remove();
    }
  }

  collectEditableMemoryItems(containerId, field) {
    const container = document.getElementById(containerId);
    const values = [];
    const changes = [];
    if (!container) return { values, changes };
    container.querySelectorAll(".memory-item-row").forEach(row => {
      const original = String(row.dataset.original || "").trim();
      const input = row.querySelector(".memory-item-input");
      const current = input ? input.value.trim() : "";
      const deleted = row.dataset.deleted === "true";
      if (original && deleted) {
        changes.push({ field, operation: "remove", before: original, after: null });
        return;
      }
      if (current) values.push(current);
      if (original && current && original !== current) {
        changes.push({ field, operation: "replace", before: original, after: current });
      } else if (!original && current) {
        changes.push({ field, operation: "add", before: null, after: current });
      }
    });
    return { values, changes };
  }

  /**
   * 保存记忆编辑
   * @param {Object} detail - 原始记忆详情
   */
  async saveEdit(detail) {
    const newContent = document.getElementById("edit-content-area").value.trim();
    const topicEdit = this.collectEditableMemoryItems("edit-topics-list", "topics");
    const factEdit = this.collectEditableMemoryItems("edit-key-facts-list", "key_facts");
    const newTopics = topicEdit.values;
    const newKeyFacts = factEdit.values;
    const newParticipants = document.getElementById("edit-participants-area").value.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
    const newSentiment = document.getElementById("edit-sentiment").value;
    const newStatus = document.getElementById("edit-status").value;
    const newType = document.getElementById("edit-type").value.trim();
    const newImportance = parseFloat(document.getElementById("edit-importance").value);
    const reason = document.getElementById("peek-edit-reason").value.trim();
    if (!newContent) {
      this.showToast(window.t("detail.contentRequired"), true);
      return;
    }

    this.openSaveDialog({
      memory_id: detail.memory_id,
      value: {
        summary: newContent,
        persona_summary: newContent,
        topics: newTopics,
        key_facts: newKeyFacts,
        participants: newParticipants,
        sentiment: newSentiment,
        importance: newImportance,
        importance_scale: "display",
        memory_type: newType,
        status: newStatus
      },
      field_changes: topicEdit.changes.concat(factEdit.changes),
      reason: reason
    });
  }

  openSaveDialog(updatePayload) {
    const previous = document.getElementById("memory-save-overlay");
    if (previous) previous.remove();

    const overlay = document.createElement("div");
    overlay.id = "memory-save-overlay";
    overlay.className = "modal-overlay visible memory-save-overlay";
    overlay.innerHTML = '<div class="modal memory-save-modal" role="dialog" aria-modal="true">' +
      '<div class="modal-header"><div class="modal-title">' + esc(window.t("saveDialog.title")) + '</div>' +
      '<button class="modal-close" id="memory-save-close" aria-label="Close">×</button></div>' +
      '<div class="modal-body" id="memory-save-body">' +
      '<div class="save-danger-notice"><strong>' + esc(window.t("saveDialog.dangerTitle")) + '</strong>' +
      '<span>' + esc(window.t("saveDialog.dangerMessage")) + '</span></div>' +
      '<div class="save-dialog-section"><div class="save-dialog-label">' + esc(window.t("detail.saveMode")) + '</div>' +
      '<label class="save-dialog-option"><input type="radio" name="memory-save-mode" value="in_place" checked /> ' + esc(window.t("saveDialog.mode.sameId")) + '</label>' +
      '<label class="save-dialog-option"><input type="radio" name="memory-save-mode" value="rebuild" /> ' + esc(window.t("saveDialog.mode.newId")) + '</label></div>' +
      '<div class="save-dialog-section"><label class="save-dialog-label" for="memory-related-scope">' + esc(window.t("saveDialog.relatedScope")) + '</label>' +
      '<select id="memory-related-scope" class="memory-detail-select save-dialog-select">' +
      '<option value="current">' + esc(window.t("saveDialog.scope.current")) + '</option>' +
      '<option value="session">' + esc(window.t("saveDialog.scope.session")) + '</option>' +
      '<option value="persona">' + esc(window.t("saveDialog.scope.persona")) + '</option></select></div>' +
      '<div id="memory-related-detection" class="save-dialog-detection" hidden>' +
      '<button class="btn btn-secondary btn-sm" id="memory-detect-related">' + esc(window.t("saveDialog.detect")) + '</button>' +
      '<span class="save-dialog-detect-status" id="memory-detect-status"></span>' +
      '<div class="save-related-list" id="memory-related-list"></div>' +
      '<label class="save-risk-confirm" id="memory-risk-confirm-wrap" hidden><input type="checkbox" id="memory-risk-confirm" />' +
      '<span>' + esc(window.t("saveDialog.riskConfirm")) + '</span></label></div>' +
      '<div class="save-progress" id="memory-save-progress" hidden>' +
      '<div class="save-progress-track"><div class="save-progress-bar" id="memory-save-progress-bar"></div></div>' +
      '<div class="save-progress-text" id="memory-save-progress-text"></div>' +
      '<div class="save-progress-current" id="memory-save-progress-current"></div></div>' +
      '</div><div class="modal-footer">' +
      '<button class="btn btn-secondary" id="memory-save-cancel">' + esc(window.t("common.cancel")) + '</button>' +
      '<button class="btn btn-primary" id="memory-save-confirm">' + esc(window.t("common.confirm")) + '</button>' +
      '</div></div>';
    document.body.appendChild(overlay);

    const scopeSelect = overlay.querySelector("#memory-related-scope");
    const detection = overlay.querySelector("#memory-related-detection");
    const detectBtn = overlay.querySelector("#memory-detect-related");
    const confirmBtn = overlay.querySelector("#memory-save-confirm");
    const cancelBtn = overlay.querySelector("#memory-save-cancel");
    const closeBtn = overlay.querySelector("#memory-save-close");
    overlay._relatedDetected = false;
    overlay._planId = "";

    const closeDialog = () => {
      if (this._saveJobRunning) return;
      const shouldRefresh = !!overlay._jobFinished;
      overlay.remove();
      if (shouldRefresh) {
        this.close();
        if (window.lmRefreshMemories) {
          Promise.resolve(window.lmRefreshMemories()).catch(() => {});
        }
      }
    };
    const resetDetection = () => {
      const scoped = scopeSelect.value !== "current";
      detection.hidden = !scoped;
      overlay._relatedDetected = !scoped;
      overlay._planId = "";
      confirmBtn.disabled = scoped;
      overlay.querySelector("#memory-related-list").innerHTML = "";
      overlay.querySelector("#memory-detect-status").textContent = scoped ? window.t("saveDialog.detectRequired") : "";
      overlay.querySelector("#memory-risk-confirm-wrap").hidden = true;
      overlay.querySelector("#memory-risk-confirm").checked = false;
    };
    scopeSelect.addEventListener("change", resetDetection);
    cancelBtn.addEventListener("click", closeDialog);
    closeBtn.addEventListener("click", closeDialog);
    overlay.addEventListener("click", event => {
      if (event.target === overlay) closeDialog();
    });
    detectBtn.addEventListener("click", () => this.detectRelatedMemories(overlay, updatePayload));
    confirmBtn.addEventListener("click", () => this.startSaveJob(overlay, updatePayload));
    resetDetection();
  }

  async detectRelatedMemories(overlay, updatePayload) {
    const detectBtn = overlay.querySelector("#memory-detect-related");
    const status = overlay.querySelector("#memory-detect-status");
    const list = overlay.querySelector("#memory-related-list");
    const confirmBtn = overlay.querySelector("#memory-save-confirm");
    const scope = overlay.querySelector("#memory-related-scope").value;
    detectBtn.disabled = true;
    status.textContent = window.t("saveDialog.detecting");
    list.innerHTML = "";
    try {
      const result = await this.api.post("memories/related", {
        memory_id: updatePayload.memory_id,
        scope: scope,
        value: updatePayload.value,
        field_changes: updatePayload.field_changes || []
      });
      const items = (result && result.items) || [];
      overlay._planId = (result && result.plan_id) || "";
      if (!items.length) {
        list.innerHTML = '<div class="save-related-empty">' + esc(window.t("saveDialog.noRelated")) + '</div>';
      } else {
        list.innerHTML = items.map(item => {
          const modifications = Array.isArray(item.modifications) ? item.modifications : [];
          const details = modifications.map(mod => {
            const matchLabel = window.t("saveDialog.match." + (mod.match_type || "near"));
            const fieldLabel = window.t("saveDialog.field." + (mod.field || "key_facts"));
            const actionLabel = window.t(
              mod.match_type === "near" ? "saveDialog.action.nearReplace" : "saveDialog.action.exactReplace"
            );
            const score = Math.round(Number(mod.score || 0) * 100);
            return '<div class="save-related-change">' +
              '<div class="save-related-change-meta"><span>' + esc(fieldLabel) + ' · ' + esc(actionLabel) + '</span><span>' + esc(matchLabel) + ' · ' + score + '%</span></div>' +
              '<div class="save-related-diff"><span class="save-related-before">− ' + esc(mod.candidate_before || "") + '</span>' +
              '<span class="save-related-after">+ ' + esc(mod.candidate_after || "") + '</span></div>' +
              (mod.summary_changed ? '<details class="save-summary-diff"><summary>' + esc(window.t("saveDialog.summaryChanged")) + '</summary>' +
                '<div class="save-related-diff"><span class="save-related-before">− ' + esc(mod.summary_before || "") + '</span>' +
                '<span class="save-related-after">+ ' + esc(mod.summary_after || "") + '</span></div></details>' : '') +
              '</div>';
          }).join("");
          return '<div class="save-related-item">' +
            '<label class="save-related-head"><input type="checkbox" class="memory-related-checkbox" value="' + esc(item.plan_item_id || "") + '" data-memory-id="' + parseInt(item.memory_id) + '"' + (item.default_selected ? ' checked' : '') + ' />' +
            '<span class="save-related-id">#' + parseInt(item.memory_id) + '</span>' +
            '<span class="save-related-excerpt">' + esc(item.excerpt || "") + '</span></label>' +
            details + '<div class="save-related-impact">' + esc(window.t("saveDialog.rebuildImpact")) + '</div></div>';
        }).join("");
      }
      overlay._relatedDetected = true;
      confirmBtn.disabled = false;
      status.textContent = window.t("saveDialog.detected", items.length);
      overlay.querySelector("#memory-risk-confirm-wrap").hidden = !items.length;
    } catch (e) {
      overlay._relatedDetected = false;
      overlay._planId = "";
      confirmBtn.disabled = true;
      status.textContent = e.message || window.t("saveDialog.detectFailed");
    } finally {
      detectBtn.disabled = false;
    }
  }

  async startSaveJob(overlay, updatePayload) {
    const scope = overlay.querySelector("#memory-related-scope").value;
    if (scope !== "current" && !overlay._relatedDetected) return;
    const modeInput = overlay.querySelector('input[name="memory-save-mode"]:checked');
    const selectedPlanItemIds = Array.from(overlay.querySelectorAll(".memory-related-checkbox:checked"))
      .map(input => input.value).filter(Boolean);
    if (selectedPlanItemIds.length && !overlay.querySelector("#memory-risk-confirm").checked) {
      this.showToast(window.t("saveDialog.riskConfirmRequired"), true);
      return;
    }
    const confirmBtn = overlay.querySelector("#memory-save-confirm");
    const cancelBtn = overlay.querySelector("#memory-save-cancel");
    const closeBtn = overlay.querySelector("#memory-save-close");
    confirmBtn.disabled = true;
    cancelBtn.disabled = true;
    closeBtn.disabled = true;
    overlay.querySelector("#memory-related-scope").disabled = true;
    overlay.querySelectorAll('input[name="memory-save-mode"], .memory-related-checkbox, #memory-risk-confirm').forEach(input => { input.disabled = true; });
    const detectBtn = overlay.querySelector("#memory-detect-related");
    if (detectBtn) detectBtn.disabled = true;
    overlay.querySelector("#memory-save-progress").hidden = false;
    this._saveJobRunning = true;
    let jobStarted = false;
    let jobId = "";

    try {
      const job = await this.api.post("memories/update/start", {
        ...updatePayload,
        update_mode: modeInput ? modeInput.value : "in_place",
        scope: scope,
        plan_id: scope === "current" ? "" : overlay._planId,
        selected_plan_item_ids: selectedPlanItemIds,
        risk_acknowledged: selectedPlanItemIds.length > 0 && overlay.querySelector("#memory-risk-confirm").checked
      });
      jobId = job.job_id;
      jobStarted = true;
      await this.pollSaveJob(overlay, jobId);
    } catch (e) {
      this._saveJobRunning = false;
      cancelBtn.disabled = false;
      closeBtn.disabled = false;
      if (jobStarted) {
        overlay._jobFinished = true;
        cancelBtn.textContent = window.t("common.close");
        overlay.querySelector("#memory-save-progress-current").textContent = window.t("saveDialog.progressUnavailable", jobId);
      } else {
        confirmBtn.disabled = false;
        overlay.querySelector("#memory-related-scope").disabled = false;
        overlay.querySelectorAll('input[name="memory-save-mode"], .memory-related-checkbox, #memory-risk-confirm').forEach(input => { input.disabled = false; });
        if (detectBtn) detectBtn.disabled = false;
        overlay.querySelector("#memory-save-progress").hidden = true;
      }
      this.showToast(e.message || window.t("edit.updateFailed"), true);
    }
  }

  async pollSaveJob(overlay, jobId) {
    let job = null;
    while (true) {
      job = await this.api.get("memories/update/progress", { job_id: jobId });
      this.renderSaveProgress(overlay, job);
      if (job.status === "completed" || job.status === "failed") break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }

    this._saveJobRunning = false;
    if (job.status === "failed") {
      this.showToast(window.t("saveDialog.jobFailed"), true);
      const cancelBtn = overlay.querySelector("#memory-save-cancel");
      const closeBtn = overlay.querySelector("#memory-save-close");
      cancelBtn.disabled = false;
      closeBtn.disabled = false;
      cancelBtn.textContent = window.t("common.close");
      return;
    }

    this.showToast(window.t("saveDialog.completed", job.succeeded, job.failed), job.failed > 0);
    if (job.failed > 0) {
      overlay._jobFinished = true;
      const failed = (job.results || []).filter(item => item.status === "failed");
      const detection = overlay.querySelector("#memory-related-detection");
      const list = overlay.querySelector("#memory-related-list");
      detection.hidden = false;
      list.innerHTML = '<div class="save-dialog-label">' + esc(window.t("saveDialog.failedItems")) + '</div>' + failed.map(item =>
        '<div class="save-related-item"><span></span><span class="save-related-id">#' + parseInt(item.old_memory_id) + '</span>' +
        '<span class="save-related-excerpt">' + esc(item.error || window.t("edit.updateFailed")) + '</span></div>'
      ).join("");
      const cancelBtn = overlay.querySelector("#memory-save-cancel");
      const closeBtn = overlay.querySelector("#memory-save-close");
      cancelBtn.disabled = false;
      closeBtn.disabled = false;
      cancelBtn.textContent = window.t("common.close");
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 700));
    overlay.remove();
    this.close();
    if (window.lmRefreshMemories) await window.lmRefreshMemories();
  }

  renderSaveProgress(overlay, job) {
    const percent = Math.max(0, Math.min(100, Number(job.percent || 0)));
    overlay.querySelector("#memory-save-progress-bar").style.width = percent + "%";
    overlay.querySelector("#memory-save-progress-text").textContent = window.t(
      "saveDialog.progress", job.completed || 0, job.total || 0, percent
    );
    const current = job.current_item;
    overlay.querySelector("#memory-save-progress-current").textContent = current
      ? window.t("saveDialog.processing", current.memory_id, current.excerpt || "")
      : window.t("saveDialog.finishing");
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
  showConfirmDialog(title, message) {
    return new Promise((resolve) => {
      this._confirmResolve = resolve;
      this._prevPeekContent = document.getElementById("peek-body").innerHTML;

      let html = '<div class="confirm-dialog">';
      html += '<div class="confirm-dialog-title">' + esc(title) + '</div>';
      html += '<div class="confirm-dialog-message">' + esc(message) + '</div>';
      html += '<div class="confirm-dialog-actions">';
      html += '<button class="btn btn-secondary" id="confirm-cancel-btn">' + window.t("common.cancel") + '</button>';
      html += '<button class="btn btn-danger" id="confirm-ok-btn">' + window.t("common.confirm") + '</button>';
      html += '</div></div>';

      document.getElementById("peek-body").innerHTML = html;

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
