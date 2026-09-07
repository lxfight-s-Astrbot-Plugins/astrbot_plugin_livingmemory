import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

test("app waits for the graph DOM initializer before contacting a ready Bridge", async () => {
  const source = readFileSync(new URL("../../pages/dashboard/app.js", import.meta.url), "utf8")
    .replace(/import \{[\s\S]*?\} from "\.\/modules\/index\.js";/, "");
  let graphReady = false;
  let readyCalls = 0;
  const stopAtBridge = new Error("Stop after startup ordering is verified");
  const domReady = [() => { graphReady = true; }];
  class PageStub {}
  vm.runInNewContext(source, {
    ApiClient: class {
      async ready() {
        readyCalls++;
        assert.equal(graphReady, true);
        throw stopAtBridge;
      }
    },
    PeekPanel: PageStub,
    MemoryPage: PageStub,
    RecallPage: PageStub,
    SystemPage: PageStub,
    PromptPage: PageStub,
    esc() {}, statusPill() {}, nodeBadge() {},
    window: { matchMedia: () => ({ matches: true }), LMAppearance: { init() {} } },
    document: {
      // Static modules execute in the interactive state, before DOMContentLoaded.
      readyState: "interactive",
      addEventListener: (event, callback) => {
        if (event === "DOMContentLoaded") domReady.push(callback);
      },
    },
  });
  assert.equal(readyCalls, 0);
  assert.equal(domReady.length, 2);
  domReady[0]();
  await assert.rejects(domReady[1](), error => error === stopAtBridge);
  assert.equal(readyCalls, 1);
});
