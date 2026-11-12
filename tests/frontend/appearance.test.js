import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../pages/dashboard/appearance.js", import.meta.url), "utf8");

function boot({ saved = {}, dark = false, blocked = false } = {}) {
  const storage = new Map(Object.entries(saved));
  const events = {};
  const elements = {};
  const names = ["appearance-style", "appearance-mode", "appearance-accent", "appearance-wallpaper", "appearance-radius", "appearance-density", "appearance-font"];
  const values = {
    "appearance-style": ["macos", "studio", "paper", "terminal"],
    "appearance-mode": ["system", "light", "dark"],
    "appearance-accent": ["", "#007aff"],
    "appearance-wallpaper": ["", "dusk"],
    "appearance-radius": ["", "sharp", "round"],
    "appearance-density": ["compact", "", "spacious"],
    "appearance-font": ["", "serif", "mono"],
  };
  const inputs = names.flatMap(name => values[name].map(value => ({ name, value, checked: false })));
  const glass = { value: "" };
  const picker = { value: "" };
  const dialog = {
    addEventListener: (name, callback) => { events[name] = callback; },
    querySelectorAll: selector => inputs.filter(input => selector.includes('"' + input.name + '"')),
    querySelector: selector => (selector.includes("appearance-glass") ? glass : picker),
    showModal() {},
  };
  const element = id => (elements[id] ??= {
    id, value: "", hidden: false, textContent: "", files: [],
    addEventListener(name, callback) { events[id + ":" + name] = callback; },
    click() {},
  });
  elements["appearance-dialog"] = dialog;
  const vars = {};
  const head = [];
  const root = {
    dataset: {},
    style: {
      setProperty: (name, value) => { vars[name] = value; },
      removeProperty: name => { delete vars[name]; },
    },
  };
  const media = { matches: dark, addEventListener: (_, callback) => { events.media = callback; } };
  const window = {
    matchMedia: () => media,
    addEventListener: (name, callback) => { events[name] = callback; },
    t: key => key,
  };
  const context = {
    window,
    URL,
    getComputedStyle: () => ({ getPropertyValue: name => (name === "--glass-strength" ? "60" : "#007aff") }),
    document: {
      documentElement: root,
      baseURI: "http://localhost/",
      head: { appendChild: node => head.push(node), insertBefore: node => head.push(node) },
      createElement: () => ({}),
      getElementById: element,
    },
    localStorage: {
      getItem(key) { if (blocked) throw new Error("Storage denied"); return storage.get(key) ?? null; },
      setItem(key, value) { if (blocked) throw new Error("Storage denied"); storage.set(key, value); },
      removeItem(key) { if (blocked) throw new Error("Storage denied"); storage.delete(key); },
    },
  };
  vm.runInNewContext(source, context);
  const input = (name, value) => events.input({ target: { name, value } });
  return { appearance: window.LMAppearance, root, vars, head, storage, events, inputs, input, element, glass, picker };
}

const checked = inputs => inputs.filter(input => input.checked).map(input => input.name.replace("appearance-", "") + ":" + input.value);

test("saved style, mode and custom values apply before UI initialization", () => {
  const { appearance, root, vars, head } = boot({
    saved: {
      lmem_style: "paper", lmem_theme: "dark", lmem_css: ".btn{color:red}",
      lmem_custom: JSON.stringify({ accent: "#ff9500", glass: 20, radius: "round", density: "compact", wallpaper: "dusk", font: "mono" }),
    },
  });
  assert.equal(root.dataset.style, "paper");
  assert.equal(root.dataset.theme, "dark");
  assert.equal(root.dataset.wallpaper, "dusk");
  assert.equal(root.dataset.font, "mono");
  assert.equal(vars["--accent"], "#ff9500");
  assert.equal(vars["--glass-strength"], "20");
  assert.equal(vars["--radius-scale"], "1.4");
  assert.equal(vars["--density"], "0.85");
  assert.equal(head[0].textContent, ".btn{color:red}");
  appearance.setContext({ isDark: false });
  assert.equal(root.dataset.theme, "dark", "Host updates must respect an explicit selection");
});

test("auto follows device until host context arrives, and continues following host updates", () => {
  const { appearance, root, events, events: { media } } = boot({ dark: true });
  assert.equal(root.dataset.theme, "dark");
  events.media = media;
  appearance.setContext({ isDark: false });
  assert.equal(root.dataset.theme, "light");
  appearance.setContext({ locale: "en" });
  assert.equal(root.dataset.theme, "light");
  appearance.setContext({ isDark: true });
  assert.equal(root.dataset.theme, "dark");
});

test("style and mode change independently, persist, and return to host following", () => {
  const { appearance, root, input, inputs, storage } = boot();
  appearance.init();
  appearance.setContext({ isDark: true });
  input("appearance-mode", "light");
  input("appearance-style", "terminal");
  assert.equal(root.dataset.style, "terminal");
  assert.equal(root.dataset.theme, "light");
  assert.equal(storage.get("lmem_style"), "terminal");
  assert.equal(storage.get("lmem_theme"), "light");
  assert.deepEqual(checked(inputs).filter(item => /^(style|mode)/.test(item)), ["style:terminal", "mode:light"]);
  input("appearance-mode", "system");
  assert.equal(root.dataset.theme, "dark");
});

test("custom controls set tokens, persist, and clear back to the preset", () => {
  const { appearance, root, vars, input, storage } = boot();
  appearance.init();
  input("appearance-accent", "#ffcc00");
  assert.equal(vars["--accent"], "#ffcc00");
  assert.equal(vars["--on-accent"], "#111111", "light accents get dark label text");
  input("appearance-accent", "#003366");
  assert.equal(vars["--on-accent"], "#ffffff");
  input("appearance-glass", "0");
  input("appearance-radius", "sharp");
  input("appearance-density", "spacious");
  input("appearance-wallpaper", "forest");
  input("appearance-font", "serif");
  assert.equal(vars["--glass-strength"], "0");
  assert.equal(vars["--radius-scale"], "0.2");
  assert.equal(vars["--density"], "1.12");
  assert.equal(root.dataset.wallpaper, "forest");
  assert.equal(root.dataset.font, "serif");
  assert.deepEqual(JSON.parse(storage.get("lmem_custom")), { accent: "#003366", glass: 0, radius: "sharp", density: "spacious", wallpaper: "forest", font: "serif" });
  for (const name of ["accent", "radius", "density", "wallpaper", "font"]) input("appearance-" + name, "");
  assert.equal(vars["--accent"], undefined);
  assert.equal(vars["--radius-scale"], undefined);
  assert.equal(root.dataset.wallpaper, undefined);
  assert.equal(root.dataset.font, undefined);
  assert.deepEqual(JSON.parse(storage.get("lmem_custom")), { glass: 0 });
});

test("custom CSS applies live and persists; reset restores every default", () => {
  const { appearance, root, vars, head, input, storage, events, element } = boot();
  appearance.init();
  input("appearance-css", "body{outline:1px solid red}");
  assert.equal(head[0].textContent, "body{outline:1px solid red}");
  assert.equal(storage.get("lmem_css"), "body{outline:1px solid red}");
  input("appearance-style", "studio");
  input("appearance-accent", "#ff3b30");
  events["appearance-reset:click"]();
  assert.equal(root.dataset.style, "macos");
  assert.equal(vars["--accent"], undefined);
  assert.equal(element("appearance-css").value, "");
  assert.equal(head[0].textContent, "");
  assert.equal(storage.size, 0);
});

test("theme files import valid presets and report invalid ones", async () => {
  const { appearance, root, vars, events, element, storage } = boot();
  appearance.init();
  const file = text => ({ text: async () => text });
  const importFile = element("appearance-import-file");
  importFile.files = [file("not json")];
  events["appearance-import-file:change"]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(element("appearance-status").textContent, "appearance.importFailed");
  importFile.files = [file(JSON.stringify({ style: "paper", mode: "dark", custom: { accent: "#34c759", bogus: 1 }, css: "a{}" }))];
  events["appearance-import-file:change"]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(root.dataset.style, "paper");
  assert.equal(root.dataset.theme, "dark");
  assert.equal(vars["--accent"], "#34c759");
  assert.equal(element("appearance-status").textContent, "");
  assert.deepEqual(JSON.parse(storage.get("lmem_custom")), { accent: "#34c759" });
  assert.equal(storage.get("lmem_css"), "a{}");
});

test("invalid saved preferences fall back and invalid control values are ignored", () => {
  const { appearance, root, vars, input } = boot({
    saved: { lmem_style: "editorial", lmem_theme: "unknown", lmem_custom: JSON.stringify({ accent: "red", glass: "x", radius: "huge", wallpaper: "x" }) },
  });
  appearance.init();
  input("appearance-style", "unknown");
  assert.equal(root.dataset.style, "macos");
  assert.equal(root.dataset.theme, "light");
  assert.deepEqual(Object.keys(vars).filter(name => name !== "--on-accent" || vars[name]), []);
});

test("denied storage never blocks applying an appearance for the current visit", () => {
  const { appearance, root, input } = boot({ blocked: true });
  appearance.init();
  input("appearance-style", "studio");
  input("appearance-mode", "dark");
  assert.equal(root.dataset.style, "studio");
  assert.equal(root.dataset.theme, "dark");
});

test("preferences and radio state stay synchronized across tabs, including storage clear", () => {
  const { appearance, root, events, inputs, storage } = boot();
  appearance.init();
  storage.set("lmem_style", "paper");
  storage.set("lmem_theme", "dark");
  events.storage({ key: "lmem_style" });
  assert.equal(root.dataset.style, "paper");
  assert.equal(root.dataset.theme, "dark");
  assert.deepEqual(checked(inputs).filter(item => /^(style|mode)/.test(item)), ["style:paper", "mode:dark"]);
  events.storage({ key: "unrelated" });
  storage.clear();
  events.storage({ key: null });
  assert.equal(root.dataset.style, "macos");
  assert.deepEqual(checked(inputs).filter(item => /^(style|mode)/.test(item)), ["style:macos", "mode:system"]);
});
