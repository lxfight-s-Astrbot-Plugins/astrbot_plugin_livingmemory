import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../pages/dashboard/appearance.js", import.meta.url), "utf8");

function boot({ saved = {}, dark = false, blocked = false } = {}) {
  const storage = new Map(Object.entries(saved));
  const events = {};
  const inputs = [
    ...["editorial", "studio", "paper", "terminal"].map(value => ({ name: "appearance-style", value })),
    ...["system", "light", "dark"].map(value => ({ name: "appearance-mode", value })),
  ];
  const dialog = {
    addEventListener: (name, callback) => { events[name] = callback; },
    querySelectorAll: selector => inputs.filter(input => selector.includes(input.name)),
    showModal() {},
  };
  const root = { dataset: {}, style: {} };
  const media = {
    matches: dark,
    addEventListener: (_, callback) => { events.media = callback; },
  };
  const window = {
    matchMedia: () => media,
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  vm.runInNewContext(source, {
    window,
    document: {
      documentElement: root,
      getElementById: id => id === "appearance-dialog" ? dialog : { addEventListener() {} },
    },
    localStorage: {
      getItem(key) { if (blocked) throw new Error("Storage denied"); return storage.get(key); },
      setItem(key, value) { if (blocked) throw new Error("Storage denied"); storage.set(key, value); },
    },
  });
  return { appearance: window.LMAppearance, root, storage, events, inputs, media };
}

test("saved style and legacy light/dark preference apply before UI initialization", () => {
  const { appearance, root } = boot({ saved: { lmem_style: "paper", lmem_theme: "dark" } });
  assert.equal(root.dataset.style, "paper");
  assert.equal(root.dataset.theme, "dark");
  assert.equal(root.style.colorScheme, "dark");
  appearance.setContext({ isDark: false });
  assert.equal(root.dataset.theme, "dark", "Host updates must respect an explicit selection");
});

test("auto follows device until host context arrives, and continues following host updates", () => {
  const { appearance, root, media, events } = boot({ dark: true });
  assert.equal(root.dataset.theme, "dark");
  media.matches = false;
  events.media();
  assert.equal(root.dataset.theme, "light");
  appearance.setContext({ isDark: true });
  events.media();
  assert.equal(root.dataset.theme, "dark");
  appearance.setContext({ locale: "en" });
  assert.equal(root.dataset.theme, "dark");
  appearance.setContext({ isDark: false });
  assert.equal(root.dataset.theme, "light");
});

test("style and mode change independently, persist, and return to host following", () => {
  const { appearance, root, events, inputs, storage } = boot();
  appearance.init();
  appearance.setContext({ isDark: true });
  events.change({ target: { name: "appearance-mode", value: "light" } });
  events.change({ target: { name: "appearance-style", value: "terminal" } });
  assert.equal(root.dataset.style, "terminal");
  assert.equal(root.dataset.theme, "light");
  assert.equal(storage.get("lmem_style"), "terminal");
  assert.equal(storage.get("lmem_theme"), "light");
  assert.deepEqual(inputs.filter(input => input.checked).map(input => input.value), ["terminal", "light"]);
  events.change({ target: { name: "appearance-mode", value: "system" } });
  assert.equal(root.dataset.theme, "dark");
  assert.equal(root.dataset.style, "terminal");
});

test("invalid saved preferences fall back safely and invalid control values are ignored", () => {
  const { appearance, root, events } = boot({ saved: { lmem_style: "unknown", lmem_theme: "unknown" } });
  appearance.init();
  events.change({ target: { name: "appearance-style", value: "unknown" } });
  assert.equal(root.dataset.style, "editorial");
  assert.equal(root.dataset.theme, "light");
});

test("denied storage never blocks applying an appearance for the current visit", () => {
  const { appearance, root, events } = boot({ blocked: true });
  appearance.init();
  events.change({ target: { name: "appearance-style", value: "studio" } });
  events.change({ target: { name: "appearance-mode", value: "dark" } });
  assert.equal(root.dataset.style, "studio");
  assert.equal(root.dataset.theme, "dark");
});

test("preferences and radio state stay synchronized across tabs, including storage clear", () => {
  const { appearance, root, events, inputs } = boot();
  appearance.init();
  events.storage({ key: "lmem_style", newValue: "paper" });
  events.storage({ key: "lmem_theme", newValue: "dark" });
  events.storage({ key: "unrelated", newValue: "ignored" });
  assert.equal(root.dataset.style, "paper");
  assert.equal(root.dataset.theme, "dark");
  assert.deepEqual(inputs.filter(input => input.checked).map(input => input.value), ["paper", "dark"]);
  events.storage({ key: null, newValue: null });
  assert.equal(root.dataset.style, "editorial");
  assert.equal(root.dataset.theme, "light");
  assert.deepEqual(inputs.filter(input => input.checked).map(input => input.value), ["editorial", "system"]);
});
