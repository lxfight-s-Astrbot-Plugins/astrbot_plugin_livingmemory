import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { DiagnosticsPage } from "../../pages/dashboard/modules/diagnostics-page.js";

const here = dirname(fileURLToPath(import.meta.url));
const dashboard = join(here, "../../pages/dashboard");
const readDashboard = (name) => readFileSync(join(dashboard, name), "utf-8");

class FakeElement {
  constructor() {
    this.innerHTML = "";
    this.textContent = "";
    this.hidden = false;
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

const TRACE = {
  id: "t1",
  ts: 1762000000,
  scope: "qq:group:1",
  cache_hit: true,
  routes: {
    document: { candidates: 5, ok: true, ms: 12 },
    graph: { candidates: 4, ok: true, ms: 20 },
  },
  injected: 1,
  injected_chars: 100,
  duration_ms: 42,
};

function setup(options = {}) {
  const root = new FakeElement();
  const elements = { "page-diagnostics": root };
  const calls = [];
  const registered = [];
  const api = {
    async get(path, params) {
      calls.push({ method: "GET", path, params });
      if (options.failGet === path) throw new Error("backend down");
      if (path === "traces") {
        if (options.emptyTraces) {
          return { enabled: true, traces: [], total_records: 0, persist_enabled: true };
        }
        return {
          enabled: true,
          traces: [TRACE],
          count: 1,
          max_records: 100,
          total_records: 1,
          persist_enabled: true,
        };
      }
      if (path === "traces/t1") {
        return {
          enabled: true,
          trace: {
            ...TRACE,
            filters: {
              min_similarity: 0.2,
              min_importance: 0.3,
              memory_type: "all",
              candidates: 9,
              kept: 5,
              rejected: { low_importance: 4, atom_expired: 2 },
            },
            candidates: [{ doc_id: 7, final_score: 0.91, breakdown: {} }],
            injected: [{ doc_id: 7, score: 0.91, chars: 100 }],
            agentic: true,
            budget_truncated: true,
            notes: ["trace note", "second note"],
          },
        };
      }
      if (path === "memories/7/lineage") {
        return {
          memory_id: 7,
          memory: { id: 7, text: "lineage summary", importance: 0.8, status: "active" },
          sources: {
            count: 1,
            messages: [{ content: "hello world", timestamp: 1762000000 }],
            source_window: { start: 1762000000, end: 1762000100 },
            has_source: true,
          },
          supersedes: {
            chain: [{ id: 7, superseded_by: null }],
            superseded_by: null,
            of: [{ id: 3, text: "old memory" }],
          },
          links: { previous_id: 2, parent_memory_id: 1, related_ids: [9] },
          graph: { nodes: [{ id: 1, label: "n1" }], edges: [{ id: 2 }] },
          traces: [{ id: "t1", ts: 1762000000 }],
        };
      }
      if (path === "channels") {
        if (options.emptyChannels) return { channels: [], total: 0 };
        return {
          channels: [
            { scope: "qq:group:1", platform: "qq", channel_id: "1", memory_count: 3, latest_create_time: 1762000000 },
          ],
          total: 1,
        };
      }
      if (path === "memories") {
        return {
          items: [
            {
              id: 7,
              summary: "channel memory",
              metadata: { importance: 0.7, status: "active", create_time: 1762000000 },
              created_at: "2026-11-01",
            },
          ],
          total: 40,
          page: params.page,
        };
      }
      if (path === "external/status") {
        return {
          enabled: !options.externalDisabled,
          listen_host: "127.0.0.1",
          listen_port: 7801,
          audit_retention_days: 30,
        };
      }
      if (path === "external/keys") {
        if (options.emptyKeys) return { keys: [], total: 0 };
        return {
          keys: [
            {
              id: "k1",
              name: "app",
              key_prefix: "lm_ab",
              scopes: ["read"],
              allowed_personas: [],
              allowed_scopes: [],
              rate_limit_per_min: 60,
              write_limit_per_min: 0,
              created_at: 1762000000,
              last_used_at: 1762000100,
              revoked_at: null,
            },
          ],
          total: 1,
        };
      }
      if (path === "external/audit") {
        return {
          records: [
            { ts: 1762000000, key_name: "app", action: "search", status_code: 200, latency_ms: 5, memory_id: 7 },
          ],
          total: 1,
        };
      }
      if (path === "usage") {
        if (options.emptyUsage) {
          return { usage: {}, fallback_provider_ids: [], timeout_seconds: 30 };
        }
        return {
          usage: {
            calls: 5,
            input_tokens: 1000,
            output_tokens: 500,
            total_tokens: 1500,
            estimated_calls: 2,
            providers: { p1: 3, p2: 2 },
            fallback_calls: 1,
          },
          fallback_provider_ids: ["p2"],
          timeout_seconds: 30,
        };
      }
      throw new Error(`unexpected GET ${path}`);
    },
    async post(path, body) {
      calls.push({ method: "POST", path, body });
      if (path === "external/keys") return { key: { id: "k2" }, token: "lm_SECRET" };
      return {};
    },
    async delete(path) {
      calls.push({ method: "DELETE", path });
      return { key: { id: "k1" } };
    },
  };
  globalThis.window = {
    t: (key, ...args) => (args.length ? `${key}:${args.join("|")}` : key),
    lmShowToast: () => {},
    lmRegisterMessages: (messages) => registered.push(messages),
  };
  globalThis.document = {
    getElementById: (id) => elements[id] || null,
    head: null,
    createElement: () => new FakeElement(),
    baseURI: "http://host/api/page/",
  };
  const page = new DiagnosticsPage({}, api);
  return { page, root, calls, registered };
}

function teardown() {
  delete globalThis.window;
  delete globalThis.document;
}

test("loads each tab on demand and renders trace list first", async () => {
  const { page, root, calls } = setup();
  try {
    page.init();
    await page.fetch();

    assert.ok(calls.some((call) => call.path === "traces"));
    const html = root.innerHTML;
    assert.match(html, /role="tablist"/);
    assert.match(html, /role="tab"/);
    assert.match(html, /aria-selected="true"/);
    assert.match(html, /data-diag-row="trace"/);
    assert.match(html, /data-trace-id="t1"/);
    assert.match(html, /diag\.recorderOn/);
    assert.match(html, /1 \/ 100 chars/, "summary.injected 是数字时也应显示注入条数");

    await page.activateTab("channels");
    assert.ok(calls.some((call) => call.path === "channels"));
    assert.match(root.innerHTML, /qq/);

    await page.activateTab("external");
    assert.ok(calls.some((call) => call.path === "external/keys"));
    assert.ok(calls.some((call) => call.path === "external/audit"));
    assert.match(root.innerHTML, /lm_ab/);

    await page.activateTab("usage");
    assert.ok(calls.some((call) => call.path === "usage"));
    assert.match(root.innerHTML, /1,500/);
  } finally {
    teardown();
  }
});

test("opens a trace detail and loads memory lineage", async () => {
  const { page, root, calls } = setup();
  try {
    await page.fetch();
    await page.openTrace("t1");
    assert.ok(calls.some((call) => call.path === "traces/t1"));
    let html = root.innerHTML;
    assert.match(html, /diag\.detail/);
    assert.match(html, /document/);
    assert.match(html, /low_importance/);
    assert.match(html, /trace note/);
    assert.match(html, /diag\.budget/);

    await page.loadLineage(7);
    assert.ok(calls.some((call) => call.path === "memories/7/lineage"));
    html = root.innerHTML;
    assert.match(html, /diag\.lineageTitle:7/);
    assert.match(html, /lineage summary/);
    assert.match(html, /hello world/);
    assert.match(html, /diag\.graphCounts:1\|1/);
    assert.match(html, /previous:2/);
    assert.match(html, /diag\.traceHits/);
  } finally {
    teardown();
  }
});

test("channels drill into memories and load more", async () => {
  const { page, root, calls } = setup();
  try {
    await page.activateTab("channels");
    await page.openChannel((page.channels.channels || [])[0]);

    const firstCall = calls.find((call) => call.path === "memories");
    assert.deepEqual(firstCall.params, {
      session_id: "qq:group:1",
      page: 1,
      page_size: 20,
      status: "all",
    });
    assert.match(root.innerHTML, /channel memory/);
    assert.match(root.innerHTML, /diag\.loadMore/, "总数大于当前条数时应显示加载更多");

    await page.loadChannelMemories(2);
    assert.equal(page.channelMemories.items.length, 2);
    assert.equal(page.channelMemories.page, 2);

    page.backToChannels();
    assert.equal(page.activeChannel, null);
  } finally {
    teardown();
  }
});

test("external tab explains that the API is disabled instead of showing Not found", async () => {
  const ctx = setup({ externalDisabled: true });
  try {
    await ctx.page.fetch();
    await ctx.page.activateTab("external");
    assert.match(ctx.root.innerHTML, /diag\.externalDisabled/);
    assert.match(ctx.root.innerHTML, /diag\.externalDisabledHint/);
    assert.doesNotMatch(ctx.root.innerHTML, /diag\.noKeys/);
    assert.ok(
      !ctx.calls.some((call) => call.path === "external/keys" || call.path === "external/audit"),
      "禁用时不应请求 key/audit 接口"
    );
  } finally {
    teardown();
  }
});

test("creates an external key and keeps the one-time token only until dismissed", async () => {
  const { page, root, calls } = setup();
  try {
    await page.activateTab("external");
    const token = await page.createKey({
      name: " new-key ",
      mode: "write",
      personas: "p1, p2",
      scopes: "scope-a",
      rate: "30",
      write: "5",
    });

    assert.equal(token, "lm_SECRET");
    const create = calls.find((call) => call.method === "POST" && call.path === "external/keys");
    assert.ok(create);
    assert.equal(create.body.name, "new-key");
    assert.equal(create.body.mode, "write");
    assert.deepEqual(create.body.allowed_personas, ["p1", "p2"]);
    assert.deepEqual(create.body.allowed_scopes, ["scope-a"]);
    assert.equal(create.body.rate_limit_per_min, 30);
    assert.equal(create.body.write_limit_per_min, 5);

    assert.match(root.innerHTML, /lm_SECRET/);
    assert.match(root.innerHTML, /diag\.tokenWarning/);

    page.dismissToken();
    assert.equal(page.token, null);
    assert.doesNotMatch(root.innerHTML, /lm_SECRET/);
  } finally {
    teardown();
  }
});

test("revoking a key requires confirmation", async () => {
  const { page, calls } = setup();
  try {
    await page.activateTab("external");
    page._confirmRevoke = async () => false;
    await page.revokeKey({ id: "k1", name: "app" });
    assert.equal(
      calls.filter(
        (call) => call.method === "POST" && call.path === "external/keys/revoke"
      ).length,
      0
    );

    page._confirmRevoke = async () => true;
    await page.revokeKey({ id: "k1", name: "app" });
    const revokes = calls.filter(
      (call) => call.method === "POST" && call.path === "external/keys/revoke"
    );
    assert.equal(revokes.length, 1);
    assert.deepEqual(revokes[0].body, { id: "k1" });
    assert.equal(
      calls.filter((call) => call.method === "DELETE").length,
      0,
      "吊销不应再依赖 bridge DELETE"
    );
  } finally {
    teardown();
  }
});

test("usage tab renders cards, provider distribution and fallback chain", async () => {
  const { page, root } = setup();
  try {
    await page.activateTab("usage");
    const html = root.innerHTML;
    assert.match(html, /1,000/);
    assert.match(html, /1,500/);
    assert.match(html, /p1/);
    assert.match(html, /p2/);
    assert.match(html, /diag\.usageFallbackChain/);
    assert.match(html, /diag\.usageTimeout/);
    assert.match(html, /diag\.seconds:30/);
  } finally {
    teardown();
  }
});

test("empty and error states are rendered per tab", async () => {
  const empty = setup({ emptyTraces: true, emptyChannels: true, emptyKeys: true, emptyUsage: true });
  try {
    await empty.page.fetch();
    assert.match(empty.root.innerHTML, /diag\.tracesEmpty/);
    await empty.page.activateTab("channels");
    assert.match(empty.root.innerHTML, /diag\.channelsEmpty/);
    await empty.page.activateTab("external");
    assert.match(empty.root.innerHTML, /diag\.noKeys/);
    await empty.page.activateTab("usage");
    assert.match(empty.root.innerHTML, /diag\.usageEmpty/);
  } finally {
    teardown();
  }

  const failing = setup({ failGet: "traces" });
  try {
    await failing.page.fetch();
    assert.match(failing.root.innerHTML, /backend down/);
    assert.match(failing.root.innerHTML, /diag\.retry/);
  } finally {
    teardown();
  }
});

test("tab keyboard navigation changes the active tab", async () => {
  const { page } = setup();
  try {
    await page.fetch();
    let defaultPrevented = false;
    page._onKeydown({
      key: "ArrowRight",
      target: { dataset: { diagRole: "tab", diagTab: "traces" } },
      preventDefault() {
        defaultPrevented = true;
      },
    });
    for (let i = 0; i < 10; i++) await Promise.resolve();
    assert.equal(defaultPrevented, true);
    assert.equal(page.activeTab, "channels");
  } finally {
    teardown();
  }
});

test("diagnostics page is wired as a lazy-loaded navigation entry", () => {
  const index = readDashboard("index.html");
  const app = readDashboard("app.js");
  const modules = readDashboard("modules/index.js");
  const i18n = readDashboard("i18n.js");
  const build = readFileSync(join(dashboard, "../../scripts/build_dashboard.mjs"), "utf-8");
  const css = readDashboard("diagnostics.css");

  assert.match(index, /data-page="diagnostics"/);
  assert.match(index, /id="page-diagnostics"/);
  assert.match(index, /<span class="page-index">08<\/span><span class="page-title" data-i18n="nav.settings">/);
  assert.match(app, /ensureDiagnosticsPage/);
  assert.match(app, /diagnostics\.min\.js/);
  assert.match(modules, /export \{ DiagnosticsPage \}/);
  assert.match(build, /diagnostics\.min\.js/);
  assert.match(build, /diagnostics\.min\.css/);
  assert.match(i18n, /"nav\.diagnostics"/);
  assert.match(css, /\.diag-tabs/);
});
