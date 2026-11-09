/**
 * Utils - 纯工具函数集
 * 提供数据处理、UI 渲染相关的工具方法
 */

/**
 * 标准化重要性值（支持 0-1 和 0-10 两种输入）
 * @param {number} value - 输入值
 * @returns {number} 0-10 范围的重要性值
 */
export function normalizeImportance(value) {
  let n = Number(value);
  if (!Number.isFinite(n)) n = 0.5;
  if (n <= 1) n *= 10;
  return Math.min(10, Math.max(0, n));
}

/**
 * 从记忆详情对象中提取文本内容
 * @param {Object} detail - 记忆详情对象
 * @returns {string} 文本内容
 */
export function getDetailText(detail) {
  return detail.summary || detail.text || detail.content || "";
}

/**
 * HTML 转义，防止 XSS
 *
 * 必须转义引号：esc() 的输出会拼进 HTML 属性（如 value="..."、title="..."），
 * 只转义 & < > 时属性值中的双引号可 breakout 注入任意属性/事件处理器。
 * 同时避免每次调用分配 DOM 节点（虚拟滚动热路径）。
 * @param {string} text - 原始文本
 * @returns {string} 转义后的 HTML 安全文本（文本与属性上下文均安全）
 */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function esc(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ESC_MAP[ch]);
}

/**
 * 渲染状态标签 pill
 * @param {string} status - 状态值（active/archived/deleted）
 * @returns {string} HTML 字符串
 */
export function statusPill(status) {
  const label = statusLabel(status);
  const cls = ["active", "archived", "deleted"].includes(status) ? status : "active";
  return '<span class="status-pill ' + cls + '">' + esc(label) + '</span>';
}

/**
 * 获取状态的显示文本
 * @param {string} status - 状态值
 * @returns {string} 显示文本
 */
export function statusLabel(status) {
  if (status === "active") return window.t("status.active");
  if (status === "archived") return window.t("status.archived");
  if (status === "deleted") return window.t("status.deleted");
  return status || "--";
}

/**
 * 获取记忆类型的显示文本
 * @param {string} type - 类型值
 * @returns {string} 显示文本
 */
export function typeLabel(type) {
  const normalized = String(type || "").toUpperCase();
  if (normalized === "GENERAL") return window.t("type.general");
  if (normalized === "FACT") return window.t("type.fact");
  if (normalized === "FACTUAL") return window.t("type.factual");
  if (normalized === "PREFERENCE") return window.t("type.preference");
  if (normalized === "EVENT") return window.t("type.event");
  if (normalized === "EPISODIC") return window.t("type.episodic");
  if (normalized === "RELATIONAL") return window.t("type.relational");
  if (normalized === "PLANNED") return window.t("type.planned");
  if (normalized === "OPINION") return window.t("type.opinion");
  return type || "GENERAL";
}

/**
 * 获取记忆来源的显示文本
 *
 * 自动总结写入的记忆不带 memory_origin 标记，按缺省来源处理。
 * @param {string} origin - memory_origin 元数据值
 * @returns {string} 显示文本
 */
export function originLabel(origin) {
  const labels = {
    agent_memorize_tool: window.t("origin.agent"),
    memory_import: window.t("origin.import"),
    source_resummarization: window.t("origin.resummarize"),
  };
  return labels[origin] || window.t("origin.auto");
}

/**
 * 渲染图节点类型徽章
 * @param {string} type - 节点类型
 * @returns {string} HTML 字符串
 */
export function nodeBadge(type) {
  const normalized = String(type || "unknown").toLowerCase();
  return '<span class="peek-node-badge ' + esc(normalized) + '">' + esc(type || "Unknown") + '</span>';
}

/**
 * 渲染元数据项（label: value）
 * @param {string} label - 标签
 * @param {string} value - 值
 * @param {boolean} [valueIsHtml=false] - value 是否已是可信 HTML（调用方必须自行转义）
 * @returns {string} HTML 字符串
 */
export function metaItem(label, value, valueIsHtml = false) {
  const rendered = valueIsHtml ? value : esc(value);
  return '<div class="memory-detail-meta-item"><span class="memory-detail-meta-label">' + esc(label) + '</span><span class="memory-detail-meta-value">' + rendered + '</span></div>';
}

/**
 * 显示 Toast 提示（实际渲染由 dashboard 入口的 window.lmShowToast 完成）
 * @param {string} message - 提示消息
 * @param {boolean} isError - 是否为错误
 */
export function showToast(message, isError = false) {
  if (window.lmShowToast) window.lmShowToast(message, isError);
}

/**
 * 解包 Bridge API 响应
 *
 * 后端返回格式：
 * - 成功：{status: "ok", data: {...}}
 * - 失败：{status: "error", message: "..."}
 *
 * @param {Object} response - API 响应
 * @returns {any} 解包后的数据
 */
export function unwrapResponse(response) {
  if (response && response.status === "ok" && Object.prototype.hasOwnProperty.call(response, "data")) {
    return response.data;
  }
  if (response && response.status === "error") {
    if (response.code === "not_ready") {
      /* 初始化未完成：交给全局横幅展示指引（重试/自动恢复），调用方仍收到错误。 */
      if (typeof window !== "undefined" && typeof window.lmReportPluginNotReady === "function") {
        window.lmReportPluginNotReady(response);
      }
      const notReadyError = new Error(response.message || window.t("misc.requestFailed"));
      notReadyError.code = "not_ready";
      throw notReadyError;
    }
    throw new Error(response.message || window.t("misc.requestFailed"));
  }
  return response || {};
}

/**
 * 序列化容器内可编辑控件的当前值，作为编辑态脏检查基线
 * @param {string} selector - 控件选择器
 * @returns {string} 序列化后的快照
 */
export function captureFormSnapshot(selector) {
  return JSON.stringify(Array.from(document.querySelectorAll(selector), input => input.value));
}

/**
 * 判断编辑表单相对快照是否有未保存修改
 * @param {string} selector - 控件选择器
 * @param {string} snapshot - captureFormSnapshot 生成的快照
 * @returns {boolean} 是否有未保存修改
 */
export function hasFormChanges(selector, snapshot) {
  return captureFormSnapshot(selector) !== snapshot;
}

/**
 * 有未保存修改时先确认是否丢弃；忙碌中直接拒绝离开
 * @param {boolean} busy - 是否正在保存
 * @param {boolean} hasUnsavedChanges - 是否存在未保存修改
 * @returns {Promise<boolean>} 是否允许离开
 */
export async function canDiscardChanges(busy, hasUnsavedChanges) {
  if (busy) {
    showToast(window.t("flow.waitForSave"));
    return false;
  }
  return !hasUnsavedChanges || await confirmDiscardChanges();
}

let discardRequest;

export function confirmDiscardChanges() {
  const dialog = document.getElementById("discard-dialog");
  if (discardRequest) {
    // Native close events are queued; a new action after dismissal needs a
    // fresh decision, rather than inheriting the previous cancelled result.
    return dialog.open ? discardRequest : discardRequest.then(() => confirmDiscardChanges());
  }
  discardRequest = new Promise(resolve => {
    dialog.returnValue = "cancel";
    dialog.addEventListener("close", () => {
      const discarded = dialog.returnValue === "discard";
      discardRequest = null;
      resolve(discarded);
    }, { once: true });
    dialog.showModal();
  });
  return discardRequest;
}

/**
 * 页面内通用确认框（原生 <dialog>，宿主 iframe 没有 allow-modals，不能用 window.confirm）。
 *
 * @param {Object} options
 * @param {string} options.title - 标题
 * @param {string} options.message - 正文（可含换行）
 * @param {string} [options.confirmLabel] - 确认按钮文案，默认 common.confirm
 * @param {boolean} [options.destructive] - true 时确认按钮用危险色
 * @returns {Promise<boolean>} 用户是否确认
 */
export function confirmAction(options = {}) {
  const dialog = document.getElementById("action-dialog");
  if (!dialog) return Promise.resolve(false);
  const titleEl = document.getElementById("action-dialog-title");
  const messageEl = document.getElementById("action-dialog-message");
  const confirmBtn = document.getElementById("action-dialog-confirm");
  if (!titleEl || !messageEl || !confirmBtn) return Promise.resolve(false);
  const previousFocus = document.activeElement || null;
  titleEl.textContent = options.title || "";
  messageEl.textContent = options.message || "";
  confirmBtn.textContent = options.confirmLabel || window.t("common.confirm");
  confirmBtn.classList.toggle("btn-danger", options.destructive !== false);
  confirmBtn.classList.toggle("btn-primary", options.destructive === false);
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "confirm");
      if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
    }, { once: true });
    dialog.showModal();
  });
}

/**
 * 获取 Atom 类型的显示文本
 * @param {string} type - Atom 类型
 * @returns {string} 显示文本
 */
export function atomLabel(type) {
  const normalized = String(type || "").toLowerCase();
  const labels = {
    factual: window.t("system.atomFactual"),
    episodic: window.t("system.atomEpisodic"),
    relational: window.t("system.atomRelational"),
    planned: window.t("system.atomPlanned"),
    entity: window.t("atom.entity"),
    event: window.t("atom.event"),
    preference: window.t("atom.preference"),
    topic: window.t("atom.topic"),
  };
  return labels[normalized] || type || "--";
}
