/**
 * Not-Ready Banner - 插件初始化/Provider 未就绪时的全局指引条。
 *
 * 读接口在后端改为非阻塞后，未就绪会立即返回 ``code: "not_ready"``；
 * 本模块负责展示横幅、提供重试，并在初始化完成后自动收起：轮询
 * ``page/readiness``（不做初始化等待），就绪时隐藏横幅并通知调用方刷新当前页。
 */

const POLL_INTERVAL_MS = 2000;

export class NotReadyBanner {
  constructor(apiClient, options = {}) {
    this.api = apiClient;
    this.onReady = typeof options.onReady === "function" ? options.onReady : null;
    this.element = null;
    this.messageEl = null;
    this.retryBtn = null;
    this.pollTimer = null;
    this.visible = false;
    this._bound = false;
  }

  init() {
    if (this._bound) return;
    this.element = document.getElementById("not-ready-banner");
    this.messageEl = document.getElementById("not-ready-banner-message");
    this.retryBtn = document.getElementById("not-ready-retry");
    if (this.retryBtn) {
      this.retryBtn.addEventListener("click", () => this.retry());
    }
    this._bound = true;
  }

  show(response) {
    this.visible = true;
    if (!this.element) return;
    this.element.hidden = false;
    this.element.setAttribute("title", (response && response.message) || "");
    if (this.messageEl) {
      const serverMessage = response && response.message ? " " + response.message : "";
      this.messageEl.textContent = window.t("notReady.message") + serverMessage;
    }
    this.startPolling();
  }

  hide() {
    this.visible = false;
    this.stopPolling();
    if (!this.element) return;
    this.element.hidden = true;
    this.element.removeAttribute("title");
  }

  async retry() {
    const readiness = await this.check();
    if (readiness && readiness.ready) {
      this.handleReady();
    } else if (readiness) {
      this.show({ message: readiness.message, init_state: readiness.state });
    }
  }

  async check() {
    try {
      return await this.api.get("readiness");
    } catch (_error) {
      return null;
    }
  }

  startPolling() {
    if (this.pollTimer !== null) return;
    this.pollTimer = setInterval(async () => {
      const readiness = await this.check();
      if (readiness && readiness.ready) this.handleReady();
    }, POLL_INTERVAL_MS);
  }

  stopPolling() {
    if (this.pollTimer === null) return;
    clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  handleReady() {
    if (!this.visible && !this.element) return;
    this.hide();
    if (this.onReady) this.onReady();
  }
}
