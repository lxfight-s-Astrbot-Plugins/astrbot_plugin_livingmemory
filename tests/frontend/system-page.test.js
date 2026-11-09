import assert from "node:assert/strict";
import test from "node:test";

import { SystemPage } from "../../pages/dashboard/modules/system-page.js";

test("consolidation asks for confirmation and skips the request when cancelled", async () => {
  const elements = {
    "cons-run-btn": { disabled: false },
    "cons-run-result": { textContent: "" },
  };
  const posts = [];
  globalThis.window = { t: (key) => key };
  globalThis.document = { getElementById: (id) => elements[id] || null };
  try {
    const state = { _consolidationEnabled: true };
    const page = new SystemPage(state, {
      post: async (...args) => {
        posts.push(args);
        return {};
      },
    });

    await page.runConsolidation();

    assert.equal(posts.length, 0, "取消确认后不得触发整合");
    assert.equal(elements["cons-run-btn"].disabled, false);
    assert.equal(elements["cons-run-result"].textContent, "");
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});
