import assert from "node:assert/strict";
import test from "node:test";

import { NotReadyBanner } from "../../pages/dashboard/modules/not-ready.js";
import { unwrapResponse } from "../../pages/dashboard/modules/utils.js";

function fakeElement() {
  return {
    hidden: true,
    textContent: "",
    dataset: {},
    attrs: {},
    listeners: {},
    addEventListener(type, callback) {
      (this.listeners[type] = this.listeners[type] || []).push(callback);
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    getAttribute(name) {
      return this.attrs[name];
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
  };
}

function installDom() {
  const elements = {
    "not-ready-banner": fakeElement(),
    "not-ready-banner-message": fakeElement(),
    "not-ready-retry": fakeElement(),
  };
  const timers = [];
  const realSetInterval = globalThis.setInterval;
  const realClearInterval = globalThis.clearInterval;
  globalThis.setInterval = (callback, delay) => {
    const handle = { callback, delay };
    timers.push(handle);
    return handle;
  };
  globalThis.clearInterval = (handle) => {
    const index = timers.indexOf(handle);
    if (index !== -1) timers.splice(index, 1);
  };
  globalThis.window = { t: (key) => key };
  globalThis.document = { getElementById: (id) => elements[id] || null };
  return {
    elements,
    timers,
    restore() {
      globalThis.setInterval = realSetInterval;
      globalThis.clearInterval = realClearInterval;
      delete globalThis.window;
      delete globalThis.document;
    },
  };
}

test("not-ready banner shows guidance, polls readiness and hides when ready", async () => {
  const dom = installDom();
  try {
    const readiness = { current: { ready: false, state: "in_progress", message: "尚未就绪" } };
    const api = { get: async () => readiness.current };
    const readyCalls = [];
    const banner = new NotReadyBanner(api, { onReady: () => readyCalls.push("ready") });

    banner.init();
    banner.show({ message: "尚未就绪", init_state: "in_progress" });

    assert.equal(dom.elements["not-ready-banner"].hidden, false);
    assert.equal(
      dom.elements["not-ready-banner-message"].textContent,
      "notReady.message 尚未就绪"
    );
    assert.equal(dom.timers.length, 1, "横幅可见时应轮询 readiness");

    await banner.retry();
    assert.equal(dom.elements["not-ready-banner"].hidden, false, "仍未就绪时保持横幅");

    readiness.current = { ready: true, state: "ready", message: "" };
    await dom.timers[0].callback();

    assert.equal(dom.elements["not-ready-banner"].hidden, true, "就绪后自动收起");
    assert.equal(dom.timers.length, 0, "收起后停止轮询");
    assert.deepEqual(readyCalls, ["ready"], "就绪后通知调用方刷新当前页");
  } finally {
    dom.restore();
  }
});

test("not-ready banner retry hides immediately when the plugin is ready", async () => {
  const dom = installDom();
  try {
    const api = { get: async () => ({ ready: true, state: "ready", message: "" }) };
    let refreshed = 0;
    const banner = new NotReadyBanner(api, { onReady: () => { refreshed += 1; } });
    banner.init();
    banner.show({ message: "尚未就绪" });

    dom.elements["not-ready-retry"].listeners["click"][0]();
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(dom.elements["not-ready-banner"].hidden, true);
    assert.equal(refreshed, 1);
  } finally {
    dom.restore();
  }
});

test("unwrapResponse reports not_ready responses to the global banner hook", () => {
  const reported = [];
  globalThis.window = {
    t: (key) => key,
    lmReportPluginNotReady: (response) => reported.push(response),
  };
  try {
    assert.throws(
      () => unwrapResponse({ status: "error", code: "not_ready", message: "boom" }),
      /boom/
    );
    assert.equal(reported.length, 1);
    assert.equal(reported[0].code, "not_ready");

    assert.throws(
      () => unwrapResponse({ status: "error", message: "plain failure" }),
      /plain failure/
    );
    assert.equal(reported.length, 1, "普通错误不应触发未就绪横幅");

    assert.deepEqual(unwrapResponse({ status: "ok", data: { ready: true } }), {
      ready: true,
    });
  } finally {
    delete globalThis.window;
  }
});
