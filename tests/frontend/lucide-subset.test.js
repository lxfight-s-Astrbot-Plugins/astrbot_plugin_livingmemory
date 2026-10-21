import test from "node:test";
import assert from "node:assert/strict";
import "../../pages/dashboard/vendor/lucide-subset.js";

function createDocumentStub(placeholders) {
  return {
    querySelectorAll(selector) {
      assert.equal(selector, "[data-lucide]");
      return placeholders;
    },
    createElementNS(namespace, tag) {
      return {
        namespace,
        tag,
        attrs: {},
        children: [],
        setAttribute(name, value) {
          this.attrs[name] = String(value);
        },
        appendChild(child) {
          this.children.push(child);
        },
      };
    },
  };
}

function createPlaceholder(name, className) {
  const attributes = [{ name: "data-lucide", value: name }];
  if (className) attributes.push({ name: "class", value: className });
  attributes.push({ name: "aria-hidden", value: "true" });
  return {
    attributes,
    replacedWith: null,
    getAttribute(key) {
      const attr = attributes.find((item) => item.name === key);
      return attr ? attr.value : null;
    },
    parentNode: {
      replaceChild(svg, element) {
        element.replacedWith = svg;
      },
    },
  };
}

test("createIcons replaces a used placeholder with an svg carrying classes and attrs", () => {
  const placeholder = createPlaceholder("x", "btn-icon");
  globalThis.document = createDocumentStub([placeholder]);

  globalThis.lucide.createIcons({ attrs: { "stroke-width": 1.7 } });

  const svg = placeholder.replacedWith;
  assert.ok(svg, "placeholder should be replaced");
  assert.equal(svg.tag, "svg");
  assert.equal(svg.attrs.class, "lucide lucide-x btn-icon");
  assert.equal(svg.attrs["stroke-width"], "1.7");
  assert.equal(svg.attrs["aria-hidden"], "true");
  assert.ok(svg.children.length > 0);
  assert.equal(svg.children[0].tag, "path");
});

test("createIcons leaves unknown icon names untouched", () => {
  const placeholder = createPlaceholder("not-an-icon");
  globalThis.document = createDocumentStub([placeholder]);

  globalThis.lucide.createIcons();

  assert.equal(placeholder.replacedWith, null);
});
