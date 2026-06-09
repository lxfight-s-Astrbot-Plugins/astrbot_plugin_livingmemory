/**
 * API Client - 封装 AstrBot Bridge API 通信
 * 提供统一的 API 请求、错误处理和重试机制
 */

export class ApiClient {
  constructor() {
    this.bridge = window.AstrBotPluginPage;
    this._context = null;
  }

  /**
   * 等待 Bridge 就绪
   * @returns {Promise<Object>} Bridge 上下文
   */
  async ready() {
    if (!this.bridge) {
      throw new Error(window.t ? window.t("bridge.error") : "Bridge not available");
    }
    try {
      this._context = await this.bridge.ready();
      return this._context;
    } catch (e) {
      console.error("Bridge ready failed:", e);
      return {};
    }
  }

  /**
   * 获取当前上下文
   * @returns {Object} 上下文对象
   */
  getContext() {
    return this._context || this.bridge?.getContext() || {};
  }

  /**
   * 构建 endpoint 路径
   *
   * Bridge API 会自动添加插件前缀：
   * apiGet("stats") -> /api/plug/{plugin_name}/stats
   *
   * 后端注册路由格式：/{plugin_name}/page/stats
   * Bridge API 会处理插件前缀，前端只需要提供 "page/xxx" 部分
   *
   * @param {string} path - 原始路径（如 "stats", "memories"）
   * @returns {string} 带 page 前缀的路径（如 "page/stats"）
   */
  buildEndpoint(path) {
    const cleanPath = String(path).replace(/^\/+/, "");
    // 检查路径是否已经包含 page/ 前缀
    if (cleanPath.startsWith("page/")) {
      return cleanPath;
    }
    return "page/" + cleanPath.replace(/\/+/g, "/");
  }

  /**
   * 通用 API 请求（带重试）
   * @param {string} path - API 路径
   * @param {Object} options - 请求选项
   * @param {string} options.method - HTTP 方法（GET/POST）
   * @param {Object} options.body - 请求体（POST）
   * @param {number} options.retries - 重试次数
   * @returns {Promise<any>} API 响应
   */
  async request(path, options = {}) {
    const method = options.method || "GET";
    const body = options.body;
    const retries = options.retries || 2;

    if (!this.bridge) {
      throw new Error(window.t ? window.t("bridge.error") : "Bridge not available");
    }

    let lastError;
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        if (method === "GET") {
          // 解析 query string
          const qi = path.indexOf("?");
          if (qi !== -1) {
            const base = path.substring(0, qi);
            const qs = path.substring(qi + 1);
            const params = {};
            new URLSearchParams(qs).forEach((v, k) => { params[k] = v; });
            return await this.bridge.apiGet(this.buildEndpoint(base), params);
          }
          return await this.bridge.apiGet(this.buildEndpoint(path), {});
        }
        // POST
        return await this.bridge.apiPost(this.buildEndpoint(path), body || {});
      } catch (e) {
        lastError = e;
        if (attempt === retries) throw e;
        // 指数退避
        await new Promise(resolve => {
          setTimeout(resolve, Math.min(1000 * Math.pow(2, attempt), 5000));
        });
      }
    }
    throw lastError || new Error(window.t ? window.t("misc.requestFailed") : "Request failed");
  }

  /**
   * 解包 API 响应
   *
   * 后端返回格式：
   * - 成功：{status: "ok", data: {...}}
   * - 失败：{status: "error", message: "..."}
   *
   * Dashboard bridge unwraps standard plugin responses before returning them.
   *
   * @param {Object} response - API 响应
   * @returns {any} 解包后的数据
   */
  unwrapResponse(response) {
    if (response && response.status === "ok" && Object.prototype.hasOwnProperty.call(response, "data")) {
      return response.data;
    }
    if (response && response.status === "error") {
      throw new Error(response.message || (window.t ? window.t("misc.requestFailed") : "Request failed"));
    }
    return response || {};
  }

  /**
   * GET 请求
   * @param {string} path - API 路径
   * @param {Object} params - Query 参数
   * @returns {Promise<any>} 响应数据
   */
  async get(path, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const fullPath = qs ? `${path}?${qs}` : path;
    return this.unwrapResponse(await this.request(fullPath, { method: "GET" }));
  }

  /**
   * POST 请求
   * @param {string} path - API 路径
   * @param {Object} body - 请求体
   * @returns {Promise<any>} 响应数据
   */
  async post(path, body = {}) {
    return this.unwrapResponse(await this.request(path, { method: "POST", body }));
  }
}
