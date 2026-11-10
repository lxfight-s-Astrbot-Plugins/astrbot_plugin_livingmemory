import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { ProfilePage } from "../../pages/dashboard/modules/profile-page.js";

const here = dirname(fileURLToPath(import.meta.url));
const dashboard = join(here, "../../pages/dashboard");
const readDashboard = (name) => readFileSync(join(dashboard, name), "utf-8");

class FakeElement {
  constructor() {
    this.innerHTML = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.dataset = {};
    this._listeners = {};
    const classes = new Set();
    this.classList = {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name, enabled) => (enabled ? classes.add(name) : classes.delete(name)),
    };
  }
  addEventListener(type, callback) {
    (this._listeners[type] = this._listeners[type] || []).push(callback);
  }
  setAttribute() {}
  getAttribute() { return null; }
  focus() {}
}

function makeDetail(overrides = {}) {
  return {
    id: 1,
    scope: "user",
    persona_id: "p1",
    version: 2,
    updated_at: "2026-11-01 10:00",
    profile: {
      basic: { name: "Alice" },
      preferences: { language: "zh" },
      status: { mode: "active" },
      task_preferences: {},
    },
    pending: [
      {
        index: 3,
        section: "preferences",
        field: "timezone",
        value: "UTC+8",
        operation: "add",
        evidence: [101, 102],
        confidence: 0.81,
        created_at: "2026-11-01",
      },
    ],
    ...overrides,
  };
}

function setup(options = {}) {
  const elements = {
    "profile-container": new FakeElement(),
    "profile-feedback": new FakeElement(),
    "profile-feedback-message": new FakeElement(),
    "profile-retry": new FakeElement(),
    "profile-scope": { value: "" },
    "profile-persona": { value: "" },
  };
  const calls = [];
  const detail = options.detail === undefined ? makeDetail() : options.detail;
  const items =
    options.items ||
    (detail
      ? [{ id: 1, scope: "user", persona_id: "p1", version: 2, updated_at: "t", pending_count: 1 }]
      : []);
  const historyItems = options.history || [
    {
      version: 2,
      changed_fields: ["basic.name"],
      evidence_memory_ids: [1],
      source: "manual",
      created_at: "t2",
      profile: { basic: { name: "Old" } },
    },
  ];
  const api = {
    async get(path, params) {
      calls.push({ method: "GET", path, params });
      if (path === "stats") return { sessions: { s1: 1 } };
      if (path === "profiles") {
        if (options.listError) throw new Error(options.listError);
        /* 同时给 scope + persona 时后端返回单条 data.profile。 */
        if (options.listSingle) {
          const single = detail || items[0];
          return { profile: single, total: single ? 1 : 0 };
        }
        return { profiles: items, total: items.length };
      }
      if (path === "profiles/detail") {
        if (options.detailError) throw new Error(options.detailError);
        return { profile: detail };
      }
      if (path === "profiles/history") {
        return { history: historyItems };
      }
      if (path === "profiles/pending") {
        return { pending: detail ? detail.pending : [] };
      }
      throw new Error(`unexpected GET ${path}`);
    },
    async post(path, body) {
      calls.push({ method: "POST", path, body });
      if (path === "profiles/update") {
        return {
          profile: makeDetail({
            version: 3,
            updated_at: "t3",
            profile: { ...detail.profile, ...body.profile },
          }),
        };
      }
      if (path === "profiles/delete") return { deleted: true };
      if (path === "profiles/pending/resolve") return { profile: detail };
      return {};
    },
  };
  globalThis.window = {
    t: (key, ...args) => (args.length ? `${key}:${args.join("|")}` : key),
    lmShowToast: () => {},
  };
  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    head: null,
    createElement: () => new FakeElement(),
    baseURI: "http://host/api/page/",
  };
  return { page: new ProfilePage({}, api), elements, calls, detail };
}

function teardown() {
  delete globalThis.window;
  delete globalThis.document;
}

test("renders profile list, editable sections and pending candidates", async () => {
  const { page, elements, calls } = setup();
  try {
    await page.fetch();

    const html = elements["profile-container"].innerHTML;
    assert.match(html, /data-profile-id="1"/, "应渲染档案列表项");
    assert.match(html, /data-profile-section="basic"/);
    assert.match(html, /profile\.sectionBasic/);
    assert.match(html, /data-profile-key="name"/);
    assert.match(html, /value="Alice"/);
    assert.match(html, /profile\.pendingTitle/);
    assert.match(html, /data-profile-action="pending-accept"/);
    assert.match(html, /data-pending-position="0"/);
    assert.equal(elements["profile-feedback"].hidden, true);
    const detailCall = calls.find((call) => call.path === "profiles/detail");
    assert.ok(detailCall, "详情应走 GET profiles/detail");
    assert.deepEqual(detailCall.params, { id: 1 });
  } finally {
    teardown();
  }
});

test("editing marks the page dirty and saving POSTs the whole profile", async () => {
  const { page, elements, calls } = setup();
  try {
    await page.fetch();
    assert.equal(page.isDirty(), false);

    page.setDraftValue("basic", "name", "Bob");
    assert.equal(page.isDirty(), true);

    await page.save();

    const update = calls.find(
      (call) => call.method === "POST" && call.path === "profiles/update"
    );
    assert.ok(update, "保存应调用 POST profiles/update");
    assert.equal(update.body.id, 1);
    assert.equal(update.body.profile.basic.name, "Bob");
    assert.equal(page.detail.version, 3, "应采纳 data.profile 返回的新版本号");
    assert.equal(
      calls.filter((call) => call.method === "PUT").length,
      0,
      "不应再依赖 bridge PUT"
    );
    assert.equal(page.isDirty(), false, "保存成功后应清除 dirty 状态");
    assert.equal(elements["profile-feedback"].hidden, false);
    assert.match(elements["profile-feedback-message"].textContent, /profile\.saved/);
  } finally {
    teardown();
  }
});

test("preference entries can be added, renamed and removed in the draft", async () => {
  const { page } = setup();
  try {
    await page.fetch();
    page.addDraftEntry("preferences");
    assert.equal(page.isDirty(), true);
    assert.ok(Object.prototype.hasOwnProperty.call(page.draft.preferences, "field_1"));

    page.setDraftKey("preferences", "field_1", "theme");
    assert.equal(page.draft.preferences.theme, "");
    assert.equal(Object.prototype.hasOwnProperty.call(page.draft.preferences, "field_1"), false);

    page.removeDraftEntry("preferences", "theme");
    assert.equal(Object.prototype.hasOwnProperty.call(page.draft.preferences, "theme"), false);
    assert.equal(page.isDirty(), false, "还原回初始内容后应不再 dirty");
  } finally {
    teardown();
  }
});

test("history loads versions and shows the selected snapshot read-only", async () => {
  const { page, elements, calls } = setup();
  try {
    await page.fetch();
    await page.openHistory();

    const historyCall = calls.find((call) => call.path === "profiles/history");
    assert.ok(historyCall);
    assert.deepEqual(historyCall.params, { id: 1, limit: 20 });

    let html = elements["profile-container"].innerHTML;
    assert.match(html, /profile\.historyTitle/);
    assert.match(html, /data-profile-history-version="2"/);
    assert.match(html, /profile-history-content/);

    page.selectHistoryVersion(2);
    html = elements["profile-container"].innerHTML;
    assert.match(html, /Old/);
    assert.match(html, /data-profile-action="history-close"/);

    page.closeHistory();
    assert.doesNotMatch(elements["profile-container"].innerHTML, /profile\.historyTitle/);
  } finally {
    teardown();
  }
});

test("pending candidates can be accepted or rejected with their index", async () => {
  const { page, calls } = setup();
  try {
    await page.fetch();
    const pendingCall = calls.find(
      (call) => call.method === "GET" && call.path === "profiles/pending"
    );
    assert.ok(pendingCall, "详情加载后应拉取 scope 级 pending");
    assert.deepEqual(pendingCall.params, { scope: "user", persona_id: "p1" });

    await page.acceptPending(0);
    assert.ok(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.path === "profiles/pending/resolve" &&
          call.body.action === "accept" &&
          call.body.index === 3 &&
          call.body.scope === "user" &&
          call.body.persona_id === "p1"
      )
    );

    await page.rejectPending(0);
    assert.ok(
      calls.some(
        (call) =>
          call.method === "POST" &&
          call.path === "profiles/pending/resolve" &&
          call.body.action === "reject" &&
          call.body.index === 3
      )
    );
  } finally {
    teardown();
  }
});

test("delete asks for confirmation and only deletes when confirmed", async () => {
  const { page, calls } = setup();
  try {
    await page.fetch();

    page._confirmDelete = async () => false;
    await page.deleteProfile();
    assert.equal(
      calls.filter(
        (call) => call.method === "POST" && call.path === "profiles/delete"
      ).length,
      0,
      "取消确认不得删除"
    );

    page._confirmDelete = async () => true;
    await page.deleteProfile();
    const deletes = calls.filter(
      (call) => call.method === "POST" && call.path === "profiles/delete"
    );
    assert.equal(deletes.length, 1);
    assert.deepEqual(deletes[0].body, { id: 1 });
    assert.equal(
      calls.filter((call) => call.method === "DELETE").length,
      0,
      "删除不应再依赖 bridge DELETE"
    );
  } finally {
    teardown();
  }
});

test("scope + persona filters return a single profile record", async () => {
  const { page, elements, calls } = setup({ listSingle: true });
  try {
    elements["profile-scope"].value = "user";
    elements["profile-persona"].value = "p1";

    await page.fetch();

    const listCall = calls.find((call) => call.path === "profiles");
    assert.deepEqual(listCall.params, { scope: "user", persona_id: "p1" });
    assert.equal(page.items.length, 1);
    assert.match(elements["profile-container"].innerHTML, /data-profile-id="1"/);
  } finally {
    teardown();
  }
});

test("empty scope renders the localized empty state", async () => {
  const { page, elements } = setup({ detail: null, items: [] });
  try {
    await page.fetch();
    const html = elements["profile-container"].innerHTML;
    assert.match(html, /profile\.empty/);
    assert.match(html, /profile\.emptyHint/);
    assert.doesNotMatch(html, /profile-save-btn/);
  } finally {
    teardown();
  }
});

test("load failures render inline error feedback with retry", async () => {
  const { page, elements } = setup({ listError: "backend down" });
  try {
    await page.fetch();
    assert.equal(elements["profile-feedback"].hidden, false);
    assert.equal(elements["profile-feedback-message"].textContent, "backend down");
    assert.equal(elements["profile-retry"].hidden, false);
    assert.match(elements["profile-container"].innerHTML, /backend down/);
  } finally {
    teardown();
  }
});

test("profile module registers its own messages in four languages", () => {
  let registered = null;
  globalThis.window = {
    t: (key) => key,
    lmRegisterMessages: (messages) => {
      registered = messages;
    },
  };
  globalThis.document = {
    getElementById: () => null,
    head: null,
  };
  try {
    const page = new ProfilePage({}, {});
    page.init();
    assert.ok(registered, "模块初始化时应注册文案");
    for (const locale of ["zh", "en", "ru", "ja"]) {
      assert.equal(typeof registered["profile.save"][locale], "string");
      assert.equal(typeof registered["profile.deleteTitle"][locale], "string");
    }
    assert.ok(
      Object.keys(registered).filter((key) => key.startsWith("profile.")).length >= 40
    );
  } finally {
    teardown();
  }
});

test("dashboard wires the profile page as a lazy-loaded navigation entry", () => {
  const index = readDashboard("index.html");
  const app = readDashboard("app.js");
  const modules = readDashboard("modules/index.js");
  const build = readFileSync(join(dashboard, "../../scripts/build_dashboard.mjs"), "utf-8");
  const lucide = readDashboard("vendor/lucide-subset.js");

  assert.match(index, /data-page="profile"/);
  assert.match(index, /data-lucide="user"/);
  assert.match(index, /id="page-profile"/);
  assert.match(app, /ensureProfilePage/);
  assert.match(app, /ensureLazyPage/);
  assert.match(app, /"profile\.min\.js"/);
  assert.match(modules, /export \{ ProfilePage \}/);
  assert.match(build, /profile\.min\.js/);
  assert.match(build, /profile\.min\.css/);
  assert.match(lucide, /"user":/);
});
