/* Applied before styles load so saved preferences do not flash on startup. */
(() => {
  "use strict";

  const styles = ["editorial", "studio", "paper", "terminal"];
  const modes = ["system", "light", "dark"];
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let style = "editorial";
  let mode = "system";
  let hostDark;
  let dialog;

  try {
    const savedStyle = localStorage.getItem("lmem_style");
    const savedMode = localStorage.getItem("lmem_theme");
    if (styles.includes(savedStyle)) style = savedStyle;
    if (modes.includes(savedMode)) mode = savedMode;
  } catch (error) {
    // Private browsing or embedded contexts can deny storage access.
  }

  function apply() {
    const theme = mode === "system"
      ? ((hostDark ?? media.matches) ? "dark" : "light")
      : mode;
    const root = document.documentElement;
    if (root.dataset.style !== style) root.dataset.style = style;
    if (root.dataset.theme !== theme) root.dataset.theme = theme;
    root.style.colorScheme = theme;
    if (dialog) {
      dialog.querySelectorAll('[name="appearance-style"]').forEach((input) => {
        input.checked = input.value === style;
      });
      dialog.querySelectorAll('[name="appearance-mode"]').forEach((input) => {
        input.checked = input.value === mode;
      });
    }
  }

  apply();
  media.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === "lmem_style" || event.key === null) {
      style = styles.includes(event.newValue) ? event.newValue : "editorial";
    }
    if (event.key === "lmem_theme" || event.key === null) {
      mode = modes.includes(event.newValue) ? event.newValue : "system";
    }
    apply();
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
      document.getElementById("theme-toggle").addEventListener("click", () => {
        dialog.showModal();
      });
      dialog.addEventListener("change", (event) => {
        const { name, value } = event.target;
        let key;
        if (name === "appearance-style" && styles.includes(value)) {
          style = value;
          key = "lmem_style";
        } else if (name === "appearance-mode" && modes.includes(value)) {
          mode = value;
          key = "lmem_theme";
        } else {
          return;
        }
        apply();
        try {
          localStorage.setItem(key, value);
        } catch (error) {
          // The selection still works for this visit without storage.
        }
      });
      apply();
    },
  };
})();
