import assert from "node:assert/strict";
import test from "node:test";

import { PromptPage } from "../../pages/dashboard/modules/prompt-page.js";

const PROMPTS = [
  {
    id: "group_chat_prompt",
    name: "群聊记忆总结 Prompt",
    name_en: "Group Chat Memory Prompt",
    description: "群聊场景下总结对话历史",
    description_en: "Summarize group chat history",
    usage_note: "必须输出 JSON",
    usage_note_en: "MUST output JSON",
    category: "memory_processing",
    variables: ["{conversation}"],
    is_custom: false,
  },
];

const CATEGORIES = [
  {
    id: "memory_processing",
    name: "记忆处理",
    name_en: "Memory Processing",
    description: "控制记忆提取与结构化总结的提示词",
    description_en: "Prompts controlling memory extraction",
  },
];

function setupDom(lang) {
  const container = { innerHTML: "", querySelectorAll() { return []; } };
  globalThis.window = {
    getLanguage() {
      return lang;
    },
    t(key) {
      return key;
    },
  };
  globalThis.document = {
    createElement() {
      let text = "";
      return {
        set textContent(value) {
          text = String(value);
        },
        get innerHTML() {
          return text;
        },
      };
    },
    getElementById(id) {
      return id === "prompt-content" ? container : null;
    },
  };
  return container;
}

function teardownDom() {
  delete globalThis.document;
  delete globalThis.window;
}

function renderWithLanguage(lang) {
  const container = setupDom(lang);
  try {
    const page = new PromptPage({}, {});
    page.prompts = PROMPTS;
    page.categories = CATEGORIES;
    page.render();
    return container.innerHTML;
  } finally {
    teardownDom();
  }
}

test("prompt list shows Chinese names with English subtitles in zh", () => {
  const html = renderWithLanguage("zh");
  assert.match(html, /群聊记忆总结 Prompt/);
  assert.match(html, /Group Chat Memory Prompt/);
  assert.match(html, /记忆处理/);
  assert.match(html, /群聊场景下总结对话历史/);
  assert.match(html, /必须输出 JSON/);
});

test("prompt list shows only English text in en", () => {
  const html = renderWithLanguage("en");
  assert.match(html, /Group Chat Memory Prompt/);
  assert.match(html, /Memory Processing/);
  assert.match(html, /Summarize group chat history/);
  assert.match(html, /MUST output JSON/);
  assert.doesNotMatch(html, /群聊/);
  assert.doesNotMatch(html, /记忆处理/);
});

test("prompt list falls back to Chinese when English fields are missing", () => {
  const container = setupDom("ru");
  try {
    const page = new PromptPage({}, {});
    page.prompts = [{ ...PROMPTS[0], description_en: "", usage_note_en: "" }];
    page.categories = CATEGORIES;
    page.render();
  } finally {
    teardownDom();
  }

  assert.match(container.innerHTML, /Group Chat Memory Prompt/);
  assert.match(container.innerHTML, /群聊场景下总结对话历史/);
});
