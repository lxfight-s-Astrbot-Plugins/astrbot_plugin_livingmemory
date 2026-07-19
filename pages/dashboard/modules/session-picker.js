/** Layered session picker that fills, but never replaces, free-text inputs. */
export class SessionPicker {
  constructor(apiClient, showToast) {
    this.api = apiClient;
    this.showToast = showToast;
    this.targetInput = null;
    this.items = [];
    this.total = 0;
    this.requestId = 0;
  }

  init() {
    this.overlay = document.getElementById("session-picker-overlay");
    this.platform = document.getElementById("session-picker-platform");
    this.chatType = document.getElementById("session-picker-chat-type");
    this.updatedAfter = document.getElementById("session-picker-after");
    this.updatedBefore = document.getElementById("session-picker-before");
    this.targetQuery = document.getElementById("session-picker-target-query");
    this.target = document.getElementById("session-picker-target");
    this.resultHint = document.getElementById("session-picker-result-hint");

    document.querySelectorAll("[data-session-picker-target]").forEach(button => {
      button.addEventListener("click", () => this.open(button.dataset.sessionPickerTarget));
    });
    document.getElementById("session-picker-close")?.addEventListener("click", () => this.close());
    document.getElementById("session-picker-cancel")?.addEventListener("click", () => this.close());
    document.getElementById("session-picker-apply")?.addEventListener("click", () => this.apply());
    document.getElementById("session-picker-clear")?.addEventListener("click", () => this.clear());
    this.overlay?.addEventListener("click", event => {
      if (event.target === this.overlay) this.close();
    });

    this.platform?.addEventListener("change", () => {
      this.chatType.value = "";
      this.chatType.disabled = !this.platform.value;
      this.updatedAfter.disabled = true;
      this.updatedBefore.disabled = true;
      this.targetQuery.value = "";
      this.targetQuery.disabled = true;
      this.target.value = "";
      this.target.disabled = true;
      this.fetchOptions();
    });
    this.chatType?.addEventListener("change", () => {
      const enabled = Boolean(this.platform.value && this.chatType.value);
      this.updatedAfter.disabled = !enabled;
      this.updatedBefore.disabled = !enabled;
      this.targetQuery.disabled = !enabled;
      this.target.disabled = !enabled;
      this.target.value = "";
      this.fetchOptions();
    });
    [this.updatedAfter, this.updatedBefore].forEach(input => {
      input?.addEventListener("change", () => this.fetchOptions());
    });
    this.targetQuery?.addEventListener("input", () => this.filterTargets());
  }

  async open(targetId) {
    this.targetInput = document.getElementById(targetId);
    if (!this.targetInput || !this.overlay) return;
    this.overlay.classList.add("visible");
    await this.fetchOptions();
  }

  close() {
    this.overlay?.classList.remove("visible");
  }

  _timestamp(input) {
    if (!input?.value) return "";
    const timestamp = new Date(input.value).getTime();
    return Number.isFinite(timestamp) ? String(timestamp / 1000) : "";
  }

  async fetchOptions() {
    if (!this.overlay?.classList.contains("visible")) return;
    const requestId = ++this.requestId;
    const params = { limit: "500" };
    if (this.platform.value) params.platform_id = this.platform.value;
    if (this.chatType.value) params.chat_type = this.chatType.value;
    const after = this._timestamp(this.updatedAfter);
    const before = this._timestamp(this.updatedBefore);
    if (after) params.updated_after = after;
    if (before) params.updated_before = before;

    try {
      const data = await this.api.get("sessions", params);
      if (requestId !== this.requestId) return;
      this.renderPlatforms(data.facets?.platform_ids || []);
      this.renderChatTypes(data.facets?.chat_types || []);
      this.items = data.items || [];
      this.total = Number(data.total || 0);
      this.filterTargets();
    } catch (error) {
      this.showToast(error.message || window.t("sessionPicker.loadFailed"), true);
      this.resultHint.textContent = window.t("sessionPicker.manualStillAvailable");
    }
  }

  renderPlatforms(platformIds) {
    const current = this.platform.value;
    this.platform.innerHTML = '<option value="">' + window.t("sessionPicker.choosePlatform") + '</option>' +
      platformIds.map(id => '<option value="' + this.escape(id) + '">' + this.escape(id) + '</option>').join("");
    if (platformIds.includes(current)) this.platform.value = current;
    this.chatType.disabled = !this.platform.value;
  }

  renderChatTypes(types) {
    const current = this.chatType.value;
    const labels = {
      group: window.t("sessionPicker.group"),
      private: window.t("sessionPicker.private"),
      other: window.t("sessionPicker.other"),
    };
    this.chatType.innerHTML = '<option value="">' + window.t("sessionPicker.chooseChatType") + '</option>' +
      types.map(type => '<option value="' + this.escape(type) + '">' + this.escape(labels[type] || type) + '</option>').join("");
    if (types.includes(current)) this.chatType.value = current;
    const enabled = Boolean(this.platform.value && this.chatType.value);
    this.updatedAfter.disabled = !enabled;
    this.updatedBefore.disabled = !enabled;
    this.targetQuery.disabled = !enabled;
    this.target.disabled = !enabled;
  }

  filterTargets() {
    const query = this.targetQuery.value.trim().toLocaleLowerCase();
    const items = query
      ? this.items.filter(item => String(item.target_id || "").toLocaleLowerCase().includes(query))
      : this.items;
    this.renderTargets(items, query ? items.length : this.total);
  }

  renderTargets(items, total) {
    const current = this.target.value;
    this.target.innerHTML = '<option value="">' + window.t("sessionPicker.chooseTarget") + '</option>' +
      items.map(item => {
        const updated = item.last_active_at ? new Date(item.last_active_at * 1000).toLocaleString() : "--";
        const label = `${item.target_id} · ${updated}`;
        return '<option value="' + this.escape(item.session_id) + '">' + this.escape(label) + '</option>';
      }).join("");
    if (items.some(item => item.session_id === current)) this.target.value = current;
    this.resultHint.textContent = window.t("sessionPicker.resultCount", total);
  }

  apply() {
    if (!this.target.value) {
      this.showToast(window.t("sessionPicker.chooseTargetFirst"), true);
      return;
    }
    this.fill(this.target.value);
  }

  clear() {
    this.fill("");
  }

  fill(value) {
    if (!this.targetInput) return;
    this.targetInput.value = value;
    this.targetInput.dispatchEvent(new Event("input", { bubbles: true }));
    this.targetInput.dispatchEvent(new Event("change", { bubbles: true }));
    this.close();
  }

  escape(value) {
    const div = document.createElement("div");
    div.textContent = String(value ?? "");
    return div.innerHTML;
  }
}
