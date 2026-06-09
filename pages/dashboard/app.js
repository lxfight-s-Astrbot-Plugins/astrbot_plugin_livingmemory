/**
 * LivingMemory Dashboard - 主入口
 * 使用模块化架构，保持主文件简洁清晰
 */

import {
  ApiClient,
  PeekPanel,
  MemoryPage,
  RecallPage,
  SystemPage,
  esc,
  statusPill,
  nodeBadge,
} from "./modules/index.js";

(() => {
  "use strict";

  /* ================================================================
     State
     ================================================================ */
  const state = {
    page: "graph",
    memory: {
      items: [],
      total: 0,
      page: 1,
      pageSize: 20,
      hasMore: false,
      keyword: "",
      session: "",
      status: "all",
      type: "all",
      sort: "created_desc",
    },
    selectedMemory: null,
    isEditing: false,
    _detailCache: null,
    _nodeDetailCache: null,
    _recallCache: null,
    _systemCache: null,
    pendingSearch: null,
  };

  /* ================================================================
     Initialize Modules
     ================================================================ */
  const api = new ApiClient();
  const peekPanel = new PeekPanel(state, api);
  const memoryPage = new MemoryPage(state, api, peekPanel);
  const recallPage = new RecallPage(state, api, peekPanel);
  const systemPage = new SystemPage(state, api);

  /* ================================================================
     Theme Management
     ================================================================ */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const darkIcon = document.getElementById("theme-icon-dark");
    const lightIcon = document.getElementById("theme-icon-light");
    if (darkIcon && lightIcon) {
      darkIcon.classList.toggle("hidden", theme === "light");
      lightIcon.classList.toggle("hidden", theme === "dark");
    }
  }

  function getInitialTheme(context) {
    if (context && typeof context.isDark === "boolean") {
      return context.isDark ? "dark" : "light";
    }

    try {
      const saved = localStorage.getItem("lmem_theme");
      if (saved === "dark" || saved === "light") {
        return saved;
      }
    } catch (e) {
      console.warn("[LM] Failed to read theme from localStorage:", e);
    }

    return "light";
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "light" ? "dark" : "light";

    try {
      localStorage.setItem("lmem_theme", next);
    } catch (e) {
      console.warn("[LM] Failed to save theme to localStorage:", e);
    }

    applyTheme(next);
    showToast(window.t(next === "dark" ? "theme.darkToast" : "theme.lightToast"));
  }

  /* ================================================================
     Toast Notification
     ================================================================ */
  let toastTimer;
  function showToast(msg, isError = false) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("visible", "error");
    if (isError) el.classList.add("error");
    void el.offsetWidth;
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("visible");
    }, 2500);
  }

  /* ================================================================
     Sidebar / Routing
     ================================================================ */
  function switchPage(name) {
    state.page = name;

    document.querySelectorAll(".nav-item[data-page]").forEach(item => {
      item.classList.toggle("active", item.dataset.page === name);
    });

    document.querySelectorAll(".page").forEach(p => {
      p.classList.toggle("active", p.id === "page-" + name);
    });

    if (name === "graph") {
      fetchGraphStats();
      if (window.ensureGraphScene) window.ensureGraphScene();
    }
    if (name === "memory") memoryPage.fetch();
    if (name === "recall") { /* 召回页面按需加载 */ }
    if (name === "system") systemPage.fetch();
  }

  function normalizeLocale(locale) {
    const lang = String(locale || "").split("-")[0];
    return ["zh", "en", "ru"].includes(lang) ? lang : "zh";
  }

  function getCurrentLanguage() {
    if (typeof window.getLanguage === "function") {
      return window.getLanguage();
    }
    return normalizeLocale(api.bridge?.getLocale?.());
  }

  function initSidebar() {
    document.querySelectorAll(".nav-item[data-page]").forEach(item => {
      item.addEventListener("click", () => {
        switchPage(item.dataset.page);
      });
    });

    document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

    const langMenu = document.getElementById("lang-menu");
    document.querySelectorAll(".lang-option[data-lang]").forEach(option => {
      option.addEventListener("click", () => {
        const lang = option.dataset.lang;
        if (!lang) return;

        if (typeof window.setLanguage === "function") {
          window.setLanguage(lang, { persist: true, source: "user" });
        } else {
          try {
            localStorage.setItem("lmem_lang", lang);
          } catch (e) {
            console.warn("[LM] Failed to save language to localStorage:", e);
          }
        }

        if (langMenu) {
          langMenu.removeAttribute("open");
        }
        if (typeof window.setLanguage !== "function") {
          refreshDynamicI18n();
        }
        showToast(window.t("language.toast", option.textContent.trim()));
      });
    });

    if (langMenu) {
      langMenu.addEventListener("toggle", (e) => {
        if (e.newState === "open") {
          updateLanguageMenu();
        }
      });
    }
  }

  function updateLanguageMenu() {
    const currentLang = getCurrentLanguage();

    document.querySelectorAll(".lang-option[data-lang]").forEach(option => {
      const active = option.dataset.lang === currentLang;
      option.classList.toggle("active", active);
      option.setAttribute("aria-current", active ? "true" : "false");
    });
  }

  function refreshDynamicI18n() {
    updateLanguageMenu();

    if (state.page === "memory") {
      memoryPage.renderVirtual();
      memoryPage.updatePagination();
    }
    if (state.page === "recall" && state._recallCache) {
      recallPage.renderResults(state._recallCache.data, state._recallCache.elapsed);
    }
    if (state.page === "system" && state._systemCache) {
      systemPage.render(state._systemCache.data);
    }

    const peekPanelEl = document.getElementById("peek-panel");
    const peekVisible = peekPanelEl && peekPanelEl.classList.contains("visible");
    if (peekVisible && !state.isEditing) {
      if (state._detailCache) {
        peekPanel.renderDetailView(state._detailCache);
      } else if (state._nodeDetailCache) {
        peekPanel.renderNode(state._nodeDetailCache);
      }
    }
  }

  /* ================================================================
     Graph Page (依赖 graph-ui.js)
     ================================================================ */
  async function fetchGraphStats() {
    try {
      const data = await api.get("stats");

      document.getElementById("gs-total").textContent = data.total_memories || 0;
      document.getElementById("gs-nodes").textContent = data.graph_nodes || 0;
      document.getElementById("gs-edges").textContent = data.graph_edges || 0;

      const sessions = data.sessions || {};
      const sessionCount = typeof sessions === "object" ? Object.keys(sessions).length : 0;
      document.getElementById("gs-sessions").textContent = sessionCount;
    } catch (e) {
      showToast(e.message || window.t("misc.statsFail"), true);
    }
  }

  /* ================================================================
     Initialization
     ================================================================ */
  async function init() {
    const context = await api.ready();

    if (api.bridge && typeof api.bridge.onContext === "function") {
      api.bridge.onContext((ctx) => {
        if (ctx && typeof ctx.isDark === "boolean") {
          const newTheme = ctx.isDark ? "dark" : "light";
          const currentTheme = document.documentElement.getAttribute("data-theme") || "light";
          if (newTheme !== currentTheme) {
            applyTheme(newTheme);
          }
        }

        if (ctx && ctx.locale) {
          updateLanguageMenu();
        }
      });
    }

    const initialTheme = getInitialTheme(context);
    applyTheme(initialTheme);

    initSidebar();

    memoryPage.initEventListeners();
    recallPage.initEventListeners();

    document.getElementById("peek-close").addEventListener("click", () => peekPanel.close());
    document.getElementById("peek-overlay").addEventListener("click", () => peekPanel.close());

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        peekPanel.close();
      }
    });
    window.addEventListener("languagechange", refreshDynamicI18n);

    fetchGraphStats();
    switchPage("graph");
  }

  /* ================================================================
     Global Exports (for graph-ui.js and other dependencies)
     ================================================================ */
  window.lmState = state;
  window.lmShowToast = showToast;
  window.lmApiRequest = (path, options) => {
    // 兼容旧 API，转发到 ApiClient
    if (options && options.method === "POST") {
      return api.request(path, options);
    }
    return api.request(path, options || {});
  };
  window.lmOpenPeekNode = (nodeData) => peekPanel.renderNode(nodeData);
  window.lmOpenPeekMemory = (memory) => peekPanel.renderMemory(memory);
  window.lmClosePeek = () => peekPanel.close();
  window.lmFetchGraphStats = fetchGraphStats;
  window.lmRefreshMemories = () => memoryPage.fetch();
  window.lmEsc = esc;
  window.lmStatusPill = statusPill;
  window.lmNodeBadge = nodeBadge;

  // 图谱小视图绘制函数（如果需要）
  window.lmDrawMiniGraph = (canvas, nodes, edges) => {
    if (!canvas || !nodes || !nodes.length) return;

    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);

    // 简单布局算法
    const positions = nodes.map((node, i) => {
      const angle = (i / nodes.length) * Math.PI * 2;
      const r = Math.min(W, H) * 0.3;
      return {
        x: W / 2 + r * Math.cos(angle),
        y: H / 2 + r * Math.sin(angle),
        node
      };
    });

    // 绘制边
    if (edges && edges.length) {
      ctx.strokeStyle = "rgba(100, 100, 100, 0.3)";
      ctx.lineWidth = 1;
      edges.forEach(edge => {
        const source = positions.find(p => p.node.id === edge.source || p.node.id === edge.from);
        const target = positions.find(p => p.node.id === edge.target || p.node.id === edge.to);
        if (source && target) {
          ctx.beginPath();
          ctx.moveTo(source.x, source.y);
          ctx.lineTo(target.x, target.y);
          ctx.stroke();
        }
      });
    }

    // 绘制节点
    positions.forEach(pos => {
      ctx.fillStyle = "#4a90e2";
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, 4, 0, Math.PI * 2);
      ctx.fill();
    });
  };

  // 启动应用
  init();
})();
