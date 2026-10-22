/**
 * Settings Page - advanced plugin configuration moved out of the host page.
 * Sections render as collapsed groups; a search box finds fields by label,
 * hint or config path. Field metadata and localized labels come from the
 * backend schema endpoint, so this page stays in sync without hand-written
 * forms for every option.
 */

import { esc } from "./utils.js";

const QUICK_PATHS = [
  "graph_memory.enabled",
  "memory_dedup.enabled",
  "memory_dedup.similarity_threshold",
  "memory_consolidation.enabled",
  "recall_engine.top_k",
  "reflection_engine.summary_trigger_rounds",
  "filtering_settings.memory_scope_mode",
  "agent_tools.enable_recall_tool",
];

const CATEGORIES = [
  {
    key: "memory",
    titleKey: "settings.catMemory",
    sections: [
      "reflection_engine",
      "importance_decay",
      "memory_dedup",
      "memory_consolidation",
      "forgetting_agent",
    ],
  },
  {
    key: "recall",
    titleKey: "settings.catRecall",
    sections: ["recall_engine", "fusion_strategy", "agent_tools"],
  },
  { key: "graph", titleKey: "settings.catGraph", sections: ["graph_memory"] },
  {
    key: "scope",
    titleKey: "settings.catScope",
    sections: ["access_control", "filtering_settings", "session_manager"],
  },
  {
    key: "system",
    titleKey: "settings.catSystem",
    sections: [
      "backup_settings",
      "migration_settings",
      "index_rebuild_settings",
    ],
  },
];

export class SettingsPage {
  constructor(state, apiClient) {
    this.state = state;
    this.api = apiClient;
    this.sections = [];
    this._open = new Set();
    this._query = "";
    this._busy = false;
    this._bound = false;
  }

  currentLang() {
    if (typeof window.getLanguage === "function") return window.getLanguage();
    return "zh";
  }

  showToast(message, isError = false) {
    if (typeof window.lmShowToast === "function") {
      window.lmShowToast(message, isError);
    }
  }

  async fetch() {
    const container = document.getElementById("settings-container");
    if (!container) return;
    if (!this._bound) this.bindEvents();
    if (!this.sections.length) {
      container.innerHTML =
        '<p class="text-tertiary">' + esc(window.t("settings.loading")) + "</p>";
    }
    try {
      const data = await this.api.get("config", { lang: this.currentLang() });
      this.sections = Array.isArray(data.sections) ? data.sections : [];
      this.renderFeedback("");
      this.render();
    } catch (error) {
      this.renderFeedback(error.message || window.t("settings.loadFailed"));
      container.innerHTML =
        '<p class="text-tertiary">' + esc(window.t("settings.loadFailed")) + "</p>";
    }
  }

  renderFeedback(message) {
    const bar = document.getElementById("settings-feedback");
    const label = document.getElementById("settings-feedback-message");
    if (!bar || !label) return;
    bar.hidden = !message;
    label.textContent = message || "";
  }

  matches(field, query) {
    if (!query) return true;
    const haystack = [field.label, field.hint, field.path].join(" ").toLowerCase();
    return haystack.includes(query);
  }

  render() {
    const container = document.getElementById("settings-container");
    if (!container) return;
    if (!this.sections.length) {
      container.innerHTML =
        '<p class="text-tertiary">' + esc(window.t("settings.empty")) + "</p>";
      return;
    }

    const query = this._query.trim().toLowerCase();
    const bySection = new Map();
    this.sections.forEach((section) => bySection.set(section.key, section));
    const quickSet = new Set(QUICK_PATHS);
    const quickFields = [];
    bySection.forEach((section) => {
      (section.fields || []).forEach((field) => {
        if (quickSet.has(field.path) && this.matches(field, query)) {
          quickFields.push(field);
        }
      });
    });

    let html = "";
    if (quickFields.length) {
      html +=
        '<section class="settings-quick">' +
        '<div class="settings-quick-title">' +
        esc(window.t("settings.quickTitle")) +
        "</div>" +
        '<div class="settings-fields">' +
        quickFields.map((field) => this.renderField(field)).join("") +
        "</div>" +
        "</section>";
    }

    const categoryHtml = CATEGORIES.map((category) => {
      const groups = category.sections
        .map((key) => bySection.get(key))
        .filter(Boolean)
        .map((section) => {
          const fields = (section.fields || []).filter(
            (field) => !quickSet.has(field.path) && this.matches(field, query)
          );
          if (!fields.length) return "";
          return (
            '<div class="settings-subgroup">' +
            '<div class="settings-subgroup-title">' +
            esc(section.title) +
            "</div>" +
            '<div class="settings-fields">' +
            fields.map((field) => this.renderField(field)).join("") +
            "</div>" +
            "</div>"
          );
        })
        .filter(Boolean)
        .join("");
      if (!groups) return "";
      const open = this._open.has(category.key) || Boolean(query);
      return (
        '<details class="settings-group settings-category" data-category="' +
        esc(category.key) +
        '"' +
        (open ? " open" : "") +
        ">" +
        "<summary>" +
        '<span class="settings-group-title">' +
        esc(window.t(category.titleKey)) +
        "</span>" +
        "</summary>" +
        groups +
        "</details>"
      );
    }).join("");

    html +=
      '<div class="settings-categories-title">' +
      esc(window.t("settings.advancedTitle")) +
      "</div>" +
      categoryHtml;

    container.innerHTML =
      html ||
      '<p class="text-tertiary">' + esc(window.t("settings.noMatches")) + "</p>";
  }

  renderField(field) {
    const path = esc(field.path);
    const common =
      'data-setting-path="' + path + '" data-type="' + esc(field.type) + '"';
    let control = "";
    if (field.type === "bool") {
      control =
        '<input type="checkbox" ' + common + (field.value ? " checked" : "") + ">";
    } else if (Array.isArray(field.options)) {
      const labels = Array.isArray(field.option_labels)
        ? field.option_labels
        : field.options;
      const options = field.options
        .map((option, index) => {
          const label = labels[index] || option;
          const selected = String(field.value) === String(option) ? " selected" : "";
          return (
            '<option value="' +
            esc(option) +
            '"' +
            selected +
            ">" +
            esc(label) +
            "</option>"
          );
        })
        .join("");
      control = '<select class="input" ' + common + ">" + options + "</select>";
    } else if (field.type === "int" || field.type === "float") {
      const attrs = [];
      if (field.min !== undefined) attrs.push('min="' + esc(field.min) + '"');
      if (field.max !== undefined) attrs.push('max="' + esc(field.max) + '"');
      if (field.step !== undefined) attrs.push('step="' + esc(field.step) + '"');
      control =
        '<input class="input" type="number" ' +
        common +
        attrs.join(" ") +
        ' value="' +
        esc(field.value ?? "") +
        '">';
    } else if (field.type === "list") {
      control =
        '<textarea class="input textarea" rows="4" ' +
        common +
        ">" +
        esc(JSON.stringify(field.value ?? field.default ?? [], null, 2)) +
        "</textarea>";
    } else {
      control =
        '<input class="input" type="text" ' +
        common +
        ' value="' +
        esc(field.value ?? "") +
        '">';
    }
    return (
      '<div class="settings-field">' +
      '<label class="settings-label">' + esc(field.label) + "</label>" +
      control +
      (field.hint ? '<div class="settings-hint">' + esc(field.hint) + "</div>" : "") +
      "</div>"
    );
  }

  bindEvents() {
    const save = document.getElementById("settings-save-btn");
    if (save && !save.dataset.bound) {
      save.dataset.bound = "1";
      save.addEventListener("click", () => this.save());
    }
    const expand = document.getElementById("settings-expand-all");
    if (expand && !expand.dataset.bound) {
      expand.dataset.bound = "1";
      expand.addEventListener("click", () => {
        CATEGORIES.forEach((category) => this._open.add(category.key));
        this.render();
      });
    }
    const collapse = document.getElementById("settings-collapse-all");
    if (collapse && !collapse.dataset.bound) {
      collapse.dataset.bound = "1";
      collapse.addEventListener("click", () => {
        this._open.clear();
        this._query = "";
        const search = document.getElementById("settings-search");
        if (search) search.value = "";
        this.render();
      });
    }
    const search = document.getElementById("settings-search");
    if (search && !search.dataset.bound) {
      search.dataset.bound = "1";
      search.addEventListener("input", () => {
        this._query = search.value;
        this.render();
      });
    }
    const container = document.getElementById("settings-container");
    if (container && !container.dataset.bound) {
      container.dataset.bound = "1";
      container.addEventListener(
        "toggle",
        (event) => {
          const details = event.target;
          const key = details?.dataset?.category;
          if (!key) return;
          if (details.open) this._open.add(key);
          else this._open.delete(key);
        },
        true
      );
    }
    this._bound = true;
  }

  collectValues() {
    const values = {};
    document.querySelectorAll("[data-setting-path]").forEach((element) => {
      const path = element.dataset.settingPath;
      const type = element.dataset.type;
      if (type === "bool") {
        values[path] = Boolean(element.checked);
      } else if (type === "int") {
        const parsed = Number.parseInt(element.value, 10);
        if (Number.isNaN(parsed)) throw new Error(path);
        values[path] = parsed;
      } else if (type === "float") {
        const parsed = Number.parseFloat(element.value);
        if (Number.isNaN(parsed)) throw new Error(path);
        values[path] = parsed;
      } else if (type === "list") {
        let parsed;
        try {
          parsed = JSON.parse(element.value);
        } catch {
          throw new Error(path);
        }
        if (!Array.isArray(parsed)) throw new Error(path);
        values[path] = parsed;
      } else {
        values[path] = element.value;
      }
    });
    return values;
  }

  async save() {
    if (this._busy) return;
    const button = document.getElementById("settings-save-btn");
    let values;
    try {
      values = this.collectValues();
    } catch (error) {
      this.showToast(window.t("settings.invalidValue", error.message), true);
      return;
    }
    this._busy = true;
    if (button) button.disabled = true;
    try {
      await this.api.post("config/update", { values });
      this.showToast(window.t("settings.saved"));
      await this.fetch();
    } catch (error) {
      this.showToast(error.message || window.t("settings.saveFailed"), true);
    } finally {
      this._busy = false;
      if (button) button.disabled = false;
    }
  }
}
