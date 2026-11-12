/* Applied before first paint so saved preferences do not flash on startup. The panel stylesheet loads after startup. */
(() => {
  "use strict";

  const STYLES = ["macos", "studio", "paper", "terminal"];
  const MODES = ["system", "light", "dark"];
  const WALLPAPERS = ["aurora", "dusk", "forest", "graphite", "none", "image"];
  const FONTS = ["serif", "mono"];
  const RADIUS = { sharp: 0.2, round: 1.4 };
  const DENSITY = { compact: 0.85, spacious: 1.12 };
  const KEYS = { style: "lmem_style", mode: "lmem_theme", custom: "lmem_custom", css: "lmem_css", image: "lmem_wallpaper" };
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const root = document.documentElement;

  let style = "macos";
  let mode = "system";
  let custom = {};
  let css = "";
  let image = "";
  let hostDark;
  let dialog;
  let cssElement;
  let panelStyles;

  function read(key) {
    try {
      return localStorage.getItem(key);
    } catch (error) {
      // Private browsing or embedded contexts can deny storage access.
      return null;
    }
  }

  function write(key, value) {
    try {
      if (value === "" || value == null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (error) {
      // The selection still works for this visit without storage.
    }
  }

  /* Storage and imported files are the only untrusted input; keep valid fields. */
  function clean(raw) {
    const result = {};
    if (!raw || typeof raw !== "object") return result;
    if (typeof raw.accent === "string" && /^#[0-9a-f]{6}$/i.test(raw.accent)) result.accent = raw.accent.toLowerCase();
    if (WALLPAPERS.includes(raw.wallpaper)) result.wallpaper = raw.wallpaper;
    if (Number.isFinite(raw.glass)) result.glass = Math.min(100, Math.max(0, Math.round(raw.glass)));
    if (Object.hasOwn(RADIUS, raw.radius)) result.radius = raw.radius;
    if (Object.hasOwn(DENSITY, raw.density)) result.density = raw.density;
    if (FONTS.includes(raw.font)) result.font = raw.font;
    return result;
  }

  function parse(text) {
    try {
      return JSON.parse(text);
    } catch (error) {
      return null;
    }
  }

  function onAccent(hex) {
    const [r, g, b] = [1, 3, 5].map((i) => {
      const channel = parseInt(hex.slice(i, i + 2), 16) / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#111111" : "#ffffff";
  }

  function setVar(name, value) {
    if (value === undefined || value === "") root.style.removeProperty(name);
    else root.style.setProperty(name, String(value));
  }

  function setAttr(name, value) {
    if (value) root.dataset[name] = value;
    else delete root.dataset[name];
  }

  function applyCss() {
    if (!cssElement) {
      cssElement = document.createElement("style");
      cssElement.id = "lm-custom-css";
      document.head.appendChild(cssElement);
    }
    cssElement.textContent = css;
  }

  function syncUI() {
    if (!dialog) return;
    const state = {
      "appearance-style": style,
      "appearance-mode": mode,
      "appearance-accent": custom.accent || "",
      "appearance-wallpaper": custom.wallpaper || "",
      "appearance-radius": custom.radius || "",
      "appearance-density": custom.density || "",
      "appearance-font": custom.font || "",
    };
    for (const [name, value] of Object.entries(state)) {
      dialog.querySelectorAll('[name="' + name + '"]').forEach((input) => {
        input.checked = input.value === value;
      });
    }
    const glass = dialog.querySelector('[name="appearance-glass"]');
    glass.value = custom.glass ?? (parseFloat(getComputedStyle(root).getPropertyValue("--glass-strength")) || 0);
    const picker = dialog.querySelector('[name="appearance-accent-custom"]');
    picker.value = custom.accent || getComputedStyle(root).getPropertyValue("--accent").trim().slice(0, 7) || "#007aff";
    document.getElementById("appearance-wp-image").hidden = !image;
    document.getElementById("appearance-css").value = css;
  }

  function apply() {
    const theme = mode === "system" ? ((hostDark ?? media.matches) ? "dark" : "light") : mode;
    if (root.dataset.style !== style) root.dataset.style = style;
    if (root.dataset.theme !== theme) root.dataset.theme = theme;
    setAttr("wallpaper", custom.wallpaper);
    setAttr("font", custom.font);
    root.style.colorScheme = theme;
    setVar("--accent", custom.accent);
    setVar("--on-accent", custom.accent && onAccent(custom.accent));
    setVar("--glass-strength", custom.glass);
    setVar("--radius-scale", RADIUS[custom.radius]);
    setVar("--density", DENSITY[custom.density]);
    setVar("--wallpaper", custom.wallpaper === "image" && image ? 'url("' + image + '") center / cover no-repeat' : "");
    syncUI();
  }

  function setCustom(key, value) {
    custom = clean({ ...custom, [key]: value === "" ? undefined : value });
    write(KEYS.custom, Object.keys(custom).length ? JSON.stringify(custom) : "");
    apply();
  }

  function load() {
    style = "macos";
    mode = "system";
    const savedStyle = read(KEYS.style);
    const savedMode = read(KEYS.mode);
    if (STYLES.includes(savedStyle)) style = savedStyle;
    if (MODES.includes(savedMode)) mode = savedMode;
    custom = clean(parse(read(KEYS.custom)));
    css = read(KEYS.css) || "";
    image = read(KEYS.image) || "";
  }

  function reset() {
    style = "macos";
    mode = "system";
    custom = {};
    css = "";
    image = "";
    for (const key of Object.values(KEYS)) write(key, "");
    applyCss();
    apply();
  }

  function exportTheme() {
    const data = JSON.stringify({ style, mode, custom, css }, null, 2);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([data], { type: "application/json" }));
    link.download = "livingmemory-theme.json";
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function importTheme(file) {
    const data = parse(await file.text());
    const status = document.getElementById("appearance-status");
    if (!data || typeof data !== "object" || !STYLES.includes(data.style)) {
      status.textContent = window.t("appearance.importFailed");
      return;
    }
    status.textContent = "";
    style = data.style;
    if (MODES.includes(data.mode)) mode = data.mode;
    custom = clean(data.custom);
    css = typeof data.css === "string" ? data.css : "";
    write(KEYS.style, style);
    write(KEYS.mode, mode);
    write(KEYS.custom, Object.keys(custom).length ? JSON.stringify(custom) : "");
    write(KEYS.css, css);
    applyCss();
    apply();
  }

  /* Wallpapers are stored as a downscaled JPEG so they fit browser storage. */
  function loadImage(file) {
    const bitmap = new Image();
    bitmap.onload = () => {
      const scale = Math.min(1, 1920 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      image = canvas.toDataURL("image/jpeg", 0.82);
      URL.revokeObjectURL(bitmap.src);
      write(KEYS.image, image);
      setCustom("wallpaper", "image");
    };
    bitmap.src = URL.createObjectURL(file);
  }

  load();
  applyCss();
  apply();
  media.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === null || Object.values(KEYS).includes(event.key)) {
      load();
      applyCss();
      apply();
    }
  });

  window.LMAppearance = {
    setContext(context) {
      if (context && typeof context.isDark === "boolean") {
        hostDark = context.isDark;
        apply();
      }
    },
    init() {
      dialog = document.getElementById("appearance-dialog");
      panelStyles = new Promise((resolve) => {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = new URL("./assets/appearance.min.css", document.baseURI).href;
        link.onload = link.onerror = resolve;
        document.head.insertBefore(link, cssElement);
      });
      document.getElementById("theme-toggle").addEventListener("click", async () => {
        await panelStyles;
        syncUI();
        dialog.showModal();
      });
      dialog.addEventListener("input", (event) => {
        const { name, value, files } = event.target;
        if (name === "appearance-style" && STYLES.includes(value)) {
          style = value;
          write(KEYS.style, value);
          apply();
        } else if (name === "appearance-mode" && MODES.includes(value)) {
          mode = value;
          write(KEYS.mode, value);
          apply();
        } else if (name === "appearance-accent" || name === "appearance-accent-custom") {
          setCustom("accent", value);
        } else if (name === "appearance-wallpaper") {
          setCustom("wallpaper", value);
        } else if (name === "appearance-glass") {
          setCustom("glass", Number(value));
        } else if (name === "appearance-radius") {
          setCustom("radius", value);
        } else if (name === "appearance-density") {
          setCustom("density", value);
        } else if (name === "appearance-font") {
          setCustom("font", value);
        } else if (name === "appearance-css") {
          css = value;
          write(KEYS.css, css);
          applyCss();
        } else if (name === "appearance-wallpaper-file" && files[0]) {
          loadImage(files[0]);
        }
      });
      document.getElementById("appearance-export").addEventListener("click", exportTheme);
      document.getElementById("appearance-reset").addEventListener("click", reset);
      const importFile = document.getElementById("appearance-import-file");
      document.getElementById("appearance-import").addEventListener("click", () => importFile.click());
      importFile.addEventListener("change", () => {
        if (importFile.files[0]) importTheme(importFile.files[0]);
        importFile.value = "";
      });
      apply();
    },
  };
})();
