import assert from "node:assert/strict";
import test from "node:test";

import { PeekPanel } from "../../pages/dashboard/modules/peek-panel.js";

// 最小 DOM 替身，含 classList 与关联记忆按钮
function environment(t) {
  const elements = new Map();
  const classes = new Map();
  const relatedButtons = [];

  function element(id) {
    if (!elements.has(id)) {
      if (!classes.has(id)) classes.set(id, new Set());
      const set = classes.get(id);
      elements.set(id, {
        innerHTML: "", textContent: "", value: "", scrollTop: 0,
        classList: {
          add: (...names) => names.forEach(n => set.add(n)),
          remove: (...names) => names.forEach(n => set.delete(n)),
          contains: name => set.has(name),
        },
        addEventListener() {}, removeEventListener() {},
        setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; },
      });
    }
    return elements.get(id);
  }

  globalThis.document = {
    getElementById: element,
    querySelectorAll: selector => (selector.includes("peek-related-item") ? relatedButtons : []),
  };
  globalThis.window = { t: (key, ...args) => [key, ...args].join(" ") };
  t.after(() => { delete globalThis.document; delete globalThis.window; });
  return { element, relatedButtons };
}

function buttonForMemory(memoryId) {
  const handlers = {};
  return {
    dataset: { memoryId: String(memoryId) },
    addEventListener(type, callback) { handlers[type] = callback; },
    click() { handlers.click(); },
  };
}

test("a node lists the memories it connects to and opens them on click", async t => {
  const { element, relatedButtons } = environment(t);
  relatedButtons.push(buttonForMemory(1), buttonForMemory(2));

  const panel = new PeekPanel({ isEditing: false, _detailGeneration: 0 }, {});
  const opened = [];
  panel.renderMemory = memory => opened.push(memory);

  await panel.renderNode({
    id: 10, type: "person", label: "Alice", degree: 3, entry_count: 2, weight: 1.5,
    memories: [
      { memory_id: 1, summary: "likes coffee" },
      { memory_id: 2, summary: "has a cat" },
    ],
  });

  const html = element("peek-body").innerHTML;
  assert.match(html, /peek-related-item/);
  assert.match(html, /likes coffee/);
  assert.match(html, /has a cat/);
  assert.doesNotMatch(html, /detail\.nodeMemories[^<]*<\/span><span class="peek-meta-value/, "计数项不再与列表重复");

  relatedButtons[1].click();
  assert.equal(opened.length, 1);
  assert.equal(opened[0].memory_id, 2);
});

test("selecting a node keeps the graph usable instead of covering it with an overlay", async t => {
  const { element } = environment(t);
  const panel = new PeekPanel({ isEditing: false, _detailGeneration: 0 }, {});

  await panel.renderNode({ id: 10, type: "person", label: "Alice", memories: [] });
  assert.equal(element("peek-panel").classList.contains("visible"), true);
  assert.equal(element("peek-overlay").classList.contains("visible"), false, "图谱页选中节点不应加遮罩");

  panel.open(true);
  assert.equal(element("peek-overlay").classList.contains("visible"), true, "记忆详情仍保持模态");
});
