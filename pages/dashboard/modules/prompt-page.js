/**
 * Prompt Page - 提示词管理页面
 * 集中管理插件所有可自定义的提示词模板
 */

import { esc } from "./utils.js";

export class PromptPage {
  constructor(state, apiClient) {
    this.state = state;
    this.api = apiClient;
    this.prompts = [];
    this.categories = [];
    this.editingId = null;
    this.editContent = null;
    this._resetMode = false;
  }

  /**
   * 按当前语言选择中/英文文案
   * 非中文语言优先英文字段，缺失时回退中文
   * @param {string} zhText - 中文字段
   * @param {string} enText - 英文字段
   * @returns {string}
   */
  _pickLang(zhText, enText) {
    const lang = window.getLanguage ? window.getLanguage() : "zh";
    if (lang === "zh") return zhText || enText || "";
    return enText || zhText || "";
  }

  /**
   * 获取提示词列表
   */
  async fetch() {
    try {
      const data = await this.api.get("prompts");
      this.prompts = data.prompts || [];
      this.categories = data.categories || [];
      this.render();
    } catch (e) {
      this.showToast(e.message || window.t("prompt.fetchFailed"), true);
    }
  }

  /**
   * 渲染主页面
   */
  render() {
    const container = document.getElementById("prompt-content");
    if (!container) return;

    if (!this.prompts.length) {
      container.innerHTML =
        '<div class="table-empty">' + window.t("prompt.noPrompts") + "</div>";
      return;
    }

    // 按分类分组
    const grouped = {};
    this.prompts.forEach((p) => {
      const cat = p.category || "other";
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(p);
    });

    let html = "";

    // 顶部警告
    html +=
      '<div class="prompt-warning">' +
      '<div class="prompt-warning-title">' +
      esc(window.t("prompt.warningTitle")) +
      "</div>" +
      '<div class="prompt-warning-text">' +
      esc(window.t("prompt.warningText")) +
      "</div></div>";

    for (const [catId, prompts] of Object.entries(grouped)) {
      const catInfo = this.categories.find((c) => c.id === catId) || {};
      const catName = this._pickLang(catInfo.name, catInfo.name_en) || catId;
      // 中文界面附英文副标题，其他语言只显示当前语言名称
      const catNameAlt =
        (window.getLanguage ? window.getLanguage() : "zh") === "zh"
          ? catInfo.name_en || ""
          : "";
      const catDesc = this._pickLang(catInfo.description, catInfo.description_en);

      html += '<div class="prompt-category">';
      html +=
        '<div class="prompt-category-header">' +
        '<span class="prompt-category-name">' +
        esc(catName) +
        "</span>";
      if (catNameAlt)
        html +=
          ' <span class="prompt-category-name-en">' + esc(catNameAlt) + "</span>";
      if (catDesc)
        html +=
          '<p class="prompt-category-desc">' + esc(catDesc) + "</p>";
      html += "</div>";

      html += '<div class="prompt-list">';
      prompts.forEach((p) => {
        const customBadge = p.is_custom
          ? ' <span class="badge badge-custom">' +
            window.t("prompt.customized") +
            "</span>"
          : "";
        const jsonBadge =
          p.category === "memory_processing"
            ? ' <span class="badge badge-json-warn" title="' +
              esc(window.t("prompt.jsonRequired")) +
              '">JSON</span>'
            : "";
        const varList = (p.variables || [])
          .map((v) => '<code>' + esc(v) + "</code>")
          .join(" ");
        const descText = this._pickLang(p.description, p.description_en);

        html +=
          '<div class="prompt-item" data-id="' +
          esc(p.id) +
          '">' +
          '<div class="prompt-item-header">' +
          '<span class="prompt-item-name">' +
          esc(this._pickLang(p.name, p.name_en) || p.id) +
          jsonBadge +
          customBadge +
          "</span>";
        const nameAlt =
          (window.getLanguage ? window.getLanguage() : "zh") === "zh"
            ? p.name_en || ""
            : "";
        if (nameAlt)
          html +=
            '<span class="prompt-item-name-en">' + esc(nameAlt) + "</span>";
        html += "</div>";
        if (descText)
          html +=
            '<p class="prompt-item-desc">' + esc(descText) + "</p>";
        const usageNote = this._pickLang(p.usage_note, p.usage_note_en);
        if (usageNote)
          html +=
            '<p class="prompt-item-usage">' + esc(usageNote) + "</p>";
        if (varList)
          html +=
            '<div class="prompt-item-vars">' +
            window.t("prompt.variables") +
            ": " +
            varList +
            "</div>";
        html +=
          '<div class="prompt-item-actions">' +
          '<button class="btn btn-sm btn-secondary prompt-edit-btn" data-id="' +
          esc(p.id) +
          '"><i data-lucide="square-pen" aria-hidden="true"></i><span>' +
          window.t("prompt.edit") +
          "</span></button>" +
          "</div>";
        html += "</div>";
      });
      html += "</div></div>";
    }

    container.innerHTML = html;
    if (window.lmHydrateIcons) window.lmHydrateIcons();

    // 绑定编辑按钮事件
    container.querySelectorAll(".prompt-edit-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        this.openEditor(id);
      });
    });
  }

  /**
   * 打开编辑器
   * @param {string} promptId - 提示词ID
   */
  async openEditor(promptId) {
    const prompt = this.prompts.find((p) => p.id === promptId);
    if (!prompt) return;

    try {
      const detail = await this.api.get("prompts/detail", { id: promptId });
      this.editingId = promptId;
      this.editContent = detail.content || "";

      const editorEl = document.getElementById("prompt-editor");
      if (!editorEl) return;

      document.getElementById("prompt-editor-title").textContent =
        this._pickLang(prompt.name, prompt.name_en) || prompt.id;
      document.getElementById("prompt-editor-textarea").value =
        this.editContent;
      document.getElementById("prompt-editor-vars").innerHTML = (
        detail.variables || []
      )
        .map(
          (v) =>
            '<code class="prompt-var-tag">' + esc(v) + "</code>"
        )
        .join(" ");
      document.getElementById("prompt-editor-status").textContent = detail
        .is_custom
        ? " " + window.t("prompt.customizedStatus")
        : " " + window.t("prompt.defaultStatus");

      editorEl.classList.remove("hidden");

      // 绑定按钮事件
      const saveBtn = document.getElementById("prompt-save-btn");
      const resetBtn = document.getElementById("prompt-reset-btn");
      const cancelBtn = document.getElementById("prompt-cancel-btn");
      const textarea = document.getElementById("prompt-editor-textarea");

      const newSaveBtn = saveBtn.cloneNode(true);
      const newResetBtn = resetBtn.cloneNode(true);
      const newCancelBtn = cancelBtn.cloneNode(true);
      saveBtn.parentNode.replaceChild(newSaveBtn, saveBtn);
      resetBtn.parentNode.replaceChild(newResetBtn, resetBtn);
      cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);

      newSaveBtn.addEventListener("click", () => this.savePrompt());
      newResetBtn.addEventListener("click", () => this.resetPrompt());
      newCancelBtn.addEventListener("click", () => this.closeEditor());

      // 检测修改
      textarea.addEventListener("input", () => {
        // 手动编辑后退出恢复默认模式
        if (this._resetMode) {
          this._resetMode = false;
        }
        const modified = textarea.value !== this.editContent;
        newSaveBtn.disabled = !modified;
      });
      newSaveBtn.disabled = true;
      this._resetMode = false;

      editorEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      this.showToast(e.message || window.t("prompt.loadFailed"), true);
    }
  }

  /**
   * 保存提示词
   */
  async savePrompt() {
    const textarea = document.getElementById("prompt-editor-textarea");
    if (!textarea) return;
    const content = textarea.value;

    const saveBtn = document.getElementById("prompt-save-btn");
    if (saveBtn) saveBtn.disabled = true;

    try {
      if (this._resetMode) {
        await this.api.post("prompts/reset", { id: this.editingId });
        this._resetMode = false;
        this.showToast(window.t("prompt.resetDone"));
      } else {
        await this.api.post("prompts/update", {
          id: this.editingId,
          content: content,
        });
        this.showToast(window.t("prompt.saved"));
      }
      this.editContent = content;
      await this.fetch();
      this.closeEditor();
    } catch (e) {
      console.error("[PromptPage] savePrompt failed:", e);
      this.showToast(e.message || window.t("prompt.saveFailed"), true);
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  /**
   * 填充默认内容到编辑器（不保存，需手动点保存）
   */
  async resetPrompt() {
    if (!this.editingId) {
      console.error("[PromptPage] resetPrompt: editingId is empty");
      return;
    }

    const resetBtn = document.getElementById("prompt-reset-btn");
    if (resetBtn) resetBtn.disabled = true;

    try {
      const result = await this.api.get("prompts/default", {
        id: this.editingId,
      });
      const newContent = result.content || "";
      const textarea = document.getElementById("prompt-editor-textarea");
      if (textarea) {
        textarea.value = newContent;
      }
      this._resetMode = true;
      const saveBtn = document.getElementById("prompt-save-btn");
      if (saveBtn) saveBtn.disabled = false;
      const statusEl = document.getElementById("prompt-editor-status");
      if (statusEl)
        statusEl.textContent = " " + window.t("prompt.defaultFilledStatus");
    } catch (e) {
      console.error("[PromptPage] resetPrompt failed:", e);
      this.showToast(e.message || window.t("prompt.resetFailed"), true);
    } finally {
      if (resetBtn) resetBtn.disabled = false;
    }
  }

  /**
   * 关闭编辑器
   */
  closeEditor() {
    const editorEl = document.getElementById("prompt-editor");
    if (editorEl) editorEl.classList.add("hidden");
    this.editingId = null;
    this.editContent = null;
  }

  /**
   * 语言切换后刷新已打开编辑器的标题
   */
  refreshEditorTitle() {
    if (!this.editingId) return;
    const prompt = this.prompts.find((p) => p.id === this.editingId);
    const titleEl = document.getElementById("prompt-editor-title");
    if (prompt && titleEl) {
      titleEl.textContent = this._pickLang(prompt.name, prompt.name_en) || prompt.id;
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
