/**
 * Recall Page - 召回测试页面
 * 负责测试记忆召回功能
 */

import { esc, statusPill, normalizeImportance } from "./utils.js";

export class RecallPage {
  constructor(state, apiClient, peekPanel) {
    this.state = state;
    this.api = apiClient;
    this.peek = peekPanel;
  }

  /**
   * 初始化召回页面事件监听
   */
  initEventListeners() {
    const queryInput = document.getElementById("recall-query");
    const searchBtn = document.getElementById("recall-search-btn");
    const kSlider = document.getElementById("recall-k");
    const kValue = document.getElementById("recall-k-value");

    // k 值滑块
    if (kSlider && kValue) {
      kSlider.addEventListener("input", () => {
        kValue.textContent = kSlider.value;
      });
    }

    // 搜索按钮
    if (searchBtn) {
      searchBtn.addEventListener("click", () => this.runRecall());
    }

    // 回车搜索（Ctrl+Enter 或 Cmd+Enter）
    if (queryInput) {
      queryInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          this.runRecall();
        }
      });
    }
  }

  /**
   * 执行召回测试
   */
  async runRecall() {
    const query = document.getElementById("recall-query").value.trim();
    const k = parseInt(document.getElementById("recall-k").value) || 5;
    const sessionId = document.getElementById("recall-session").value.trim();

    if (!query) {
      this.showToast(window.t("recall.enterQuery"), true);
      return;
    }

    const searchBtn = document.getElementById("recall-search-btn");
    if (searchBtn) searchBtn.disabled = true;

    const startTime = Date.now();

    try {
      const params = { query, k };
      if (sessionId) params.session_id = sessionId;

      const data = await this.api.post("recall/test", params);
      const elapsed = Date.now() - startTime;

      this.state._recallCache = { data, elapsed };
      this.renderResults(data, elapsed);
    } catch (e) {
      this.showToast(e.message || window.t("recall.fail"), true);
      document.getElementById("recall-results").innerHTML = "";
      document.getElementById("recall-stats").classList.add("hidden");
      this.state._recallCache = null;
    } finally {
      if (searchBtn) searchBtn.disabled = false;
    }
  }

  /**
   * 渲染召回结果
   * @param {Object} data - 召回结果数据
   * @param {number} elapsed - 耗时（毫秒）
   */
  renderResults(data, elapsed) {
    // 后端返回 results，不是 memories
    const memories = data.results || data.memories || [];
    const count = memories.length;

    // 更新统计信息
    const statsEl = document.getElementById("recall-stats");
    const countText = document.getElementById("recall-count-text");
    const timeText = document.getElementById("recall-time-text");

    if (statsEl) statsEl.classList.remove("hidden");
    if (countText) {
      countText.textContent = count === 0
        ? window.t("recall.noMatch")
        : window.t("recall.resultsCount", count);
    }
    if (timeText) {
      const time = data.elapsed_time_ms || elapsed;
      timeText.textContent = window.t("recall.timeElapsed", (time / 1000).toFixed(2));
    }

    const resultsEl = document.getElementById("recall-results");
    if (!resultsEl) return;

    if (count === 0) {
      resultsEl.innerHTML = '<div class="table-empty">' + window.t("recall.noMatch") + '</div>';
      return;
    }

    let html = '<div class="recall-results-list">';

    memories.forEach((mem, idx) => {
      // 后端返回 memory_id 和 content
      const memoryId = mem.memory_id || mem.id;
      const content = mem.content || mem.text || mem.summary || "";
      // 后端返回 similarity_score，不是 score
      const score = mem.similarity_score != null ? Number(mem.similarity_score).toFixed(3) :
                    (mem.score != null ? Number(mem.score).toFixed(3) : "--");
      const importance = normalizeImportance(mem.metadata?.importance || 0.5).toFixed(1);
      const type = mem.metadata?.memory_type || "GENERAL";
      const status = mem.metadata?.status || "active";

      const scoreNum = Number(score);
      const scoreCls = scoreNum >= 0.75 ? "high" : scoreNum >= 0.45 ? "medium" : "low";

      html += '<div class="result-card recall-result-item" data-memory-id="' + memoryId + '">';
      html += '<div class="result-card-header recall-result-header">';
      html += '<span class="result-rank recall-result-rank">#' + (idx + 1) + '</span>';
      html += '<span class="cell-mono recall-result-id">ID: ' + memoryId + '</span>';
      html += '<span class="result-score-badge ' + scoreCls + ' recall-result-score">Score: ' + score + '</span>';
      html += statusPill(status);
      html += '<span class="type-tag">' + esc(type) + '</span>';
      html += '</div>';
      html += '<div class="result-content recall-result-content">' + esc(content) + '</div>';
      html += '<div class="recall-result-meta text-secondary">';
      html += '<span>' + window.t("detail.importance") + ': ' + importance + '/10</span>';
      if (mem.metadata?.session_id) {
        html += '<span>Session: ' + esc(String(mem.metadata.session_id)) + '</span>';
      }
      html += '</div>';
      html += '</div>';
    });

    html += '</div>';
    resultsEl.innerHTML = html;

    // 绑定点击事件
    resultsEl.querySelectorAll(".recall-result-item").forEach(item => {
      item.addEventListener("click", () => {
        const memoryId = item.dataset.memoryId;
        const memory = memories.find(m => String(m.memory_id || m.id) === memoryId);
        if (memory) {
          this.peek.renderMemory({
            memory_id: memory.memory_id || memory.id,
            summary: memory.content || memory.text || memory.summary,
            content: memory.content || memory.text,
            memory_type: memory.metadata?.memory_type,
            importance: memory.metadata?.importance,
            status: memory.metadata?.status,
            created_at: memory.metadata?.create_time
              ? new Date(memory.metadata.create_time * 1000).toLocaleString()
              : "--",
            raw: memory
          });
        }
      });
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
