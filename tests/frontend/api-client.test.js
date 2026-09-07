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
