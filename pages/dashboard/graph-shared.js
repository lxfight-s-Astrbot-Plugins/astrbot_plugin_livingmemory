/* ================================================================
   graph-shared.js — 图谱渲染共享常量与工具
   供 graph-renderer.js / graph-interaction.js / graph-2d.js 共用，
   挂载到全局 GraphShared。
   ================================================================ */
(function(global) {
  "use strict";

  var CFG = {
    NODE_RADIUS_MIN: 4,
    NODE_RADIUS_MAX: 10,
    NODE_RADIUS_BASE: 4,
    NODE_FONT_SIZE: 11,
    NODE_META_SIZE: 9,
    EDGE_WIDTH_DEFAULT: 0.7,
    EDGE_WIDTH_ACTIVE: 1.1,
    EDGE_WIDTH_HIGHLIGHT: 1.7,
    EDGE_OPACITY_DEFAULT: 0.22,
    EDGE_OPACITY_ACTIVE: 0.5,
    EDGE_OPACITY_HIGHLIGHT: 0.76,
    PARTICLE_COUNT_DEFAULT: 1,
    PARTICLE_COUNT_ACTIVE: 2,
    PARTICLE_COUNT_HIGHLIGHT: 3,
    PARTICLE_SPEED: 0.12,
    PARTICLE_SIZE: 1.45,
    /* Force-directed layout - optimized for natural clustering */
    FORCE_ITERATIONS: 400,
    FORCE_REPULSION: 1680,
    FORCE_LINK_DISTANCE: 108,
    FORCE_LINK_STRENGTH: 0.032,
    FORCE_GRAVITY: 0.0095,
    FORCE_DAMPING: 0.82,
    FORCE_MAX_SPEED: 15,
    /* Center node is larger */
    CENTER_SCALE: 1.65,
    CENTER_MAX_RADIUS: 15,
    /* Animation */
    ANIM_SPEED: 0.075,
    IDLE_DAMPING: 0.05,
    ZOOM_MIN: 0.06,
    ZOOM_MAX: 3.5,
    ZOOM_STEP: 0.001,
    DPR_MAX: 2,
    HOVER_RADIUS: 8,
    LARGE_NODE_THRESHOLD: 1200,
    LARGE_EDGE_THRESHOLD: 4500,
    MASSIVE_NODE_THRESHOLD: 3500,
    MASSIVE_EDGE_THRESHOLD: 12000,
    AMBIENT_NODE_LIMIT: 700,
    AMBIENT_EDGE_LIMIT: 1800,
    PROGRESSIVE_LAYOUT_THRESHOLD: 60,
  };

  var TYPE_COLORS = {
    topic: "#78a94b", person: "#2a9e96", fact: "#c58c2a",
    summary: "#df6d62", other: "#74868a",
  };

  function isDark() {
    return (document.documentElement.getAttribute("data-theme") || "light") === "dark";
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }

  function performanceTier(nodeCount, edgeCount) {
    if (nodeCount >= CFG.MASSIVE_NODE_THRESHOLD || edgeCount >= CFG.MASSIVE_EDGE_THRESHOLD) return 2;
    if (nodeCount >= CFG.LARGE_NODE_THRESHOLD || edgeCount >= CFG.LARGE_EDGE_THRESHOLD) return 1;
    return 0;
  }

  function themeColor(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function hexToRgba(h, alpha) {
    var v = String(h || "#000").replace("#", "").trim();
    v = v.length === 3 ? v.split("").map(function(c) { return c + c; }).join("") : v.padEnd(6, "0").slice(0, 6);
    var r = parseInt(v.slice(0, 2), 16), g = parseInt(v.slice(2, 4), 16), b = parseInt(v.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + clamp(alpha, 0, 1) + ")";
  }

  function getPos(e, el) {
    var rect = el.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function pointToSegmentDistance(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1;
    var dy = y2 - y1;
    var len2 = dx * dx + dy * dy;
    if (!len2) return Math.sqrt((px - x1) ** 2 + (py - y1) ** 2);
    var t = clamp(((px - x1) * dx + (py - y1) * dy) / len2, 0, 1);
    var x = x1 + t * dx;
    var y = y1 + t * dy;
    return Math.sqrt((px - x) ** 2 + (py - y) ** 2);
  }

  global.GraphShared = {
    CFG: CFG,
    TYPE_COLORS: TYPE_COLORS,
    isDark: isDark,
    clamp: clamp,
    lerp: lerp,
    performanceTier: performanceTier,
    themeColor: themeColor,
    hexToRgba: hexToRgba,
    getPos: getPos,
    easeInOutCubic: easeInOutCubic,
    pointToSegmentDistance: pointToSegmentDistance,
  };
})(typeof self !== "undefined" ? self : window);
