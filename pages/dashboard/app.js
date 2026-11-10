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
  PromptPage,
  SettingsPage,
  NotReadyBanner,
  esc,
  unwrapResponse,
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
      selectedIds: new Set(),
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
  const notReadyBanner = new NotReadyBanner(api, { onReady: () => refreshCurrentPage() });
  /* utils.unwrapResponse 在所有 Page API 响应上调用该钩子（含 graph-ui 直连请求）。 */
  window.lmReportPluginNotReady = (response) => notReadyBanner.show(response);
  const peekPanel = new PeekPanel(state, api);
  const memoryPage = new MemoryPage(state, api, peekPanel);
  const recallPage = new RecallPage(state, api, peekPanel);
  const systemPage = new SystemPage(state, api);
  const promptPage = new PromptPage(state, api);
  const settingsPage = new SettingsPage(state, api);

  /* 懒加载页面共用加载器：首次进入才 import 对应 chunk，失败提示后可重试。 */
  const lazyPages = {};
  const lazyPageLoads = {};
  function ensureLazyPage(name, file, className) {
    if (lazyPages[name]) return Promise.resolve(lazyPages[name]);
    if (!lazyPageLoads[name]) {
      const moduleUrl = new URL("./assets/" + file, document.baseURI).href;
      lazyPageLoads[name] = import(moduleUrl)
        .then((module) => {
          lazyPages[name] = new module[className](state, api);
          lazyPages[name].init();
          return lazyPages[name];
        })
        .catch((error) => {
          showToast(error.message || window.t("misc.requestFailed"), true);
          return null;
        })
        .finally(() => {
          lazyPageLoads[name] = null;
        });
    }
    return lazyPageLoads[name];
  }
  const ensureProfilePage = () => ensureLazyPage("profile", "profile.min.js", "ProfilePage");
  const ensureDiagnosticsPage = () =>
    ensureLazyPage("diagnostics", "diagnostics.min.js", "DiagnosticsPage");

  function hydrateIcons() {
    if (!window.lucide || typeof window.lucide.createIcons !== "function") return;
    window.lucide.createIcons({
      attrs: {
        "stroke-width": 1.7,
        "aria-hidden": "true",
      },
    });
  }

  /* ================================================================
     Toast Notification
     ================================================================ */
  let toastTimer;
  function showToast(msg, isError = false) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.toggle("error", isError);
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("visible");
    }, 3200);
  }

  /* ================================================================
     Sidebar / Routing
     ================================================================ */
  let navigationGeneration = 0;
  let pageInitialized = false;
  async function switchPage(name) {
    if (pageInitialized && name === state.page) return;
    if (memoryPage._bulkBusy || memoryPage._transferBusy) {
      showToast(window.t("flow.waitForSave"));
      return;
    }
    if (state.page === "settings" && !await settingsPage.canLeave()) return;
    if (state.page === "profile" && lazyPages.profile && !await lazyPages.profile.canLeave()) return;
    const generation = ++navigationGeneration;
    if (state.page === "prompts" && !await promptPage.canLeave()) return;
    if (!await peekPanel.requestClose() || generation !== navigationGeneration) return;
    if (state.page === "prompts") await promptPage.closeEditor({ force: true });
    pageInitialized = true;
    state.page = name;

    document.querySelectorAll(".nav-item[data-page]").forEach(item => {
      item.classList.toggle("active", item.dataset.page === name);
      if (item.dataset.page === name) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });

    document.querySelectorAll(".page").forEach(p => {
      p.classList.toggle("active", p.id === "page-" + name);
    });

    if (name === "graph") {
      fetchGraphStats();
      if (window.ensureGraphScene) window.ensureGraphScene();
    }
    window.lmSetGraphActive(name === "graph");
    if (name === "memory") memoryPage.fetch();
    if (name === "recall") { /* 召回页面按需加载 */ }
    if (name === "system") systemPage.fetch();
    if (name === "prompts") promptPage.fetch();
    if (name === "settings") settingsPage.fetch();
    if (name === "profile") {
      const page = await ensureProfilePage();
      if (page) await page.fetch();
    }
    if (name === "diagnostics") {
      const page = await ensureDiagnosticsPage();
      if (page) await page.fetch();
    }
  }

  /* 初始化完成后刷新当前页数据（横幅自动收起时调用）。 */
  function refreshCurrentPage() {
    if (state.page === "graph") {
      fetchGraphStats();
      if (window.ensureGraphScene) window.ensureGraphScene();
    } else if (state.page === "memory") {
      memoryPage.fetch();
    } else if (state.page === "system") {
      systemPage.fetch();
    } else if (state.page === "prompts") {
      promptPage.fetch();
    } else if (state.page === "settings") {
      settingsPage.fetch();
    } else if (state.page === "profile" && lazyPages.profile) {
      lazyPages.profile.render();
    } else if (state.page === "diagnostics" && lazyPages.diagnostics) {
      lazyPages.diagnostics.fetch();
    }
  }

  function normalizeLocale(locale) {
    const lang = String(locale || "").split("-")[0];
    return ["zh", "en", "ru", "ja"].includes(lang) ? lang : "zh";
  }

  function getCurrentLanguage() {
    if (typeof window.getLanguage === "function") {
      return window.getLanguage();
    }
    return normalizeLocale(api.bridge?.getLocale?.());
  }

  function initSidebar() {
    const toggle = document.getElementById("sidebar-toggle");
    function setMenuOpen(open) {
      toggle.setAttribute("aria-expanded", String(open));
    }
    toggle.addEventListener("click", () => setMenuOpen(toggle.getAttribute("aria-expanded") === "false"));
    toggle.closest(".sidebar").addEventListener("keydown", (event) => {
      if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
        event.stopPropagation();
        setMenuOpen(false);
        toggle.focus();
      }
    });
    document.querySelectorAll(".nav-item[data-page]").forEach(item => {
      item.addEventListener("click", () => {
        if (toggle.offsetWidth) {
          setMenuOpen(false);
          toggle.focus();
        }
        switchPage(item.dataset.page);
      });
    });

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
    if (state.page === "prompts" && promptPage.prompts.length) {
      promptPage.render();
      promptPage.refreshEditorTitle();
    }
    if (state.page === "settings") settingsPage.fetch();
    if (state.page === "profile" && lazyPages.profile) lazyPages.profile.render();
    if (state.page === "diagnostics" && lazyPages.diagnostics) lazyPages.diagnostics.render();

    const peekPanelEl = document.getElementById("peek-panel");
    const peekVisible = peekPanelEl && peekPanelEl.classList.contains("visible");
    if (peekVisible && !state.isEditing && !peekPanel._confirmResolve && !peekPanel._batchEditResolve) {
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
      const sessionIds = typeof sessions === "object" ? Object.keys(sessions) : [];
      document.getElementById("gs-sessions").textContent = sessionIds.length;

      const sessionOptions = document.getElementById("graph-session-options");
      if (sessionOptions) {
        sessionOptions.innerHTML = sessionIds
          .map(id => '<option value="' + esc(String(id)) + '"></option>')
          .join("");
      }
      /* /stats 依赖运行时组件：成功即代表插件已就绪，可立即收起横幅。 */
      notReadyBanner.hide();
    } catch (e) {
      showToast(e.message || window.t("misc.statsFail"), true);
    }
  }

  /* ================================================================
     Initialization
     ================================================================ */
  async function init() {
    hydrateIcons();
    notReadyBanner.init();
    window.LMAppearance.init();
    const context = await api.ready();

    if (api.bridge && typeof api.bridge.onContext === "function") {
      api.bridge.onContext((ctx) => {
        window.LMAppearance.setContext(ctx);

        if (ctx && ctx.locale) {
          updateLanguageMenu();
        }
      });
    }

    window.LMAppearance.setContext(context);

    initSidebar();

    memoryPage.initEventListeners();
    recallPage.initEventListeners();
    systemPage.initEventListeners();
    document.getElementById("prompt-retry")?.addEventListener("click", () => promptPage.fetch());

    document.getElementById("peek-close").addEventListener("click", () => peekPanel.requestClose());
    document.getElementById("peek-overlay").addEventListener("click", () => peekPanel.requestClose());

    document.addEventListener("keydown", async (e) => {
      if (e.key === "Escape" && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        if (!await peekPanel.handleEscape() && state.page === "prompts" && promptPage.editingId) {
          promptPage.closeEditor();
        }
      }
    });
    window.addEventListener("beforeunload", event => {
      if (promptPage.hasUnsavedChanges() || peekPanel.hasUnsavedChanges() || settingsPage.hasUnsavedChanges() || (lazyPages.profile && lazyPages.profile.hasUnsavedChanges()) || memoryPage._bulkBusy || memoryPage._transferBusy) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
    window.addEventListener("languagechange", refreshDynamicI18n);

    /* switchPage("graph") 自身会刷新统计，这里不再重复请求 stats。 */
    switchPage("graph");
  }

  /* ================================================================
     Global Exports (for graph-ui.js and other dependencies)
     ================================================================ */
  window.lmState = state;
  window.lmShowToast = showToast;
  window.lmUnwrapResponse = unwrapResponse;
  window.lmSwitchPage = (name) => switchPage(name);
  window.lmOpenPeekNode = (nodeData) => peekPanel.renderNode(nodeData);
  window.lmOpenPeekMemory = (memory) => peekPanel.renderMemory(memory);
  window.lmClosePeek = () => peekPanel.requestClose();
  window.lmFetchGraphStats = fetchGraphStats;
  window.lmRefreshMemories = () => memoryPage.fetch();
  window.lmHydrateIcons = hydrateIcons;

  // Graph UI registers its DOMContentLoaded initializer before this module.
  // A cached Bridge can resolve immediately, so wait for the canvas to be ready.
  document.addEventListener("DOMContentLoaded", init, { once: true });
})();
