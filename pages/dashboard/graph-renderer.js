/* ================================================================
   graph-renderer.js — Canvas 2D 渲染器
   点阵背景、社区、边/粒子、节点/标签、命中测试。
   依赖 graph-shared.js（GraphShared）。
   ================================================================ */
(function(global) {
  "use strict";

  var CFG = global.GraphShared.CFG;
  var TYPE_COLORS = global.GraphShared.TYPE_COLORS;
  var isDark = global.GraphShared.isDark;
  var clamp = global.GraphShared.clamp;
  var lerp = global.GraphShared.lerp;
  var performanceTier = global.GraphShared.performanceTier;
  var themeColor = global.GraphShared.themeColor;
  var hexToRgba = global.GraphShared.hexToRgba;
  var easeInOutCubic = global.GraphShared.easeInOutCubic;
  var pointToSegmentDistance = global.GraphShared.pointToSegmentDistance;

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
    this._labelBoxes = [];
    this._particleOffsets = {};
    this._selection = null;
    this.performanceTier = 0;
    this._adjacency = {};
    this._nodeEdges = {};
    this._memoryEdges = {};
    this._structuralEdges = [];
    this._communityBundles = [];
    this._nodeHitGrid = {};
    this._labelGrid = {};
    this._labelWidthCache = {};
    this._communityCacheKey = null;
    this._communityCache = null;
    this._bgCanvas = null;
    this._bgCacheKey = null;
  }

  Renderer.prototype.configureData = function(nodes, edges) {
    var self = this;
    this.performanceTier = performanceTier(nodes.length, edges.length);
    this._adjacency = {};
    this._nodeEdges = {};
    this._memoryEdges = {};
    this._structuralEdges = [];
    this._communityBundles = [];
    this._labelWidthCache = {};
    this._communityCacheKey = null;
    this._communityCache = null;
    nodes.forEach(function(node) {
      self._adjacency[node.id] = [];
      self._nodeEdges[node.id] = [];
    });
    edges.forEach(function(edge) {
      if (self._adjacency[edge.source]) self._adjacency[edge.source].push(edge.target);
      if (self._adjacency[edge.target]) self._adjacency[edge.target].push(edge.source);
      if (self._nodeEdges[edge.source]) self._nodeEdges[edge.source].push(edge);
      if (self._nodeEdges[edge.target]) self._nodeEdges[edge.target].push(edge);
      if (!self._memoryEdges[edge.memory_id]) self._memoryEdges[edge.memory_id] = [];
      self._memoryEdges[edge.memory_id].push(edge);
    });
    return this.performanceTier;
  };

  Renderer.prototype.prepareGraph = function(nodes, edges, layout) {
    if (this.performanceTier === 0 || !layout) {
      this._structuralEdges = edges;
      this._communityBundles = [];
      return;
    }

    var ranked = edges.slice().sort(function(a, b) {
      return Number(b.weight || 0) - Number(a.weight || 0) ||
        Number(b.confidence || 0) - Number(a.confidence || 0) ||
        String(a.id).localeCompare(String(b.id));
    });
    var nodeBudget = {};
    var selected = [];
    var bundles = {};
    ranked.forEach(function(edge) {
      var sourceCommunity = layout.communities[edge.source];
      var targetCommunity = layout.communities[edge.target];
      if (sourceCommunity !== targetCommunity) {
        var lo = Math.min(sourceCommunity, targetCommunity);
        var hi = Math.max(sourceCommunity, targetCommunity);
        var bundleKey = lo + ":" + hi;
        if (!bundles[bundleKey]) {
          bundles[bundleKey] = {
            sourceCommunity: lo,
            targetCommunity: hi,
            count: 0,
            weight: 0,
            color: edge.__color || TYPE_COLORS.other,
          };
        }
        bundles[bundleKey].count += 1;
        bundles[bundleKey].weight += Number(edge.weight || 1);
        return;
      }

      var sourceBudget = nodeBudget[edge.source] || 0;
      var targetBudget = nodeBudget[edge.target] || 0;
      if (sourceBudget >= 1 && targetBudget >= 1) return;
      selected.push(edge);
      if (sourceBudget < 1) nodeBudget[edge.source] = sourceBudget + 1;
      if (targetBudget < 1) nodeBudget[edge.target] = targetBudget + 1;
    });

    var communityStats = {};
    nodes.forEach(function(node) {
      var community = layout.communities[node.id] == null ? 0 : layout.communities[node.id];
      var target = layout.getTarget(node.id);
      if (!communityStats[community]) communityStats[community] = { x: 0, y: 0, count: 0 };
      communityStats[community].x += target.tx;
      communityStats[community].y += target.ty;
      communityStats[community].count += 1;
    });
    Object.keys(communityStats).forEach(function(key) {
      var stats = communityStats[key];
      stats.x /= Math.max(stats.count, 1);
      stats.y /= Math.max(stats.count, 1);
    });

    this._structuralEdges = selected;
    this._communityBundles = Object.values(bundles).map(function(bundle) {
      bundle.source = communityStats[bundle.sourceCommunity] || { x: 0, y: 0 };
      bundle.target = communityStats[bundle.targetCommunity] || { x: 0, y: 0 };
      return bundle;
    }).sort(function(a, b) { return b.count - a.count; });
  };

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

  Renderer.prototype._rebuildBackgroundCache = function(dark, step) {
    var w = Math.ceil(this.width + step);
    var h = Math.ceil(this.height + step);
    if (!this._bgCanvas) this._bgCanvas = document.createElement("canvas");
    this._bgCanvas.width = w;
    this._bgCanvas.height = h;
    var bctx = this._bgCanvas.getContext("2d");

    bctx.fillStyle = themeColor("--graph-surface", dark ? "#11130f" : "#f8fbfa");
    bctx.fillRect(0, 0, w, h);
    bctx.fillStyle = dark ? "rgba(183,243,74,0.13)" : "rgba(40,92,94,0.12)";
    for (var x = 0; x <= w; x += step) {
      for (var y = 0; y <= h; y += step) {
        bctx.beginPath();
        bctx.arc(x, y, 0.75, 0, Math.PI * 2);
        bctx.fill();
      }
    }
    var majorStep = step * 5;
    bctx.strokeStyle = dark ? "rgba(183,243,74,0.055)" : "rgba(40,92,94,0.05)";
    bctx.lineWidth = 1;
    for (var mx = 0; mx <= w; mx += majorStep) {
      bctx.beginPath();
      bctx.moveTo(mx, 0);
      bctx.lineTo(mx, h);
      bctx.stroke();
    }
    for (var my = 0; my <= h; my += majorStep) {
      bctx.beginPath();
      bctx.moveTo(0, my);
      bctx.lineTo(w, my);
      bctx.stroke();
    }
  };

  Renderer.prototype.drawBackground = function(dark, animateDecorations) {
    var ctx = this.ctx;
    var step = clamp(30 * this.viewport.scale, 22, 42);
    var ox = ((this.viewport.ox * this.viewport.scale) % step + step) % step;
    var oy = ((this.viewport.oy * this.viewport.scale) % step + step) % step;

    /* 静态点阵/网格缓存到离屏画布，平移只改 blit 偏移，避免每帧重画数百个点。 */
    var cacheKey = (dark ? "d" : "l") + ":" + this.width + "x" + this.height + ":s" + step.toFixed(2);
    if (this._bgCacheKey !== cacheKey) {
      this._bgCacheKey = cacheKey;
      this._rebuildBackgroundCache(dark, step);
    }
    if (this._bgCanvas) {
      ctx.drawImage(this._bgCanvas, ox, oy);
    } else {
      ctx.fillStyle = themeColor("--graph-surface", dark ? "#11130f" : "#f8fbfa");
      ctx.fillRect(0, 0, this.width, this.height);
    }

    if (animateDecorations) {
      ctx.save();
      var scanY = (Date.now() * 0.018) % Math.max(this.height, 1);
      ctx.fillStyle = dark ? "rgba(183,243,74,0.13)" : "rgba(42,167,157,0.1)";
      ctx.fillRect(0, scanY, this.width, 1);
      ctx.restore();
    }
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

  Renderer.prototype.render = function(nodes, edges, nodeMap, selection, hoverId, layout, animProgress) {
    var ctx = this.ctx;
    var scale = this.viewport.scale;
    var dark = isDark();
    var selNodeId = (selection && selection.type === "node") ? selection.id : null;
    var selMemId = (selection && selection.type === "memory") ? selection.id : null;

    /* Build highlight sets */
    var highlightNodes = new Set();
    var highlightEdges = new Set();
    var focusEdges = null;

    if (selNodeId !== null) {
      highlightNodes.add(selNodeId);
      (this._adjacency[selNodeId] || []).forEach(function(nid) { highlightNodes.add(nid); });
      focusEdges = this._nodeEdges[selNodeId] || [];
    }
    if (selMemId !== null) {
      focusEdges = this._memoryEdges[selMemId] || [];
      focusEdges.forEach(function(edge) {
        highlightNodes.add(edge.source);
        highlightNodes.add(edge.target);
        highlightEdges.add(edge.id);
      });
    }
    var hasFocus = highlightNodes.size > 0 || highlightEdges.size > 0;
    var visibleEdges = focusEdges || (this.performanceTier > 0 ? this._structuralEdges : edges);

    var centerId = layout ? layout.centerId : null;

    this.drawBackground(dark, this.performanceTier === 0);

    /* Compute animated positions */
    var ap = animProgress == null ? 1 : animProgress;

    this._drawCommunities(nodes, layout, ap, dark);
    if (this.performanceTier > 0) this._drawCommunityBundles(dark, hasFocus);

    /* Draw edges first (under nodes) */
    this._drawnEdges = [];
    this._labelBoxes = [];
    this._labelGrid = {};
    /* 边 LOD：tier 0 且缩小时抽样绘制（高亮/聚焦边始终完整绘制）。 */
    var edgeStride = 1;
    if (this.performanceTier === 0 && scale < 0.45 && visibleEdges.length > 900) {
      edgeStride = Math.max(2, Math.round(visibleEdges.length / 900));
    }
    ctx.save();
    for (var e = 0; e < visibleEdges.length; e++) {
      var edge = visibleEdges[e];
      if (edgeStride > 1 && e % edgeStride !== 0 && !highlightEdges.has(edge.id)) continue;
      var src = nodeMap[edge.source];
      var tgt = nodeMap[edge.target];
      if (!src || !tgt) continue;

      var sAnim = { x: lerp(src._prevX || src.x, src.x, ap), y: lerp(src._prevY || src.y, src.y, ap) };
      var tAnim = { x: lerp(tgt._prevX || tgt.x, tgt.x, ap), y: lerp(tgt._prevY || tgt.y, tgt.y, ap) };

      var ssp = this.worldToScreen(sAnim.x, sAnim.y);
      var tsp = this.worldToScreen(tAnim.x, tAnim.y);
      var edgeMargin = 48;
      if (Math.max(ssp.x, tsp.x) < -edgeMargin || Math.min(ssp.x, tsp.x) > this.width + edgeMargin ||
          Math.max(ssp.y, tsp.y) < -edgeMargin || Math.min(ssp.y, tsp.y) > this.height + edgeMargin) {
        continue;
      }
      var lineDx = tsp.x - ssp.x;
      var lineDy = tsp.y - ssp.y;
      var lineLength = Math.sqrt(lineDx * lineDx + lineDy * lineDy) || 1;
      var sameCommunity = src.community === tgt.community;
      var bend = (edge._bendSign || 1) * Math.min(24, lineLength * 0.065) *
        (sameCommunity ? 1 : 0.38);
      var controlX = (ssp.x + tsp.x) / 2 - lineDy / lineLength * bend;
      var controlY = (ssp.y + tsp.y) / 2 + lineDx / lineLength * bend;

      var isActive = !hasFocus || (highlightNodes.has(edge.source) && highlightNodes.has(edge.target));
      var isMemHl = highlightEdges.has(edge.id);
      var isMuted = hasFocus && !isActive && !isMemHl;

      var de = {
        id: edge.id, sx: ssp.x, sy: ssp.y, tx: tsp.x, ty: tsp.y,
        cx: controlX, cy: controlY,
        sourceId: edge.source, targetId: edge.target,
        relationType: edge.relation_type || "related",
        memoryId: edge.memory_id, weight: edge.weight || 1,
        confidence: edge.confidence || 0.8,
        isActive: isActive, isHighlighted: isMemHl,
        isMuted: isMuted, hasFocus: hasFocus,
        isCrossCommunity: !sameCommunity,
        isHovered: edge.id === hoverId,
        color: edge.__color || TYPE_COLORS.other,
      };
      this._drawnEdges.push(de);

      if (de.isMuted) continue;
      this._drawEdge(ctx, de, dark);
    }
    ctx.restore();

    /* Particles */
    if (this.performanceTier === 0) {
      ctx.save();
      var now = Date.now() / 1000;
      var particleStride = Math.max(1, Math.ceil(this._drawnEdges.length / 320));
      for (var p = 0; p < this._drawnEdges.length; p++) {
        var de2 = this._drawnEdges[p];
        if (de2.isMuted) continue;
        if (!de2.hasFocus && particleStride > 1 && p % particleStride !== 0) continue;
        this._drawParticles(ctx, de2, now, dark);
      }
      ctx.restore();
    }

    /* Draw nodes */
    this._drawnNodes = [];
    this._nodeHitGrid = {};
    var denseMode = this.performanceTier > 0 && scale < 0.52;
    var denseBuckets = {};
    var detailedNodes = [];
    ctx.save();
    for (var i = 0; i < nodes.length; i++) {
      var nd = nodes[i];
      /* Animated position */
      var px = lerp(nd._prevX || nd.x, nd.x, ap);
      var py = lerp(nd._prevY || nd.y, nd.y, ap);
      var sp = this.worldToScreen(px, py);
      var nodeMargin = 36;
      if (sp.x < -nodeMargin || sp.x > this.width + nodeMargin ||
          sp.y < -nodeMargin || sp.y > this.height + nodeMargin) {
        continue;
      }

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
        labelScore: nd.labelScore || 0,
        color: TYPE_COLORS[nd.type] || TYPE_COLORS.other, fixed: nd.fixed,
      };
      this._drawnNodes.push(drawInfo);

      var needsDetail = drawInfo.isSelected || drawInfo.isHovered || drawInfo.isCenter;
      if (denseMode && !needsDetail) {
        var bucketKey = drawInfo.isMuted ? "muted" : drawInfo.type;
        if (!denseBuckets[bucketKey]) denseBuckets[bucketKey] = [];
        denseBuckets[bucketKey].push(drawInfo);
      } else {
        detailedNodes.push(drawInfo);
      }
    }
    this._drawDenseNodeBuckets(ctx, denseBuckets, dark);
    for (var d = 0; d < detailedNodes.length; d++) {
      var detailNode = detailedNodes[d];
      if (detailNode.isMuted && !detailNode.isHovered) {
        ctx.globalAlpha = 0.22;
        ctx.beginPath();
        ctx.arc(detailNode.sx, detailNode.sy, Math.max(2, detailNode.sr * 0.62), 0, Math.PI * 2);
        ctx.fillStyle = dark ? "#5c6370" : "#c7ccd4";
        ctx.fill();
        ctx.globalAlpha = 1;
      } else {
        this._drawNode(ctx, detailNode, scale, dark);
      }
    }
    ctx.restore();
    this._rebuildNodeHitGrid();
  };

  Renderer.prototype._drawCommunities = function(nodes, layout, animProgress, dark) {
    if (!layout || !nodes.length) return;
    var viewportKey = this.viewport.scale.toFixed(3) + "|" +
      this.viewport.ox.toFixed(2) + "|" + this.viewport.oy.toFixed(2);
    var idle = layout._done && animProgress >= 1;
    if (!layout._done) this._communityCacheKey = null;
    if (idle && this._communityCacheKey === viewportKey && this._communityCache) {
      this._drawCommunityCache(dark);
      return;
    }

    var groups = {};
    for (var i = 0; i < nodes.length; i++) {
      var nd = nodes[i];
      var community = nd.community == null ? 0 : nd.community;
      if (!groups[community]) groups[community] = [];
      var px = lerp(nd._prevX || nd.x, nd.x, animProgress);
      var py = lerp(nd._prevY || nd.y, nd.y, animProgress);
      groups[community].push(this.worldToScreen(px, py));
    }
    var keys = Object.keys(groups);
    if (keys.length < 2) return;

    var palette = ["#78a94b", "#2a9e96", "#df6d62", "#c58c2a", "#74868a", "#6684b8"];
    var ctx = this.ctx;
    var cacheEntries = idle ? [] : null;
    ctx.save();
    ctx.setLineDash([5, 8]);
    keys.forEach(function(key, index) {
      var points = groups[key];
      if (points.length < 2) return;
      var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      points.forEach(function(point) {
        minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
        minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y);
      });
      var cx = (minX + maxX) / 2;
      var cy = (minY + maxY) / 2;
      var rx = Math.max(36, (maxX - minX) / 2 + 28);
      var ry = Math.max(28, (maxY - minY) / 2 + 24);
      var color = palette[index % palette.length];
      ctx.beginPath();
      ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(color, dark ? 0.04 : 0.045);
      ctx.strokeStyle = hexToRgba(color, dark ? 0.24 : 0.25);
      ctx.lineWidth = 1;
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = hexToRgba(color, dark ? 0.72 : 0.9);
      ctx.font = "600 9px 'SFMono-Regular', Consolas, monospace";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText("C/" + String(index + 1).padStart(2, "0") + " · " + points.length, cx - rx + 12, cy - ry + 10);
      ctx.setLineDash([5, 8]);
      if (cacheEntries) {
        cacheEntries.push({
          cx: cx, cy: cy, rx: rx, ry: ry, color: color,
          count: points.length, index: index + 1, dark: dark,
        });
      }
    });
    ctx.restore();
    if (cacheEntries) {
      this._communityCacheKey = viewportKey;
      this._communityCache = cacheEntries;
    }
  };

  /* 空闲时按缓存绘制社区椭圆，避免每帧重算全节点包围盒。 */
  Renderer.prototype._drawCommunityCache = function(dark) {
    var ctx = this.ctx;
    ctx.save();
    ctx.setLineDash([5, 8]);
    for (var i = 0; i < this._communityCache.length; i++) {
      var e = this._communityCache[i];
      ctx.beginPath();
      ctx.ellipse(e.cx, e.cy, e.rx, e.ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(e.color, dark ? 0.04 : 0.045);
      ctx.strokeStyle = hexToRgba(e.color, dark ? 0.24 : 0.25);
      ctx.lineWidth = 1;
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = hexToRgba(e.color, dark ? 0.72 : 0.9);
      ctx.font = "600 9px 'SFMono-Regular', Consolas, monospace";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText("C/" + String(e.index).padStart(2, "0") + " · " + e.count, e.cx - e.rx + 12, e.cy - e.ry + 10);
      ctx.setLineDash([5, 8]);
    }
    ctx.restore();
  };

  Renderer.prototype._drawCommunityBundles = function(dark, muted) {
    if (!this._communityBundles.length) return;
    var ctx = this.ctx;
    ctx.save();
    ctx.lineCap = "round";
    for (var i = 0; i < this._communityBundles.length; i++) {
      var bundle = this._communityBundles[i];
      var source = this.worldToScreen(bundle.source.x, bundle.source.y);
      var target = this.worldToScreen(bundle.target.x, bundle.target.y);
      var dx = target.x - source.x;
      var dy = target.y - source.y;
      var distance = Math.sqrt(dx * dx + dy * dy) || 1;
      var curve = Math.min(70, distance * 0.14) * (i % 2 ? 1 : -1);
      var cx = (source.x + target.x) / 2 - dy / distance * curve;
      var cy = (source.y + target.y) / 2 + dx / distance * curve;
      var strength = Math.log2(bundle.count + 1);
      var primary = i < Math.min(24, this._communityBundles.length);

      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.quadraticCurveTo(cx, cy, target.x, target.y);
      ctx.strokeStyle = hexToRgba(
        bundle.color,
        muted ? 0.035 : primary ? (dark ? 0.2 : 0.16) : (dark ? 0.065 : 0.05)
      );
      ctx.lineWidth = primary ? clamp(0.55 + strength * 0.48, 1, 5) : 0.75;
      ctx.stroke();

      if (!muted && primary && bundle.count >= 8 && this.viewport.scale > 0.1) {
        ctx.beginPath();
        ctx.arc(cx, cy, clamp(1.2 + strength * 0.22, 1.5, 3.5), 0, Math.PI * 2);
        ctx.fillStyle = hexToRgba(bundle.color, dark ? 0.7 : 0.62);
        ctx.fill();
      }
    }
    ctx.restore();
  };

  Renderer.prototype._drawDenseNodeBuckets = function(ctx, buckets, dark) {
    Object.keys(buckets).forEach(function(key) {
      var items = buckets[key];
      if (!items.length) return;
      ctx.beginPath();
      for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var radius = key === "muted" ? Math.max(1.1, item.sr * 0.58) : Math.max(1.15, item.sr);
        ctx.moveTo(item.sx + radius, item.sy);
        ctx.arc(item.sx, item.sy, radius, 0, Math.PI * 2);
      }
      ctx.fillStyle = key === "muted"
        ? (dark ? "rgba(92,99,112,0.24)" : "rgba(176,188,187,0.28)")
        : hexToRgba(TYPE_COLORS[key] || TYPE_COLORS.other, dark ? 0.9 : 0.86);
      ctx.fill();
    });
  };

  Renderer.prototype._rebuildNodeHitGrid = function() {
    var cellSize = 26;
    var grid = {};
    for (var i = 0; i < this._drawnNodes.length; i++) {
      var node = this._drawnNodes[i];
      var key = Math.floor(node.sx / cellSize) + ":" + Math.floor(node.sy / cellSize);
      if (!grid[key]) grid[key] = [];
      grid[key].push(node);
    }
    this._nodeHitGrid = grid;
  };

  /* Draw a single edge as a straight link */
  Renderer.prototype._drawEdge = function(ctx, de, dark) {
    var opacity = de.isHighlighted ? CFG.EDGE_OPACITY_HIGHLIGHT
      : de.hasFocus && de.isActive ? CFG.EDGE_OPACITY_ACTIVE : CFG.EDGE_OPACITY_DEFAULT;
    var width = de.isHighlighted ? CFG.EDGE_WIDTH_HIGHLIGHT
      : de.hasFocus && de.isActive ? CFG.EDGE_WIDTH_ACTIVE : CFG.EDGE_WIDTH_DEFAULT;
    var strength = clamp(Math.sqrt(Number(de.weight || 1)) / 3.6, 0, 1);

    if (de.isMuted) opacity *= 0.35;
    if (de.isCrossCommunity && !de.hasFocus) opacity *= 0.32;
    if (!de.isMuted) {
      width += strength * (de.hasFocus ? 0.35 : 0.8);
      opacity = clamp(opacity + strength * (de.hasFocus ? 0.04 : 0.1), 0, 0.84);
    }

    ctx.beginPath();
    ctx.moveTo(de.sx, de.sy);
    ctx.quadraticCurveTo(de.cx, de.cy, de.tx, de.ty);
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
      var oneMinusT = 1 - t;
      var px = oneMinusT * oneMinusT * de.sx + 2 * oneMinusT * t * de.cx + t * t * de.tx;
      var py = oneMinusT * oneMinusT * de.sy + 2 * oneMinusT * t * de.cy + t * t * de.ty;
      ctx.beginPath();
      ctx.arc(px, py, CFG.PARTICLE_SIZE * (de.isHighlighted ? 1.35 : 1), 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(de.color, de.isHighlighted ? 0.82 : 0.46);
      ctx.fill();
    }
  };

  /* Draw a single circular node */
  Renderer.prototype._drawNode = function(ctx, dn, scale, dark) {
    var x = dn.sx, y = dn.sy, r = Math.max(1.4, dn.sr);

    ctx.save();
    ctx.globalAlpha = dn.isMuted ? 0.26 : 1;

    var pulse = this.performanceTier === 0
      ? 0.5 + Math.sin(Date.now() * 0.0024 + Number(dn.id || 0) * 0.73) * 0.5
      : 0.5;
    var isProminent = scale > 0.34 && (dn.degree >= 3 || dn.memoryCount >= 3 || dn.isCenter);
    var haloBase = dn.isSelected ? 8 : dn.isHovered ? 6 : dn.isCenter ? 5 : isProminent ? 1.5 + pulse * 1.8 : 0;
    var halo = haloBase * scale;
    if (halo > 0 && !dn.isMuted) {
      ctx.beginPath();
      ctx.arc(x, y, r + halo, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(dn.color, dn.isSelected ? 0.18 : 0.055 + pulse * 0.035);
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

    var prominent = dn.degree >= 5 || dn.memoryCount >= 4 || dn.labelScore >= 15;
    var labelVisible = dn.isHovered || dn.isSelected || dn.isCenter ||
      (!dn.hasFocus && scale > 0.64 && prominent) ||
      (!dn.hasFocus && scale > 1.18 && dn.degree >= 3);
    if (!labelVisible || dn.isMuted) {
      ctx.restore();
      return;
    }

    var fontSize = Math.max(10, CFG.NODE_FONT_SIZE * scale);
    ctx.fillStyle = dark ? "#e9ecef" : "#2f343a";
    ctx.font = (dn.isSelected || dn.isCenter ? "650 " : "520 ") + fontSize + "px Arial, sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    var maxChars = dn.isCenter ? 28 : 24;
    var label = dn.label.length > maxChars ? dn.label.substring(0, maxChars - 1) + "…" : dn.label;
    var labelX = x + r + 7 * scale;
    /* measureText 缓存：按（标签, 字号桶）复用宽度，避免每帧重复测量。 */
    var fontBucket = Math.round(fontSize);
    var widthKey = fontBucket + "|" + label;
    var labelWidth = this._labelWidthCache[widthKey];
    if (labelWidth == null) {
      labelWidth = ctx.measureText(label).width;
      if (Object.keys(this._labelWidthCache).length > 3000) this._labelWidthCache = {};
      this._labelWidthCache[widthKey] = labelWidth;
    }
    var labelHeight = fontSize + 4;
    var box = {
      x1: labelX - 3 * scale,
      y1: y - labelHeight / 2 - 2,
      x2: labelX + labelWidth + 3 * scale,
      y2: y + labelHeight / 2 + 2,
    };
    var forceLabel = dn.isHovered || dn.isSelected || dn.isCenter;
    if (!forceLabel && (!this._labelInView(box) || this._labelIntersects(box))) {
      ctx.restore();
      return;
    }
    this._labelBoxes.push(box);
    this._insertLabelGrid(box);
    ctx.fillText(label, labelX, y);

    if (dn.isHovered || dn.isSelected) {
      var metaFs = Math.max(8, CFG.NODE_META_SIZE * scale);
      ctx.fillStyle = dark ? "#a6abb4" : "#6b7280";
      ctx.font = metaFs + "px 'SFMono-Regular', Consolas, monospace";
      ctx.textBaseline = "top";
      ctx.fillText(dn.memoryCount + "M / " + dn.degree + " links", labelX, y + 8 * scale);
    }

    ctx.restore();
  };

  Renderer.prototype._labelInView = function(box) {
    return box.x2 >= 0 && box.x1 <= this.width && box.y2 >= 0 && box.y1 <= this.height;
  };

  /* 标签碰撞检测：把已画标签按屏幕网格分桶，只检查重叠格子，O(L) 而非 O(L²)。 */
  Renderer.prototype._insertLabelGrid = function(box) {
    var cellSize = 120;
    var x1 = Math.floor(box.x1 / cellSize), x2 = Math.floor(box.x2 / cellSize);
    var y1 = Math.floor(box.y1 / cellSize), y2 = Math.floor(box.y2 / cellSize);
    for (var gx = x1; gx <= x2; gx++) {
      for (var gy = y1; gy <= y2; gy++) {
        var key = gx * 10000 + gy;
        if (!this._labelGrid[key]) this._labelGrid[key] = [];
        this._labelGrid[key].push(box);
      }
    }
  };

  Renderer.prototype._labelIntersects = function(box) {
    var cellSize = 120;
    var x1 = Math.floor(box.x1 / cellSize), x2 = Math.floor(box.x2 / cellSize);
    var y1 = Math.floor(box.y1 / cellSize), y2 = Math.floor(box.y2 / cellSize);
    for (var gx = x1; gx <= x2; gx++) {
      for (var gy = y1; gy <= y2; gy++) {
        var cell = this._labelGrid[gx * 10000 + gy];
        if (!cell) continue;
        for (var i = 0; i < cell.length; i++) {
          var other = cell[i];
          if (box.x1 <= other.x2 && box.x2 >= other.x1 && box.y1 <= other.y2 && box.y2 >= other.y1) {
            return true;
          }
        }
      }
    }
    return false;
  };

  Renderer.prototype.hitTestNode = function(sx, sy) {
    var best = null, bestDist = Infinity;
    var cellSize = 26;
    var cellX = Math.floor(sx / cellSize);
    var cellY = Math.floor(sy / cellSize);
    for (var gx = -1; gx <= 1; gx++) {
      for (var gy = -1; gy <= 1; gy++) {
        var candidates = this._nodeHitGrid[(cellX + gx) + ":" + (cellY + gy)] || [];
        for (var i = candidates.length - 1; i >= 0; i--) {
          var dn = candidates[i];
          if (dn.isMuted) continue;
          var d = Math.sqrt((sx - dn.sx) ** 2 + (sy - dn.sy) ** 2);
          if (d < dn.sr + CFG.HOVER_RADIUS && d < bestDist) { best = dn; bestDist = d; }
        }
      }
    }
    return best;
  };

  Renderer.prototype.hitTestEdge = function(sx, sy) {
    if (this.performanceTier > 0 && !this._selection) return null;
    var margin = 8;
    for (var i = 0; i < this._drawnEdges.length; i++) {
      var de = this._drawnEdges[i];
      if (de.isMuted) continue;
      /* 快速 AABB 预过滤：光标不在边的包围盒内则跳过，避免每条边都做
         昂贵的点到线段距离计算。 */
      var loX = de.sx < de.tx ? de.sx - margin : de.tx - margin;
      var hiX = de.sx > de.tx ? de.sx + margin : de.tx + margin;
      var loY = de.sy < de.ty ? de.sy - margin : de.ty - margin;
      var hiY = de.sy > de.ty ? de.sy + margin : de.ty + margin;
      if (sx < loX || sx > hiX || sy < loY || sy > hiY) continue;
      var dist = pointToSegmentDistance(sx, sy, de.sx, de.sy, de.tx, de.ty);
      if (dist < margin) return de;
    }
    return null;
  };


  global.GraphRenderer = Renderer;
})(typeof self !== "undefined" ? self : window);
