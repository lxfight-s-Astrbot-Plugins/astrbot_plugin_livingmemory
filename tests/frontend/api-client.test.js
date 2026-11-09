import assert from "node:assert/strict";
import test from "node:test";
import { ApiClient } from "../../pages/dashboard/modules/api-client.js";

test("identical concurrent GETs share transport but completed responses are not cached", async () => {
  let finish;
  let calls = 0;
  globalThis.window = { AstrBotPluginPage: {
    apiGet() {
      calls++;
      return new Promise(resolve => { finish = resolve; });
    },
  } };
  try {
    const api = new ApiClient();
    const first = api.get("memories", { page: 1, keyword: "coffee" });
    const second = api.get("memories", { keyword: "coffee", page: 1 });
    assert.equal(calls, 1);
    finish({ status: "ok", data: { total: 3 } });
    assert.deepEqual(await first, await second);
    const next = api.get("memories", { page: 1, keyword: "coffee" });
    assert.equal(calls, 2);
    finish({ total: 4 });
    assert.equal((await next).total, 4);
  } finally {
    delete globalThis.window;
  }
});

test("a rejected GET releases its in-flight slot so retry can succeed", async () => {
  globalThis.window = { AstrBotPluginPage: {} };
  try {
    const api = new ApiClient();
    api.request = async () => { throw new Error("offline"); };
    await assert.rejects(api.get("stats"), /offline/);
    assert.equal(api._pendingGets.size, 0);
    api.request = async () => ({ total: 7 });
    assert.equal((await api.get("stats")).total, 7);
  } finally {
    delete globalThis.window;
  }
});

test("the plugin view bridge takes precedence over the legacy page alias", async () => {
  let activeBridge = "";
  globalThis.window = {
    AstrBotPluginView: {
      apiGet() {
        activeBridge = "view";
        return { status: "ok", data: { total: 2 } };
      },
    },
    AstrBotPluginPage: {
      apiGet() {
        activeBridge = "page";
        return { status: "ok", data: { total: 1 } };
      },
    },
  };
  try {
    const api = new ApiClient();
    assert.deepEqual(await api.get("stats"), { total: 2 });
    assert.equal(activeBridge, "view");
  } finally {
    delete globalThis.window;
  }
});

test("PUT and DELETE use their dedicated bridge methods", async () => {
  const calls = [];
  globalThis.window = {
    AstrBotPluginPage: {
      apiGet() {
        return { status: "ok", data: {} };
      },
      apiPost() {
        return { status: "ok", data: {} };
      },
      apiPut(path, body) {
        calls.push({ method: "PUT", path, body });
        return { status: "ok", data: { version: 3 } };
      },
      apiDelete(path, body) {
        calls.push({ method: "DELETE", path, body });
        return { status: "ok", data: { deleted: true } };
      },
    },
  };
  try {
    const api = new ApiClient();
    assert.deepEqual(await api.put("profiles/1", { profile: { basic: {} } }), { version: 3 });
    assert.deepEqual(await api.delete("profiles/1"), { deleted: true });
    assert.equal(calls[0].method, "PUT");
    assert.equal(calls[0].path, "page/profiles/1");
    assert.deepEqual(calls[0].body, { profile: { basic: {} } });
    assert.equal(calls[1].method, "DELETE");
    assert.equal(calls[1].path, "page/profiles/1");
  } finally {
    delete globalThis.window;
  }
});

test("PUT/DELETE fail clearly when the host bridge has no such method", async () => {
  globalThis.window = {
    t: (key) => key,
    AstrBotPluginPage: {},
  };
  try {
    const api = new ApiClient();
    await assert.rejects(api.put("profiles/1", {}), /misc\.requestFailed|PUT/);
    await assert.rejects(api.delete("profiles/1"), /misc\.requestFailed|DELETE/);
  } finally {
    delete globalThis.window;
  }
});
