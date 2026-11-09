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


test("adapter selection shows shared members and preserves an untouched legacy policy", () => {
  global.window = { t: key => key };
  const page = new SettingsPage({}, {});
  const html = page.renderField({path: "filtering_settings.shared_adapter_ids", type: "adapter-list", value: ["qq-a"], label: "Shared adapters", options: ["qq-a", "wechat-a"], option_labels: ["QQ", "WeChat"]});
  assert.match(html, /value="qq-a" checked/);
  assert.doesNotMatch(html, /value="wechat-a" checked/);
  const element = {dataset: {settingPath: "filtering_settings.shared_adapter_ids", type: "adapter-list", original: "null"}, querySelectorAll: () => [{value: "qq-a"}]};
  global.document = {querySelectorAll: () => [element]};
  assert.equal(page.collectValues()[element.dataset.settingPath], null);
  element.dataset.edited = "1";
  assert.deepEqual(page.collectValues()[element.dataset.settingPath], ["qq-a"]);
});

test("schema text fields render as textareas while numeric fields stay number inputs", () => {
  globalThis.window = { t: (key) => key };
  const page = new SettingsPage({}, {});
  const textHtml = page.renderField({
    path: "access_control.identity_aliases",
    label: "Aliases",
    hint: "每行一个",
    type: "text",
    value: "telegram:1=小明\nqq:2=小红",
    default: "",
  });
  assert.match(textHtml, /<textarea[^>]*data-setting-path="access_control\.identity_aliases"/);
  assert.match(textHtml, /telegram:1=小明/);
  assert.doesNotMatch(textHtml, /<input[^>]*type="text"/);

  const numberHtml = page.renderField({
    path: "recall_engine.top_k",
    label: "Top K",
    type: "int",
    value: 5,
    default: 5,
  });
  assert.match(numberHtml, /type="number"/);
  assert.doesNotMatch(numberHtml, /textarea/);
});

test("fields expose schema defaults, restore buttons and reload hints", () => {
  globalThis.window = { t: (key) => key };
  const page = new SettingsPage({}, {});
  const html = page.renderField({
    path: "agent_tools.enable_agentic_recall_beta",
    label: "Beta recall",
    hint: "保存并成功重载插件后生效",
    type: "bool",
    value: true,
    default: false,
  });

  assert.match(html, /class="settings-default"/);
  assert.match(html, /settings\.defaultValue/);
  assert.match(html, /data-default="false"/);
  assert.match(html, /data-reset-path="agent_tools\.enable_agentic_recall_beta"/);
  assert.match(html, /settings-reload-badge/);
  assert.match(html, /settings\.reloadRequired/);

  const plain = page.renderField({ path: "recall_engine.max_k", label: "Max", type: "int", value: 10 });
  assert.doesNotMatch(plain, /settings-reload-badge/, "普通字段不应出现重载徽标");
  assert.doesNotMatch(plain, /settings-default/, "没有 schema default 的字段不显示默认值行");

  const numeric = { value: "" };
  page._applyDefault(numeric, "int", 5);
  assert.equal(numeric.value, 5);
  const booleanControl = { checked: false };
  page._applyDefault(booleanControl, "bool", true);
  assert.equal(booleanControl.checked, true);
});

test("dirty tracking compares the current form to the loaded baseline", async () => {
  globalThis.window = { t: (key) => key };
  const page = new SettingsPage({}, {});
  const control = { dataset: { settingPath: "recall_engine.top_k", type: "int" }, value: "5" };
  globalThis.document = { querySelectorAll: () => [control] };
  try {
    page._baseline = JSON.stringify({ "recall_engine.top_k": 5 });
    assert.equal(page.hasUnsavedChanges(), false);

    control.value = "9";
    assert.equal(page.hasUnsavedChanges(), true);

    control.value = "5";
    assert.equal(page.hasUnsavedChanges(), false);

    page._busy = true;
    assert.equal(await page.canLeave(), false, "保存中不允许离开");
  } finally {
    delete globalThis.document;
  }
});

function createSavePage(postImpl) {
  const feedbackBar = {
    hidden: true,
    classes: new Set(),
    classList: {
      toggle(name, enabled) {
        if (enabled) feedbackBar.classes.add(name);
        else feedbackBar.classes.delete(name);
      },
    },
  };
  const feedbackLabel = { textContent: "" };
  const saveButton = { disabled: false };
  const elements = {
    "settings-save-btn": saveButton,
    "settings-feedback": feedbackBar,
    "settings-feedback-message": feedbackLabel,
  };
  const toasts = [];
  globalThis.window = {
    t: (key, ...args) => (args.length ? `${key}:${args.join(",")}` : key),
    lmShowToast: (message, isError) => toasts.push({ message, isError }),
  };
  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    querySelectorAll: () => [
      {
        dataset: { settingPath: "memory_consolidation.keep_original", type: "string" },
        value: "delete",
      },
    ],
  };
  const posts = [];
  const page = new SettingsPage(
    {},
    {
      post: async (...args) => {
        posts.push(args);
        return postImpl ? postImpl() : {};
      },
      get: async () => ({ sections: [] }),
    }
  );
  page._baseline = JSON.stringify({ "memory_consolidation.keep_original": "archive" });
  return { page, feedbackBar, feedbackLabel, saveButton, posts, toasts };
}

test("cancelling the dangerous-change confirmation does not submit", async () => {
  const env = createSavePage();
  try {
    env.page._confirmDangerousChanges = async () => false;
    await env.page.save();

    assert.equal(env.posts.length, 0, "取消确认后不得提交");
    assert.equal(env.feedbackLabel.textContent, "settings.saveCancelled");
    assert.equal(env.saveButton.disabled, false, "取消后按钮恢复可用");
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("confirming the dangerous change submits and reports success", async () => {
  const env = createSavePage();
  try {
    let confirmedValues = null;
    env.page._confirmDangerousChanges = async (values) => {
      confirmedValues = values;
      return true;
    };
    await env.page.save();

    assert.deepEqual(confirmedValues, { "memory_consolidation.keep_original": "delete" });
    assert.equal(env.posts.length, 1);
    assert.equal(env.posts[0][0], "config/update");
    assert.equal(env.feedbackLabel.textContent, "settings.saved");
    assert.equal(env.feedbackBar.hidden, false);
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("save failure renders an error feedback bar", async () => {
  const env = createSavePage(() => {
    throw new Error("backend exploded");
  });
  try {
    env.page._confirmDangerousChanges = async () => true;
    await env.page.save();

    assert.equal(env.feedbackLabel.textContent, "backend exploded");
    assert.equal(env.feedbackBar.hidden, false);
    assert.equal(env.feedbackBar.classes.has("is-error"), true);
    assert.equal(env.toasts.at(-1).isError, true);
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("editing a filtered-out field still counts as dirty", () => {
  globalThis.window = { t: (key) => key };
  const page = new SettingsPage({}, {});
  globalThis.document = {
    querySelectorAll: () => [
      { dataset: { settingPath: "recall_engine.max_k", type: "int" }, value: "10" },
    ],
  };
  try {
    page._baseline = JSON.stringify({ "recall_engine.top_k": 5, "recall_engine.max_k": 10 });
    page._formValues = { "recall_engine.top_k": 9 }; // 已被搜索条件隐藏的字段
    assert.equal(page.hasUnsavedChanges(), true);

    page._formValues = {};
    assert.equal(page.hasUnsavedChanges(), false);
  } finally {
    delete globalThis.document;
  }
});

test("list textareas keep raw JSON while editing instead of double-encoding", () => {
  globalThis.window = { t: (key) => key };
  const page = new SettingsPage({}, {});
  const html = page.renderField({
    path: "some.list_field",
    label: "List",
    type: "list",
    value: "[1, 2]",
    default: [],
  });
  assert.match(html, />\[1, 2\]</);
  assert.doesNotMatch(html, /&quot;\[1, 2\]&quot;/);

  page._rememberValue({
    dataset: { settingPath: "some.list_field", type: "list" },
    value: "[3]",
  });
  assert.deepEqual(page._formValues["some.list_field"], [3]);
});

test("dangerous-change detection only fires for risky transitions", async () => {
  globalThis.window = { t: (key) => key };
  globalThis.document = { getElementById: () => null };
  const page = new SettingsPage({}, {});
  try {
    page._baseline = JSON.stringify({
      "memory_consolidation.keep_original": "archive",
      "forgetting_agent.auto_cleanup_enabled": false,
      "graph_memory.enabled": true,
    });
    assert.equal(
      await page._confirmDangerousChanges({
        "memory_consolidation.keep_original": "archive",
        "forgetting_agent.auto_cleanup_enabled": false,
        "graph_memory.enabled": true,
      }),
      true,
      "未改变危险项时直接放行"
    );
    assert.equal(
      await page._confirmDangerousChanges({
        "memory_consolidation.keep_original": "delete",
        "forgetting_agent.auto_cleanup_enabled": false,
        "graph_memory.enabled": true,
      }),
      false,
      "危险切换需要确认（测试环境没有 dialog 时按取消）"
    );
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});
