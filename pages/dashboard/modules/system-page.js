/**
 * System Page - 系统统计页面
 * 负责展示系统概览和统计信息
 */

import { esc, atomLabel } from "./utils.js";

export class SystemPage {
  constructor(state, apiClient) {
    this.state = state;
    this.api = apiClient;
  }

  /**
   * 获取系统概览数据
   */
  async fetch() {
    // 如果有缓存且未过期，直接使用
    if (this.state._systemCache && Date.now() - this.state._systemCache.timestamp < 10000) {
      this.render(this.state._systemCache.data);
      return;
    }

    try {
      const data = await this.api.get("stats");
      this.state._systemCache = { data, timestamp: Date.now() };
      this.render(data);
    } catch (e) {
      this.showToast(e.message || window.t("system.fetchFailed"), true);
    }
  }

  /**
   * 渲染系统概览
   * @param {Object} data - 统计数据
   */
  render(data) {
    // 更新统计卡片
    this.renderStatCards(data);

    // 更新重要性分布图表
    this.renderImportanceChart(data.importance_distribution || {});

    // 更新 Atom 类型图表 - 后端返回 atom_breakdown
    this.renderAtomChart(data.atom_breakdown || {});

    // 更新活跃会话列表 - 后端返回 recent_sessions
    this.renderSessionList(data.recent_sessions || []);

    // 更新备份列表 - 需要单独获取
    this.fetchAndRenderBackups();
  }

  /**
   * 渲染统计卡片
   * @param {Object} data - 统计数据
   */
  renderStatCards(data) {
    // 后端返回字段：total_memories, status_breakdown, graph_nodes, atom_count
    const statusBreakdown = data.status_breakdown || {};

    document.getElementById("ss-total").textContent = data.total_memories || 0;
    document.getElementById("ss-active").textContent = statusBreakdown.active || 0;
    document.getElementById("ss-archived").textContent = statusBreakdown.archived || 0;
    document.getElementById("ss-deleted").textContent = statusBreakdown.deleted || 0;
    document.getElementById("ss-nodes").textContent = data.graph_nodes || 0;
    document.getElementById("ss-atoms").textContent = data.atom_count || 0;
  }

  /**
   * 渲染重要性分布图表
   * @param {Object} distribution - 分布数据 {"0-1": N, "1-2": N, ...}
   */
  renderImportanceChart(distribution) {
    const chartEl = document.getElementById("importance-chart");
    if (!chartEl) return;

    // 后端返回的是 {"0-1": 10, "1-2": 20, ...} 格式
    const bins = ["0-1", "1-2", "2-3", "3-4", "4-5", "5-6", "6-7", "7-8", "8-9", "9-10"];
    const values = bins.map(bin => distribution[bin] || 0);
    const maxValue = Math.max(...values, 1);

    let html = '';
    bins.forEach((bin, idx) => {
      const value = values[idx];
      const percentage = ((value / maxValue) * 100).toFixed(0);
      html += '<div class="bar-row">';
      html += '<span class="bar-row-label">' + bin + '</span>';
      html += '<div class="bar-row-track">';
      html += '<div class="bar-row-fill" style="width:' + percentage + '%"></div>';
      html += '</div>';
      html += '<span class="bar-row-value">' + value + '</span>';
      html += '</div>';
    });

    chartEl.innerHTML = html;
  }

  /**
   * 渲染 Atom 类型图表
   * @param {Object} types - 类型数据 {episodic: N, factual: N, ...}
   */
  renderAtomChart(types) {
    const chartEl = document.getElementById("atom-chart");
    if (!chartEl) return;

    // 后端返回 atom_breakdown
    const atomBreakdown = types || {};
    const entries = Object.entries(atomBreakdown);

    if (entries.length === 0) {
      chartEl.innerHTML = '<div class="bar-chart-empty">' + window.t("system.noAtoms") + '</div>';
      return;
    }

    const maxValue = Math.max(...entries.map(([_, count]) => count), 1);

    let html = '';
    entries.forEach(([type, count]) => {
      const percentage = ((count / maxValue) * 100).toFixed(0);
      html += '<div class="bar-row">';
      html += '<span class="bar-row-label" style="width:80px">' + atomLabel(type) + '</span>';
      html += '<div class="bar-row-track">';
      html += '<div class="bar-row-fill" style="width:' + percentage + '%"></div>';
      html += '</div>';
      html += '<span class="bar-row-value">' + count + '</span>';
      html += '</div>';
    });

    chartEl.innerHTML = html;
  }

  /**
   * 渲染柱状图项
   * @param {string} label - 标签
   * @param {number} value - 数值
   * @param {number} total - 总数
   * @param {string} className - CSS 类名
   * @returns {string} HTML 字符串
   */
  renderBarChartItem(label, value, total, className = "") {
    const percentage = ((value / total) * 100).toFixed(1);
    let html = '<div class="bar-chart-item">';
    html += '<div class="bar-chart-label">' + esc(label) + '</div>';
    html += '<div class="bar-chart-bar">';
    html += '<div class="bar-chart-fill ' + className + '" style="width:' + percentage + '%"></div>';
    html += '</div>';
    html += '<div class="bar-chart-value">' + value + ' (' + percentage + '%)</div>';
    html += '</div>';
    return html;
  }

  /**
   * 渲染活跃会话列表
   * @param {Array} sessions - 会话列表
   */
  renderSessionList(sessions) {
    const listEl = document.getElementById("session-list");
    if (!listEl) return;

    if (!sessions || sessions.length === 0) {
      listEl.innerHTML = '<div class="session-empty">' + window.t("system.noSessions") + '</div>';
      return;
    }

    let html = '';
    sessions.forEach(session => {
      const sessionId = session.session_id || "--";
      const messageCount = session.message_count || 0;
      const lastActive = session.last_active || "--";

      html += '<div class="session-item">';
      html += '<div class="session-item-header">';
      html += '<span class="session-item-id">' + esc(String(sessionId)) + '</span>';
      html += '<span class="session-item-count">' + messageCount + ' ' + window.t("system.messages") + '</span>';
      html += '</div>';
      if (lastActive !== "--") {
        html += '<div class="session-item-meta">' + window.t("system.lastActive") + ': ' + esc(lastActive) + '</div>';
      }
      html += '</div>';
    });

    listEl.innerHTML = html;
  }

  /**
   * 渲染备份列表
   * @param {Array} backups - 备份列表
   */
  renderBackupList(backups) {
    const listEl = document.getElementById("backup-list");
    if (!listEl) return;

    if (!backups || backups.length === 0) {
      listEl.innerHTML = '<div class="backup-empty">' + window.t("system.noBackups") + '</div>';
      return;
    }

    let html = '';
    backups.forEach(backup => {
      // 后端返回字段：name, directory, backup_timestamp, file_count, files_copied
      const name = backup.name || backup.directory || "Unknown";
      const timestamp = backup.backup_timestamp || "--";
      const fileCount = backup.file_count || backup.files_copied || 0;

      html += '<div class="backup-item">';
      html += '<div class="backup-item-name">' + esc(name) + '</div>';
      html += '<div class="backup-item-meta">';
      html += '<span>' + esc(timestamp) + '</span>';
      html += '<span>' + fileCount + ' ' + window.t("system.files") + '</span>';
      html += '</div>';
      html += '</div>';
    });

    listEl.innerHTML = html;
  }

  /**
   * 格式化文件大小
   * @param {number} bytes - 字节数
   * @returns {string} 格式化后的字符串
   */
  formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
  }

  /**
   * 获取并渲染备份列表
   */
  async fetchAndRenderBackups() {
    try {
      const data = await this.api.get("backups");
      this.renderBackupList(data.backups || []);
    } catch (e) {
      // 备份功能可能不可用，静默失败
      const listEl = document.getElementById("backup-list");
      if (listEl) {
        listEl.innerHTML = '<div class="backup-empty">' + window.t("common.unavailable") + '</div>';
      }
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
