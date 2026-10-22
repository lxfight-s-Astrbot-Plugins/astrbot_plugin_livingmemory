/**
 * Settings Page - advanced plugin configuration moved out of the host page.
 * Fields and localized labels come from the plugin schema and config-page
 * locale files, so this page stays in sync without hand-maintained forms.
 */

import { esc } from "./utils.js";

export class SettingsPage {
  constructor(state, apiClient) {
    this.state = state;
    this.api = apiClient;
    this.sections = [];
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

  render() {
    const container = document.getElementById("settings-container");
    if (!container) return;
    if (!this.sections.length) {
      container.innerHTML =
        '<p class="text-tertiary">' + esc(window.t("settings.empty")) + "</p>";
      return;
    }
    container.innerHTML = this.sections
      .map((section) => {
        const fields = (section.fields || []).map((field) => this.renderField(field)).join("");
        return (
          '<section class="settings-group">' +
          '<div class="settings-group-title">' + esc(section.title) + "</div>" +
          '<div class="settings-fields">' + fields + "</div>" +
          "</section>"
        );
      })
      .join("");
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
          return '<option value="' + esc(option) + '"' + selected + ">" + esc(label) + "</option>";
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
