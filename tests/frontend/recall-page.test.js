import assert from "node:assert/strict";
import test from "node:test";
import { RecallPage } from "../../pages/dashboard/modules/recall-page.js";

// 最小 DOM 替身：只实现 recall 页面用到的成员
function environment(t) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        value: "", disabled: false, hidden: false, innerHTML: "", textContent: "", readOnly: false,
        classList: {
          add: (...names) => names.forEach(n => classes.add(n)),
          remove: (...names) => names.forEach(n => classes.delete(n)),
          contains: name => classes.has(name),
        },
        addEventListener() {}, removeEventListener() {},
        setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; },
        replaceChildren() { this.innerHTML = ""; },
        querySelectorAll: () => [],
      });
    }
    return elements.get(id);
  }
  globalThis.document = { getElementById: element, querySelectorAll: () => [] };
  globalThis.window = { t: (key, ...args) => [key, ...args].join(" ") };
  t.after(() => { delete globalThis.document; delete globalThis.window; });
  return element;
}

test("only the retrieval routes that contributed are shown", async t => {
  const el = environment(t);
  const page = new RecallPage({}, {}, {});
  page.renderResults({
    results: [{
      memory_id: 42,
      content: "hello",
      similarity_score: 0.8123,
      metadata: { importance: 0.5, memory_type: "FACT", status: "active" },
      score_breakdown: {
        document_keyword_score: 0.31,
        document_vector_score: 0,
        graph_keyword_score: 0.12,
        graph_vector_score: 0,
      },
    }],
    total: 1,
    k: 5,
    elapsed_time_ms: 120,
  }, 120);

  const html = el("recall-results").innerHTML;
  assert.match(html, /graph\.scoreDocKW/);
  assert.match(html, /0\.310/);
  assert.match(html, /graph\.scoreGraphKW/);
  assert.doesNotMatch(html, /graph\.scoreDocVec/);
  assert.doesNotMatch(html, /graph\.scoreGraphVec/);
});

test("a result without a score breakdown renders no route row", async t => {
  const el = environment(t);
  const page = new RecallPage({}, {}, {});
  page.renderResults({
    results: [{ memory_id: 7, content: "no breakdown", similarity_score: 0.4, metadata: {} }],
    total: 1,
    k: 5,
    elapsed_time_ms: 10,
  }, 10);

  const html = el("recall-results").innerHTML;
  assert.doesNotMatch(html, /recall-result-routes/);
  assert.doesNotMatch(html, /recall-route-chip/);
});

test("the result header echoes the scope the response was produced with", async t => {
  const el = environment(t);
  const page = new RecallPage({}, {}, {});
  page.renderResults({
    results: [],
    total: 0,
    k: 3,
    session_id_filter: "sess-1",
    elapsed_time_ms: 8,
  }, 8);

  assert.equal(el("recall-context-text").textContent, "k=3 · detail.sessionId sess-1");
  assert.match(el("recall-count-text").textContent, /0/);
  assert.match(el("recall-results").innerHTML, /table-empty/);
});

test("a failed recall reports inline without a second toast", async t => {
  const el = environment(t);
  const page = new RecallPage({ _recallCache: null }, { post: async () => { throw new Error("offline"); } }, {});
  let toasts = 0;
  page.showToast = () => { toasts += 1; };
  el("recall-query").value = "query";
  el("recall-k").value = "5";
  el("recall-session").value = "";

  await page.runRecall();

  assert.equal(toasts, 0);
  assert.equal(el("recall-feedback").textContent, "offline");
  assert.equal(el("recall-search-btn").disabled, false);
  assert.equal(el("recall-query").readOnly, false);
});
