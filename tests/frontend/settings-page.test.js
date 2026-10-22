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

test("settings sections render collapsed with search and badges", () => {
  const container = { innerHTML: "" };
  globalThis.document = createDocumentStub(container);
  globalThis.window = {
    t(key, value) {
      return value ? `${key}:${value}` : key;
    },
  };
  const page = new SettingsPage({}, { get: async () => ({ sections }) });
  page.sections = sections;

  page.render();

  assert.match(container.innerHTML, /<details class="settings-group" data-section="recall_engine">/);
  assert.match(container.innerHTML, /<details class="settings-group" data-section="backup_settings">/);
  assert.doesNotMatch(container.innerHTML, /<details[^>]* open/);
  assert.match(container.innerHTML, /settings\.recommended/);
});

test("search filters fields across sections", () => {
  const container = { innerHTML: "" };
  globalThis.document = createDocumentStub(container);
  globalThis.window = {
    t(key, value) {
      return value ? `${key}:${value}` : key;
    },
  };
  const page = new SettingsPage({}, { get: async () => ({ sections }) });
  page.sections = sections;
  page._query = "top";

  page.render();

  assert.match(container.innerHTML, /recall_engine/);
  assert.doesNotMatch(container.innerHTML, /backup_settings/);
  assert.match(container.innerHTML, /<details[^>]* open/);
});
