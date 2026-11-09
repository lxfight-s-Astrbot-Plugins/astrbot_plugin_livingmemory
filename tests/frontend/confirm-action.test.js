import assert from "node:assert/strict";
import test from "node:test";

import { confirmAction } from "../../pages/dashboard/modules/utils.js";

function fakeDialog() {
  const listeners = {};
  return {
    open: false,
    returnValue: "cancel",
    showModal() {
      this.open = true;
    },
    addEventListener(type, callback) {
      (listeners[type] = listeners[type] || []).push(callback);
    },
    close() {
      this.open = false;
      (listeners.close || []).forEach((callback) => callback());
    },
  };
}

function installDom() {
  const dialog = fakeDialog();
  const classes = new Set();
  const confirmButton = {
    textContent: "",
    classList: {
      toggle(name, enabled) {
        if (enabled) classes.add(name);
        else classes.delete(name);
      },
    },
  };
  const elements = {
    "action-dialog": dialog,
    "action-dialog-title": { textContent: "" },
    "action-dialog-message": { textContent: "" },
    "action-dialog-confirm": confirmButton,
  };
  const previous = { focused: 0, focus() { this.focused += 1; } };
  globalThis.window = { t: (key) => key };
  globalThis.document = {
    activeElement: previous,
    getElementById: (id) => elements[id] || null,
  };
  return { dialog, confirmButton, classes, elements, previous };
}

test("confirmAction shows a native dialog, resolves true and restores focus", async () => {
  const dom = installDom();
  try {
    const pending = confirmAction({
      title: "Risky",
      message: "This deletes data",
      destructive: false,
    });

    assert.equal(dom.dialog.open, true, "应通过 showModal 打开原生 dialog");
    assert.equal(dom.elements["action-dialog-title"].textContent, "Risky");
    assert.equal(dom.elements["action-dialog-message"].textContent, "This deletes data");
    assert.equal(dom.confirmButton.textContent, "common.confirm");
    assert.equal(dom.classes.has("btn-primary"), true);
    assert.equal(dom.classes.has("btn-danger"), false);

    dom.dialog.returnValue = "confirm";
    dom.dialog.close();

    assert.equal(await pending, true);
    assert.equal(dom.previous.focused, 1, "关闭后应把焦点还给触发元素");
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});

test("confirmAction resolves false when cancelled or the dialog is missing", async () => {
  const dom = installDom();
  try {
    const pending = confirmAction({ title: "T", message: "M" });
    assert.equal(dom.classes.has("btn-danger"), true, "destructive 默认使用危险按钮");
    dom.dialog.returnValue = "cancel";
    dom.dialog.close();
    assert.equal(await pending, false);
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }

  globalThis.window = { t: (key) => key };
  globalThis.document = { getElementById: () => null };
  try {
    assert.equal(await confirmAction({ title: "T", message: "M" }), false);
  } finally {
    delete globalThis.document;
    delete globalThis.window;
  }
});
