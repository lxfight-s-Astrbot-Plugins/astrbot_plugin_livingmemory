(() => {
  "use strict";

  /* ================================================================
     Graph2D — Circular Knowledge Graph
     Center node radiates outward with circular nodes and straight links
     ================================================================ */

  /* ── Configuration ─────────────────────────────────────────── */
  const CFG = {
    NODE_RADIUS_MIN: 4,
    NODE_RADIUS_MAX: 10,
    NODE_RADIUS_BASE: 4,
    NODE_FONT_SIZE: 11,
    NODE_META_SIZE: 9,
    EDGE_WIDTH_DEFAULT: 0.7,
    EDGE_WIDTH_ACTIVE: 1.1,
    EDGE_WIDTH_HIGHLIGHT: 1.7,
    EDGE_OPACITY_DEFAULT: 0.2,
    EDGE_OPACITY_ACTIVE: 0.44,
    EDGE_OPACITY_HIGHLIGHT: 0.76,
    PARTICLE_COUNT_DEFAULT: 0,
    PARTICLE_COUNT_ACTIVE: 0,
    PARTICLE_COUNT_HIGHLIGHT: 0,
    PARTICLE_SPEED: 0.18,
    PARTICLE_SIZE: 2.0,
    /* Radial layout */
    RING1_RADIUS: 160,
    RING2_RADIUS: 300,
    RING3_RADIUS: 440,
    RING_GAP: 10,
    /* Center node is larger */
    CENTER_SCALE: 1.65,
    CENTER_MAX_RADIUS: 15,
    /* Animation */
    ANIM_SPEED: 0.075,
    IDLE_DAMPING: 0.05,
    ZOOM_MIN: 0.2,
    ZOOM_MAX: 3.5,
    ZOOM_STEP: 0.001,
    DPR_MAX: 2,
    HOVER_RADIUS: 8,
  };

  const TYPE_COLORS = {
    topic: "#7c6fca", person: "#2f9e8b", fact: "#c99a16",
    summary: "#c8648d", other: "#8b949e",
  };

  /* ── CSS helpers ───────────────────────────────────────────── */
  function isDark() {
    return (document.documentElement.getAttribute("data-theme") || "light") === "dark";
  }

  /* ── Math helpers ──────────────────────────────────────────── */
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }

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

  /* ── Event helpers ─────────────────────────────────────────── */
  function getPos(e, el) {
    var rect = el.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  /* ═══════════════════════════════════════════════════════════════
     RadialLayout — computes hub-spoke positions
     ═══════════════════════════════════════════════════════════════ */
  function RadialLayout() {
    this.centerId = null;
    this.positions = {};  // id → {tx, ty}
    this.rings = {};      // id → ring number (0=center, 1,2,3)
  }

  /* Build adjacency map from edges */
  RadialLayout.prototype._buildAdjacency = function(nodes, edges) {
    var adj = {};
    nodes.forEach(function(n) { adj[n.id] = []; });
    edges.forEach(function(e) {
      if (adj[e.source]) adj[e.source].push(e.target);
      if (adj[e.target]) adj[e.target].push(e.source);
    });
    return adj;
  };

  /* Compute radial target positions */
  RadialLayout.prototype.compute = function(nodes, edges, centerId) {
    var self = this;
    this.centerId = centerId;
    this.positions = {};
    this.rings = {};

    var n = nodes.length;
    if (n === 0) return;
    if (n === 1) {
      this.rings[nodes[0].id] = 0;
      this.positions[nodes[0].id] = { tx: 0, ty: 0 };
      this.centerId = nodes[0].id;
      return;
    }

    /* Pick center: use provided centerId, or highest-weight node, or first */
    var centerNode = null;
    if (centerId != null) {
      centerNode = nodes.find(function(nd) { return nd.id === centerId; });
    }
    if (!centerNode) {
      /* Highest weight */
      var bestW = -1;
      nodes.forEach(function(nd) {
        if (nd.weight > bestW) { bestW = nd.weight; centerNode = nd; }
      });
    }
    if (!centerNode) centerNode = nodes[0];

    var adj = this._buildAdjacency(nodes, edges);
    var nodeMap = {};
    nodes.forEach(function(nd) { nodeMap[nd.id] = nd; });

    /* BFS from center to assign rings */
    var visited = {};
    var queue = [{ id: centerNode.id, ring: 0 }];
    visited[centerNode.id] = 0;

    while (queue.length > 0) {
      var curr = queue.shift();
      this.rings[curr.id] = curr.ring;
      var neighbors = adj[curr.id] || [];
      neighbors.forEach(function(nid) {
        if (!(nid in visited)) {
          visited[nid] = curr.ring + 1;
          queue.push({ id: nid, ring: curr.ring + 1 });
        }
      });
    }

    /* Assign any unvisited nodes to ring 3 */
    nodes.forEach(function(nd) {
      if (!(nd.id in self.rings)) self.rings[nd.id] = 3;
    });

    /* Clamp rings: 0,1,2,3 */
    nodes.forEach(function(nd) {
      var ring = self.rings[nd.id];
      self.rings[nd.id] = Math.min(3, ring == null ? 3 : ring);
    });

    /* Group nodes by ring */
    var ringGroups = { 0: [], 1: [], 2: [], 3: [] };
    nodes.forEach(function(nd) {
      var r = self.rings[nd.id];
      if (r === 0) {
        ringGroups[0].push(nd);
      } else {
        ringGroups[r].push(nd);
      }
    });

    /* Place center */
    if (ringGroups[0].length > 0) {
      ringGroups[0].forEach(function(nd) {
        self.positions[nd.id] = { tx: 0, ty: 0 };
      });
    }

    /* Place ring nodes: group by type within each ring, then spread evenly */
    var radii = [0, CFG.RING1_RADIUS, CFG.RING2_RADIUS, CFG.RING3_RADIUS];

    for (var r = 1; r <= 3; r++) {
      var ringNodes = ringGroups[r];
      if (ringNodes.length === 0) continue;

      /* Sub-group by type for visual clustering */
      var typeGroups = {};
      ringNodes.forEach(function(nd) {
        var t = nd.type || "other";
        if (!typeGroups[t]) typeGroups[t] = [];
        typeGroups[t].push(nd);
      });

      /* Flatten type groups, interleaving types */
      var ordered = [];
      var typeKeys = Object.keys(typeGroups);
      /* Sort type groups by size (largest first) */
      typeKeys.sort(function(a, b) { return typeGroups[b].length - typeGroups[a].length; });

      var maxLen = 0;
      typeKeys.forEach(function(t) { maxLen = Math.max(maxLen, typeGroups[t].length); });

      /* Interleave: take one from each type group in round-robin */
      for (var i = 0; i < maxLen; i++) {
        typeKeys.forEach(function(t) {
          if (i < typeGroups[t].length) ordered.push(typeGroups[t][i]);
        });
      }

      /* Place in circle */
      var count = ordered.length;
      var radius = radii[r];
      ordered.forEach(function(nd, i) {
        var angle = (2 * Math.PI * i) / count - Math.PI / 2;
        /* Slight radius variation within ring */
        var rVar = radius + (i % 3) * CFG.RING_GAP;
        self.positions[nd.id] = {
          tx: Math.cos(angle) * rVar,
          ty: Math.sin(angle) * rVar,
        };
      });
    }
  };

  /* Get target position for a node */
  RadialLayout.prototype.getTarget = function(nodeId) {
    var p = this.positions[nodeId];
    return p || { tx: 0, ty: 0 };
  };

  /* Get ring of a node (0=center) */
  RadialLayout.prototype.getRing = function(nodeId) {
    return this.rings[nodeId] != null ? this.rings[nodeId] : 3;
  };

  /* ═══════════════════════════════════════════════════════════════
     Renderer — Canvas 2D drawing
     ═══════════════════════════════════════════════════════════════ */
  function Renderer(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.viewport = { ox: 0, oy: 0, scale: 1 };
    this.width = 0;
    this.height = 0;
    this.dpr = 1;
    this._drawnNodes = [];
    this._drawnEdges = [];
    this._particleOffsets = {};
    this._selection = null;
  }

  Renderer.prototype.resize = function() {
    var rect = this.canvas.parentElement.getBoundingClientRect();
    var w = Math.max(1, Math.floor(rect.width || this.canvas.parentElement.clientWidth || 1));
    var h = Math.max(320, Math.floor(rect.height || this.canvas.parentElement.clientHeight || 320));
    this.dpr = Math.min(window.devicePixelRatio || 1, CFG.DPR_MAX);
    this.width = w;
    this.height = h;
    this.canvas.width = w * this.dpr;
    this.canvas.height = h * this.dpr;
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  };

  Renderer.prototype.clear = function() {
    this.ctx.clearRect(0, 0, this.width, this.height);
  };

  Renderer.prototype.drawBackground = function(dark) {
    var ctx = this.ctx;
    var step = clamp(30 * this.viewport.scale, 22, 42);
    var ox = ((this.viewport.ox * this.viewport.scale) % step + step) % step;
    var oy = ((this.viewport.oy * this.viewport.scale) % step + step) % step;

    ctx.save();
    ctx.fillStyle = dark ? "#202126" : themeColor("--bg-card", "#ffffff");
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.fillStyle = dark ? "rgba(144,146,150,0.12)" : "rgba(108,117,125,0.13)";
    for (var x = ox; x <= this.width; x += step) {
      for (var y = oy; y <= this.height; y += step) {
        ctx.beginPath();
        ctx.arc(x, y, 0.75, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  };

  Renderer.prototype.worldToScreen = function(wx, wy) {
    return {
      x: (wx + this.viewport.ox) * this.viewport.scale + this.width / 2,
      y: (wy + this.viewport.oy) * this.viewport.scale + this.height / 2,
    };
  };

  Renderer.prototype.screenToWorld = function(sx, sy) {
    return {
      x: (sx - this.width / 2) / this.viewport.scale - this.viewport.ox,
      y: (sy - this.height / 2) / this.viewport.scale - this.viewport.oy,
    };
  };

  Renderer.prototype.nodeWorldRadius = function(nodeData, isCenter) {
    var w = clamp(Number(nodeData.weight || 0), 0, 20);
    var mr = clamp(Number(nodeData.memory_count || 0), 0, 15);
    var r = CFG.NODE_RADIUS_BASE + Math.sqrt(w) * 0.75 + Math.sqrt(mr) * 0.4;
    if (isCenter) {
      r = Math.min(CFG.CENTER_MAX_RADIUS, r * CFG.CENTER_SCALE);
    }
    if (nodeData.isSelected) r += 1.5;
    return clamp(r, CFG.NODE_RADIUS_MIN, isCenter ? CFG.CENTER_MAX_RADIUS : CFG.NODE_RADIUS_MAX);
  };

  Renderer.prototype.nodeScreenRadius = function(nodeData, isCenter) {
    return this.nodeWorldRadius(nodeData, isCenter) * this.viewport.scale;
  };

  Renderer.prototype.render = function(nodes, edges, nodeMap, selection, hoverId, radial, animProgress) {
    var ctx = this.ctx;
    var scale = this.viewport.scale;
    var dark = isDark();
    var selNodeId = (selection && selection.type === "node") ? selection.id : null;
    var selMemId = (selection && selection.type === "memory") ? selection.id : null;

    /* Build highlight sets */
    var highlightNodes = new Set();
    var highlightEdges = new Set();
    var adjacency = {};
    nodes.forEach(function(nd) { adjacency[nd.id] = []; });
    edges.forEach(function(e) {
      if (adjacency[e.source]) adjacency[e.source].push(e.target);
      if (adjacency[e.target]) adjacency[e.target].push(e.source);
    });

    if (selNodeId !== null) {
      highlightNodes.add(selNodeId);
      (adjacency[selNodeId] || []).forEach(function(nid) { highlightNodes.add(nid); });
    }
    if (selMemId !== null) {
      edges.forEach(function(edge) {
        if (edge.memory_id === selMemId) {
          highlightNodes.add(edge.source);
          highlightNodes.add(edge.target);
          highlightEdges.add(edge.id);
        }
      });
    }

    var centerId = radial ? radial.centerId : null;

    this.drawBackground(dark);

    /* Compute animated positions */
    var ap = animProgress || 1;

    /* Draw edges first (under nodes) */
    this._drawnEdges = [];
    ctx.save();
    for (var e = 0; e < edges.length; e++) {
      var edge = edges[e];
      var src = nodeMap[edge.source];
      var tgt = nodeMap[edge.target];
      if (!src || !tgt) continue;

      var sAnim = { x: lerp(src._prevX || src.x, src.x, ap), y: lerp(src._prevY || src.y, src.y, ap) };
      var tAnim = { x: lerp(tgt._prevX || tgt.x, tgt.x, ap), y: lerp(tgt._prevY || tgt.y, tgt.y, ap) };

      var ssp = this.worldToScreen(sAnim.x, sAnim.y);
      var tsp = this.worldToScreen(tAnim.x, tAnim.y);

      var hasFocus = highlightNodes.size > 0 || highlightEdges.size > 0;
      var isActive = !hasFocus || (highlightNodes.has(edge.source) && highlightNodes.has(edge.target));
      var isMemHl = highlightEdges.has(edge.id);
      var isMuted = hasFocus && !isActive && !isMemHl;

      var de = {
        id: edge.id, sx: ssp.x, sy: ssp.y, tx: tsp.x, ty: tsp.y,
        sourceId: edge.source, targetId: edge.target,
        relationType: edge.relation_type || "related",
        memoryId: edge.memory_id, weight: edge.weight || 1,
        isActive: isActive, isHighlighted: isMemHl,
        isMuted: isMuted, hasFocus: hasFocus,
        isHovered: edge.id === hoverId,
        color: edge.__color || TYPE_COLORS.other,
      };
      this._drawnEdges.push(de);

      if (de.isMuted) continue;
      this._drawEdge(ctx, de, dark);
    }
    ctx.restore();

    /* Particles */
    ctx.save();
    var now = Date.now() / 1000;
    for (var p = 0; p < this._drawnEdges.length; p++) {
      var de2 = this._drawnEdges[p];
      if (de2.isMuted) continue;
      this._drawParticles(ctx, de2, now, dark);
    }
    ctx.restore();

    /* Draw nodes */
    this._drawnNodes = [];
    ctx.save();
    for (var i = 0; i < nodes.length; i++) {
      var nd = nodes[i];
      /* Animated position */
      var px = lerp(nd._prevX || nd.x, nd.x, ap);
      var py = lerp(nd._prevY || nd.y, nd.y, ap);
      var sp = this.worldToScreen(px, py);

      var isCenter = centerId != null && nd.id === centerId;
      var isSel = nd.id === selNodeId;
      var isHl = highlightNodes.has(nd.id);
      var hasNodeFocus = highlightNodes.size > 0 || highlightEdges.size > 0;
      var isMuted = hasNodeFocus && !isHl && !isSel;
      var sr = this.nodeScreenRadius(nd, isCenter);

      var drawInfo = {
        id: nd.id, sx: sp.x, sy: sp.y, sr: sr,
        isSelected: isSel, isHighlighted: isHl, isMuted: isMuted,
        isHovered: nd.id === hoverId, isCenter: isCenter, hasFocus: hasNodeFocus,
        type: nd.type || "other", label: nd.label || "Unnamed",
        memoryCount: nd.memory_count || 0, degree: nd.degree || 0,
        color: TYPE_COLORS[nd.type] || TYPE_COLORS.other, fixed: nd.fixed,
      };
      this._drawnNodes.push(drawInfo);

      if (drawInfo.isMuted && !drawInfo.isHovered) {
        ctx.globalAlpha = 0.22;
        ctx.beginPath();
        ctx.arc(drawInfo.sx, drawInfo.sy, Math.max(2, drawInfo.sr * 0.62), 0, Math.PI * 2);
        ctx.fillStyle = dark ? "#5c6370" : "#c7ccd4";
        ctx.fill();
        ctx.globalAlpha = 1;
        continue;
      }
      this._drawNode(ctx, drawInfo, scale, dark);
    }
    ctx.restore();
  };

  /* Draw a single edge as a straight link */
  Renderer.prototype._drawEdge = function(ctx, de, dark) {
    var opacity = de.isHighlighted ? CFG.EDGE_OPACITY_HIGHLIGHT
      : de.hasFocus && de.isActive ? CFG.EDGE_OPACITY_ACTIVE : CFG.EDGE_OPACITY_DEFAULT;
    var width = de.isHighlighted ? CFG.EDGE_WIDTH_HIGHLIGHT
      : de.hasFocus && de.isActive ? CFG.EDGE_WIDTH_ACTIVE : CFG.EDGE_WIDTH_DEFAULT;

    if (de.isMuted) opacity *= 0.35;

    ctx.beginPath();
    ctx.moveTo(de.sx, de.sy);
    ctx.lineTo(de.tx, de.ty);
    ctx.strokeStyle = de.isHighlighted || (de.hasFocus && de.isActive)
      ? hexToRgba(de.color, opacity)
      : dark ? "rgba(150,157,168," + opacity + ")" : "rgba(91,103,120," + opacity + ")";
    ctx.lineWidth = width;
    ctx.lineCap = "round";
    ctx.stroke();
  };

  Renderer.prototype._drawParticles = function(ctx, de, now, dark) {
    if (!de.isActive && !de.isHighlighted) return;
    var count = de.isHighlighted ? CFG.PARTICLE_COUNT_HIGHLIGHT
      : de.isActive ? CFG.PARTICLE_COUNT_ACTIVE : CFG.PARTICLE_COUNT_DEFAULT;
    if (count <= 0) return;

    var key = de.id;
    if (!(key in this._particleOffsets)) this._particleOffsets[key] = Math.random();

    for (var i = 0; i < count; i++) {
      var t = ((now * CFG.PARTICLE_SPEED + this._particleOffsets[key] + i / count) % 1 + 1) % 1;
      var px = lerp(de.sx, de.tx, t);
      var py = lerp(de.sy, de.ty, t);
      ctx.beginPath();
      ctx.arc(px, py, CFG.PARTICLE_SIZE * (de.isHighlighted ? 1.35 : 1), 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(de.color, de.isHighlighted ? 0.82 : 0.46);
      ctx.fill();
    }
  };

  /* Draw a single circular node */
  Renderer.prototype._drawNode = function(ctx, dn, scale, dark) {
    var x = dn.sx, y = dn.sy, r = dn.sr;

    ctx.save();
    ctx.globalAlpha = dn.isMuted ? 0.26 : 1;

    var halo = (dn.isSelected ? 7 : dn.isHovered ? 5 : dn.isCenter ? 4 : 0) * scale;
    if (halo > 0 && !dn.isMuted) {
      ctx.beginPath();
      ctx.arc(x, y, r + halo, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(dn.color, dn.isSelected ? 0.14 : 0.08);
      ctx.fill();
    }

    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = dn.isMuted ? (dark ? "#5c6370" : "#c7ccd4") : dn.color;
    ctx.fill();

    ctx.lineWidth = dn.isSelected ? 2 : dn.isHovered || dn.isCenter ? 1.5 : 1;
    ctx.strokeStyle = dn.isSelected || dn.isHovered || dn.isCenter
      ? (dn.isMuted ? (dark ? "#6f7683" : "#b9c0ca") : dn.color)
      : dark ? "#202126" : "#ffffff";
    ctx.stroke();

    var labelVisible = dn.isHovered || dn.isSelected || dn.isCenter ||
      (!dn.hasFocus && scale > 1.05 && dn.degree >= 2);
    if (!labelVisible || dn.isMuted) {
      ctx.restore();
      return;
    }

    var fontSize = Math.max(10, CFG.NODE_FONT_SIZE * scale);
    ctx.fillStyle = dark ? "#e9ecef" : "#2f343a";
    ctx.font = (dn.isSelected || dn.isCenter ? "600 " : "500 ") + fontSize + "px -apple-system, BlinkMacSystemFont, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    var maxChars = dn.isCenter ? 28 : 24;
    var label = dn.label.length > maxChars ? dn.label.substring(0, maxChars - 1) + "…" : dn.label;
    var labelX = x + r + 7 * scale;
    ctx.fillText(label, labelX, y);

    if (dn.isHovered || dn.isSelected) {
      var metaFs = Math.max(8, CFG.NODE_META_SIZE * scale);
      ctx.fillStyle = dark ? "#a6abb4" : "#6b7280";
      ctx.font = metaFs + "px -apple-system, BlinkMacSystemFont, sans-serif";
      ctx.textBaseline = "top";
      ctx.fillText(dn.memoryCount + "M / " + dn.degree + " links", labelX, y + 8 * scale);
    }

    ctx.restore();
  };

  Renderer.prototype.hitTestNode = function(sx, sy) {
    var best = null, bestDist = Infinity;
    for (var i = this._drawnNodes.length - 1; i >= 0; i--) {
      var dn = this._drawnNodes[i];
      if (dn.isMuted) continue;
      var d = Math.sqrt((sx - dn.sx) ** 2 + (sy - dn.sy) ** 2);
      if (d < dn.sr + CFG.HOVER_RADIUS && d < bestDist) { best = dn; bestDist = d; }
    }
    return best;
  };

  Renderer.prototype.hitTestEdge = function(sx, sy) {
    for (var i = 0; i < this._drawnEdges.length; i++) {
      var de = this._drawnEdges[i];
      if (de.isMuted) continue;
      var dist = pointToSegmentDistance(sx, sy, de.sx, de.sy, de.tx, de.ty);
      if (dist < 8) return de;
    }
    return null;
  };

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

  /* ═══════════════════════════════════════════════════════════════
     Interaction — mouse / touch
     ═══════════════════════════════════════════════════════════════ */
  function Interaction(container, canvas, renderer, callbacks) {
    this.container = container;
    this.canvas = canvas;
    this.renderer = renderer;
    this.cb = callbacks || {};
    this._dragging = false;
    this._panning = false;
    this._dragNode = null;
    this._dragStart = { x: 0, y: 0 };
    this._panStart = { ox: 0, oy: 0, mx: 0, my: 0 };
    this._hoverId = null;
    this._hoverType = null;
    this._pinchDist = 0;
    this._pinchScale = 1;
    this._bind();
  }

  Interaction.prototype._bind = function() {
    var self = this;
    var el = this.canvas;
    el.addEventListener("mousedown", function(e) { self._onMouseDown(e); });
    el.addEventListener("mousemove", function(e) { self._onMouseMove(e); });
    window.addEventListener("mouseup", function(e) { self._onMouseUp(e); });
    el.addEventListener("mouseleave", function(e) { self._onMouseUp(e); });
    el.addEventListener("wheel", function(e) { self._onWheel(e); }, { passive: false });
    el.addEventListener("dblclick", function(e) { self._onDblClick(e); });
    el.addEventListener("touchstart", function(e) { self._onTouchStart(e); }, { passive: false });
    el.addEventListener("touchmove", function(e) { self._onTouchMove(e); }, { passive: false });
    el.addEventListener("touchend", function(e) { self._onTouchEnd(e); });
    el.addEventListener("contextmenu", function(e) { e.preventDefault(); });
  };

  Interaction.prototype._onMouseDown = function(e) {
    var pos = getPos(e, this.canvas);
    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit && e.button === 0) {
      this._dragging = true;
      this._dragNode = hit;
      this._dragStart = { x: pos.x, y: pos.y };
      e.preventDefault();
      return;
    }
    if (e.button === 0 || e.button === 2) {
      this._panning = true;
      this._panStart = {
        ox: this.renderer.viewport.ox, oy: this.renderer.viewport.oy,
        mx: pos.x, my: pos.y,
      };
      e.preventDefault();
    }
  };

  Interaction.prototype._onMouseMove = function(e) {
    var pos = getPos(e, this.canvas);
    var vr = this.renderer.viewport;

    if (this._dragging && this._dragNode) {
      var world = this.renderer.screenToWorld(pos.x, pos.y);
      var simNode = this.renderer._nodesMap && this.renderer._nodesMap[this._dragNode.id];
      if (simNode) {
        simNode.x = simNode._prevX = world.x;
        simNode.y = simNode._prevY = world.y;
        simNode.fixed = true;
      }
      return;
    }

    if (this._panning) {
      vr.ox = this._panStart.ox + (pos.x - this._panStart.mx) / vr.scale;
      vr.oy = this._panStart.oy + (pos.y - this._panStart.my) / vr.scale;
      return;
    }

    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit) {
      if (this._hoverId !== hit.id || this._hoverType !== "node") {
        this._hoverId = hit.id; this._hoverType = "node";
        if (this.cb.onNodeHover) this.cb.onNodeHover(hit.id);
      }
      this.canvas.style.cursor = "pointer";
      return;
    }

    var hitE = this.renderer.hitTestEdge(pos.x, pos.y);
    if (hitE) {
      this._hoverId = hitE.id; this._hoverType = "edge";
      this.canvas.style.cursor = "pointer";
      return;
    }

    if (this._hoverId !== null) {
      this._hoverId = null; this._hoverType = null;
      if (this.cb.onNodeHover) this.cb.onNodeHover(null);
    }
    this.canvas.style.cursor = this._panning ? "grabbing" : "grab";
  };

  Interaction.prototype._onMouseUp = function(e) {
    if (this._dragging && this._dragNode) {
      var pos = getPos(e, this.canvas);
      var dx = pos.x - this._dragStart.x, dy = pos.y - this._dragStart.y;
      if (Math.sqrt(dx * dx + dy * dy) < 3) {
        if (this.cb.onNodeClick) this.cb.onNodeClick(this._dragNode.id);
      }
      this._dragging = false; this._dragNode = null;
    }
    if (this._panning) {
      var pos2 = getPos(e, this.canvas);
      if (Math.sqrt((pos2.x - this._panStart.mx) ** 2 + (pos2.y - this._panStart.my) ** 2) < 3) {
        if (this.cb.onBackgroundClick) this.cb.onBackgroundClick();
      }
      this._panning = false;
    }
    this.canvas.style.cursor = "grab";
  };

  Interaction.prototype._onWheel = function(e) {
    e.preventDefault();
    var vr = this.renderer.viewport;
    var delta = e.deltaY > 0 ? -CFG.ZOOM_STEP * 60 : CFG.ZOOM_STEP * 60;
    var newScale = clamp(vr.scale + delta, CFG.ZOOM_MIN, CFG.ZOOM_MAX);
    var pos = getPos(e, this.canvas);
    var before = this.renderer.screenToWorld(pos.x, pos.y);
    vr.scale = newScale;
    var after = this.renderer.screenToWorld(pos.x, pos.y);
    vr.ox += before.x - after.x;
    vr.oy += before.y - after.y;
  };

  Interaction.prototype._onDblClick = function(e) {
    var pos = getPos(e, this.canvas);
    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit && this.cb.onNodeDblClick) this.cb.onNodeDblClick(hit.id);
  };

  Interaction.prototype._onTouchStart = function(e) {
    if (e.touches.length === 2) {
      var t0 = e.touches[0], t1 = e.touches[1];
      this._pinchDist = Math.sqrt((t1.clientX - t0.clientX) ** 2 + (t1.clientY - t0.clientY) ** 2);
      this._pinchScale = this.renderer.viewport.scale;
      return;
    }
    if (e.touches.length === 1) {
      this._onMouseDown({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY, button: 0 });
    }
    e.preventDefault();
  };

  Interaction.prototype._onTouchMove = function(e) {
    if (e.touches.length === 2 && this._pinchDist > 0) {
      var t0 = e.touches[0], t1 = e.touches[1];
      var d = Math.sqrt((t1.clientX - t0.clientX) ** 2 + (t1.clientY - t0.clientY) ** 2);
      this.renderer.viewport.scale = clamp(this._pinchScale * (d / this._pinchDist), CFG.ZOOM_MIN, CFG.ZOOM_MAX);
      return;
    }
    if (e.touches.length === 1) {
      this._onMouseMove({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY });
    }
    e.preventDefault();
  };

  Interaction.prototype._onTouchEnd = function(e) {
    if (e.touches.length < 2) this._pinchDist = 0;
    var t = e.changedTouches[0] || {};
    this._onMouseUp({ clientX: t.clientX || 0, clientY: t.clientY || 0 });
  };

  Interaction.prototype.getHoverId = function() { return this._hoverId; };
  Interaction.prototype.getHoverType = function() { return this._hoverType; };

  /* ═══════════════════════════════════════════════════════════════
     Animator — RAF loop with radial position tweening
     ═══════════════════════════════════════════════════════════════ */
  function Animator(renderer, interaction) {
    this.renderer = renderer;
    this.interaction = interaction;
    this._running = false;
    this._rafId = null;
    this._nodes = [];
    this._edges = [];
    this._nodeMap = {};
    this._mem2node = {};
    this._radial = new RadialLayout();
    this._animProgress = 1; // 0→1 for position transitions
    this._needsRender = true;
  }

  Animator.prototype.fitViewport = function(options) {
    options = options || {};
    if (!this._nodes.length || !this.renderer.width || !this.renderer.height) return;

    var centerId = options.centerId;
    var centerTarget = centerId != null ? this._radial.getTarget(centerId) : null;
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

    for (var i = 0; i < this._nodes.length; i++) {
      var nd = this._nodes[i];
      var target = this._radial.getTarget(nd.id);
      var isCenter = this._radial.centerId != null && nd.id === this._radial.centerId;
      var pad = this.renderer.nodeWorldRadius(nd, isCenter) + 28;
      minX = Math.min(minX, target.tx - pad);
      maxX = Math.max(maxX, target.tx + pad);
      minY = Math.min(minY, target.ty - pad);
      maxY = Math.max(maxY, target.ty + pad);
    }

    if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minY) || !Number.isFinite(maxY)) return;

    var boundsW = Math.max(1, maxX - minX);
    var boundsH = Math.max(1, maxY - minY);
    var padding = this.renderer.width < 520 ? 0.9 : 0.82;
    var fitScale = Math.min(
      (this.renderer.width * padding) / boundsW,
      (this.renderer.height * padding) / boundsH
    );
    var scale = clamp(fitScale, 0.35, 1.65);
    var cx = (minX + maxX) / 2;
    var cy = (minY + maxY) / 2;

    if (centerTarget && this.renderer.width >= 520) {
      cx = lerp(cx, centerTarget.tx, 0.28);
      cy = lerp(cy, centerTarget.ty, 0.2);
    }

    this.renderer.viewport.scale = scale;
    this.renderer.viewport.ox = -cx;
    this.renderer.viewport.oy = -cy;
    this._needsRender = true;
  };

  Animator.prototype.start = function() {
    if (this._running) return;
    this._running = true;
    this._tick();
  };

  Animator.prototype.stop = function() {
    this._running = false;
    if (this._rafId !== null) { cancelAnimationFrame(this._rafId); this._rafId = null; }
  };

  Animator.prototype.setData = function(nodes, edges) {
    var self = this;
    this._nodes = nodes;
    this._edges = edges;
    this._nodeMap = {};
    nodes.forEach(function(n) { self._nodeMap[n.id] = n; });
    this.renderer._nodesMap = this._nodeMap;
  };

  Animator.prototype.layoutRadial = function(centerId) {
    var self = this;
    /* Save previous positions for animation */
    this._nodes.forEach(function(n) {
      n._prevX = n.x;
      n._prevY = n.y;
    });
    this._radial.compute(this._nodes, this._edges, centerId);
    this.fitViewport({ centerId: centerId });
    this._animProgress = 0;
    this._needsRender = true;
    this.start();
  };

  Animator.prototype.recenter = function(centerId) {
    this.layoutRadial(centerId);
  };

  Animator.prototype._tick = function() {
    if (!this._running) return;
    var self = this;
    this._rafId = requestAnimationFrame(function() { self._tick(); });

    /* Animate positions toward radial targets */
    var dirty = true;
    if (this._animProgress < 1) {
      this._animProgress = Math.min(1, this._animProgress + CFG.ANIM_SPEED);
      var ap = easeInOutCubic(this._animProgress);

      for (var i = 0; i < this._nodes.length; i++) {
        var nd = this._nodes[i];
        if (nd.fixed) continue;
        var target = this._radial.getTarget(nd.id);
        if (nd._prevX == null) { nd._prevX = nd.x; nd._prevY = nd.y; }
        nd.x = lerp(nd._prevX, target.tx, ap);
        nd.y = lerp(nd._prevY, target.ty, ap);
      }
      if (this._animProgress >= 1) {
        /* Lock to exact targets */
        for (var j = 0; j < this._nodes.length; j++) {
          var nd2 = this._nodes[j];
          if (nd2.fixed) continue;
          var tgt = this._radial.getTarget(nd2.id);
          nd2.x = tgt.tx; nd2.y = tgt.ty;
          nd2._prevX = null; nd2._prevY = null;
        }
      }
    } else {
      var now = Date.now() / 1000;
      for (var k = 0; k < this._nodes.length; k++) {
        var floatNode = this._nodes[k];
        if (floatNode.fixed) continue;
        var home = this._radial.getTarget(floatNode.id);
        var ring = this._radial.getRing(floatNode.id);
        var amp = ring === 0 ? 1.2 : 2.4 + ring * 0.5;
        var phase = (floatNode.id % 17) * 0.37;
        floatNode.x = lerp(floatNode.x, home.tx + Math.sin(now * 0.65 + phase) * amp, CFG.IDLE_DAMPING);
        floatNode.y = lerp(floatNode.y, home.ty + Math.cos(now * 0.55 + phase) * amp, CFG.IDLE_DAMPING);
      }
    }

    if (dirty || this._needsRender) {
      this.renderer.clear();
      var sel = this.renderer._selection;
      var hoverId = this.interaction.getHoverId();
      this.renderer.render(this._nodes, this._edges, this._nodeMap, sel, hoverId, this._radial, this._animProgress);
      this._needsRender = false;
    }
  };

  Animator.prototype.wake = function() {
    if (!this._running) this.start();
    this._needsRender = true;
  };

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  /* ═══════════════════════════════════════════════════════════════
     Graph2D — Public API
     ═══════════════════════════════════════════════════════════════ */
  function Graph2D() {
    this.container = null;
    this.canvas = null;
    this.renderer = null;
    this.interaction = null;
    this.animator = null;
    this.selection = null;
    this.callbacks = {};
    this._initialized = false;
  }

  Graph2D.prototype.init = function(containerEl, callbacks) {
    if (this._initialized) return;
    var self = this;
    this.container = containerEl;
    this.callbacks = callbacks || {};

    this.canvas = document.createElement("canvas");
    this.canvas.style.width = "100%";
    this.canvas.style.height = "100%";
    this.canvas.style.display = "block";
    this.canvas.style.cursor = "grab";
    this.container.innerHTML = "";
    this.container.appendChild(this.canvas);

    this.renderer = new Renderer(this.canvas);
    this.renderer._selection = this.selection;

    this.interaction = new Interaction(this.container, this.canvas, this.renderer, {
      onNodeClick: function(nodeId) {
        self.selectNode(nodeId);
        if (self.callbacks.onNodeClick) self.callbacks.onNodeClick(nodeId);
      },
      onNodeDblClick: function(nodeId) {
        if (self.callbacks.onNodeDblClick) self.callbacks.onNodeDblClick(nodeId);
      },
      onNodeHover: function(nodeId) {
        if (self.callbacks.onNodeHover) self.callbacks.onNodeHover(nodeId);
      },
      onBackgroundClick: function() {
        self.clearSelection();
        if (self.callbacks.onBackgroundClick) self.callbacks.onBackgroundClick();
      },
    });

    this.animator = new Animator(this.renderer, this.interaction);
    this.renderer.resize();
    this.animator.start();

    /* Resize observer */
    if (typeof window.ResizeObserver === "function") {
      var ro = new ResizeObserver(function() {
        self.resize();
      });
      ro.observe(this.container);
    }
    window.addEventListener("resize", function() {
      self.resize();
    }, { passive: true });

    /* Theme observer */
    if (typeof window.MutationObserver === "function") {
      var mo = new MutationObserver(function() { self.animator.wake(); });
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }

    this._initialized = true;
  };

  Graph2D.prototype.loadData = function(payload) {
    var snapshot = payload.snapshot || {};
    var rawNodes = snapshot.nodes || [];
    var rawEdges = snapshot.edges || [];

    /* Convert to internal format */
    var seenIds = {};
    var nodes = [];
    rawNodes.forEach(function(node) {
      var id = Number(node.id);
      if (seenIds[id]) return;
      seenIds[id] = true;
      nodes.push({
        id: id, type: node.type || "other",
        label: node.label || node.canonical_value || "Node",
        canonicalValue: node.canonical_value || "",
        x: 0, y: 0, _prevX: null, _prevY: null, fixed: false,
        weight: Number(node.weight || 0),
        memory_count: Number(node.memory_count || 0),
        degree: Number(node.degree || 0),
        entry_count: Number(node.entry_count || 0),
        color: TYPE_COLORS[node.type] || TYPE_COLORS.other,
      });
    });

    var edges = [];
    var edgeSeen = {};
    rawEdges.forEach(function(edge) {
      var eid = edge.id != null ? Number(edge.id) : (edge.source + ":" + edge.target + ":" + edge.memory_id);
      if (edgeSeen[eid]) return;
      edgeSeen[eid] = true;
      edges.push({
        id: eid, source: Number(edge.source), target: Number(edge.target),
        relation_type: edge.relation_type || "related",
        memory_id: Number(edge.memory_id || 0),
        weight: Number(edge.weight || 1),
        confidence: Number(edge.confidence || 0.8),
        __color: relationColor(edge.relation_type),
      });
    });

    /* Build memory→node index */
    var mem2node = {};
    edges.forEach(function(edge) {
      if (!mem2node[edge.memory_id]) mem2node[edge.memory_id] = new Set();
      mem2node[edge.memory_id].add(edge.source);
      mem2node[edge.memory_id].add(edge.target);
    });

    this.animator.setData(nodes, edges);
    this._mem2node = mem2node;
    this.animator._mem2node = mem2node;
    this._nodes = nodes;
    this._edges = edges;

    /* Determine center: if there's a selection, use it; else pick highest weight node */
    var centerId = null;
    if (this.selection && this.selection.type === "node") {
      centerId = this.selection.id;
    } else if (this.selection && this.selection.type === "memory" && mem2node[this.selection.id]) {
      var mids = Array.from(mem2node[this.selection.id]);
      if (mids.length > 0) centerId = mids[0];
    }

    /* Apply radial layout with animation */
    this.animator.layoutRadial(centerId);

    this.animator.wake();
  };

  Graph2D.prototype.selectNode = function(nodeId) {
    this.selection = { type: "node", id: nodeId };
    this.renderer._selection = this.selection;
    /* Recenter on selected node with smooth animation */
    this.animator.recenter(nodeId);
  };

  Graph2D.prototype.selectMemory = function(memoryId) {
    this.selection = { type: "memory", id: memoryId };
    this.renderer._selection = this.selection;
    if (this._mem2node && this._mem2node[memoryId]) {
      var nodes = Array.from(this._mem2node[memoryId]);
      if (nodes.length) this.animator.recenter(nodes[0]);
    }
    this.animator.wake();
  };

  Graph2D.prototype.clearSelection = function() {
    this.selection = null;
    this.renderer._selection = null;
    /* Re-layout with highest-weight node as center */
    this.animator.layoutRadial(null);
  };

  Graph2D.prototype.resize = function() {
    if (this.renderer) this.renderer.resize();
    if (this.animator) {
      var centerId = this.selection && this.selection.type === "node" ? this.selection.id : null;
      this.animator.fitViewport({ centerId: centerId });
      this.animator.wake();
    }
  };

  Graph2D.prototype.destroy = function() {
    if (this.animator) this.animator.stop();
    if (this.canvas && this.canvas.parentElement) {
      this.canvas.parentElement.removeChild(this.canvas);
    }
    this._initialized = false;
  };

  function relationColor(type) {
    var palette = ["#8792a2", "#6f7f96", "#8a7b65", "#74806c", "#8a7181", "#6f8388"];
    var h = String(type || "related").split("").reduce(function(a, c) { return a * 31 + c.charCodeAt(0); }, 7);
    return palette[Math.abs(h) % palette.length];
  }

  /* ═══════════════════════════════════════════════════════════════
     Export
     ═══════════════════════════════════════════════════════════════ */
  window.Graph2D = new Graph2D();
})();
