import test from "node:test";
import assert from "node:assert/strict";

import { SettingsPage } from "../../pages/dashboard/modules/settings-page.js";

function createDocumentStub(container) {
  return {
    getElementById(id) {
      if (id === "settings-container") return container;
      return null;
    },
  };
}

const sections = [
  {
    key: "recall_engine",
    title: "Recall",
    fields: [
      {
        path: "recall_engine.top_k",
        label: "Top K",
        hint: "Number of memories",
        type: "int",
        value: 5,
        min: 0,
        max: 50,
        step: 1,
      },
      {
        path: "recall_engine.max_k",
        label: "Max K",
        hint: "Agent result limit",
        type: "int",
        value: 10,
        min: 1,
        max: 50,
        step: 1,
      },
    ],
  },
  {
    key: "backup_settings",
    title: "Backup",
    fields: [
      {
        path: "backup_settings.enabled",
        label: "Enable backups",
        hint: "",
        type: "bool",
        value: true,
      },
    ],
  },
];

function createPage(container) {
  globalThis.document = createDocumentStub(container);
  globalThis.window = {
    t(key, value) {
      return value ? `${key}:${value}` : key;
    },
  };
  const page = new SettingsPage({}, { get: async () => ({ sections }) });
  page.sections = sections;
  return page;
}

test("settings render a short common panel plus collapsed categories", () => {
  const container = { innerHTML: "" };
  const page = createPage(container);

  page.render();

  assert.match(container.innerHTML, /class="settings-quick"/);
  assert.match(container.innerHTML, /settings\.quickTitle/);
  assert.match(container.innerHTML, /data-category="system"/);
  assert.doesNotMatch(container.innerHTML, /data-section="recall_engine"/);
  assert.doesNotMatch(container.innerHTML, /<details[^>]* open/);
  // The quick field is not duplicated inside its category.
  assert.equal(
    (container.innerHTML.match(/recall_engine\.top_k/g) || []).length,
    1
  );
});

test("search filters fields and expands matching categories", () => {
  const container = { innerHTML: "" };
  const page = createPage(container);
  page._query = "max";

  page.render();

  assert.doesNotMatch(container.innerHTML, /backup_settings/);
  assert.match(container.innerHTML, /<details[^>]* data-category="recall"[^>]* open/);
  assert.match(container.innerHTML, /recall_engine\.max_k/);
});
