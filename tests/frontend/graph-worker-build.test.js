import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const workerPath = new URL(
  "../../pages/dashboard/assets/graph-layout-worker.min.js",
  import.meta.url
);

test("built 2D layout worker is self-contained and loadable", () => {
  const builtSource = readFileSync(workerPath, "utf8");
  const sandbox = {
    performance: { now: () => 0 },
    setTimeout: () => 0,
    clearTimeout: () => {},
    postMessage: () => {},
  };
  /* 浏览器 Worker 中 self === globalThis；vm 沙箱里显式指回去。 */
  sandbox.self = sandbox;

  vm.runInNewContext(builtSource, sandbox);

  assert.equal(
    typeof sandbox.onmessage,
    "function",
    "Worker 脚本必须注册消息入口"
  );
  assert.equal(
    typeof sandbox.GraphLayoutCore.createForceLayout,
    "function",
    "合并后的布局核心应在同一文件内可用"
  );
  assert.equal("importScripts" in sandbox, false, "构建产物不应再依赖 importScripts");
});
