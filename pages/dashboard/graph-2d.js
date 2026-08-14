(() => {
  "use strict";

  /* 共享常量与工具（见 graph-shared.js） */
  var CFG = GraphShared.CFG;
  var TYPE_COLORS = GraphShared.TYPE_COLORS;
  var isDark = GraphShared.isDark;
  var clamp = GraphShared.clamp;
  var lerp = GraphShared.lerp;
  var performanceTier = GraphShared.performanceTier;
  var themeColor = GraphShared.themeColor;
  var hexToRgba = GraphShared.hexToRgba;
  var getPos = GraphShared.getPos;
  var easeInOutCubic = GraphShared.easeInOutCubic;
  var pointToSegmentDistance = GraphShared.pointToSegmentDistance;
  /* 渲染器与交互（见 graph-renderer.js / graph-interaction.js） */
  var Renderer = GraphRenderer;
  var Interaction = GraphInteraction;

  /* ================================================================
     ForceDirectedLayout — 力导向布局（实现见 graph-layout-core.js，
     主线程与 Web Worker 共用）。此处仅保留薄工厂，接口与旧实现一致。
     ================================================================ */
  function ForceDirectedLayout() {
    return window.GraphLayoutCore.createForceLayout();
  }

  /* Worker 版布局：布局迭代在 Web Worker 中运行，主线程零 CPU 占用。
     runLayoutSteps 返回 Promise（消息回包后 resolve），接口其余部分
     与内联布局一致，供 Animator 无差别使用。 */
  function WorkerForceLayout() {
    this.isWorker = false;
    this._done = true;
    this.positions = {};
    this.rings = {};
    this.communities = {};
    this.centerId = null;
    this._worker = null;
    this._pending = null;
    this._lastPositions = {};
    this._lastSimPositions = {};
    try {
      this._worker = new Worker("./graph-layout-worker.js");
      this.isWorker = true;
      var self = this;
      this._worker.onmessage = function(e) { self._onMessage(e.data); };
    } catch (err) {
      this._worker = null;
    }
  }

  WorkerForceLayout.prototype.begin = function(nodes, edges, centerId) {
    this._done = false;
    this._lastSimPositions = {};
    this._worker.postMessage({
      type: "begin",
      nodes: nodes.map(function(n) {
        return { id: n.id, weight: n.weight || 0, degree: n.degree || 0, memory_count: n.memory_count || 0 };
      }),
      edges: edges.map(function(e) {
        return { id: e.id, source: e.source, target: e.target, weight: e.weight || 1, confidence: e.confidence || 0.8 };
      }),
      centerId: centerId == null ? null : centerId,
    });
  };

  WorkerForceLayout.prototype.runLayoutSteps = function(count) {
    var self = this;
    return new Promise(function(resolve) {
      self._pending = resolve;
      self._worker.postMessage({ type: "step", count: count });
    });
  };

  WorkerForceLayout.prototype.end = function() {
    this._worker.postMessage({ type: "end" });
  };

  WorkerForceLayout.prototype.compute = function(nodes, edges, focusId) {
    this.begin(nodes, edges, focusId);
  };

  WorkerForceLayout.prototype.getTarget = function(nodeId) {
    var p = this.positions[nodeId];
    return p || { tx: 0, ty: 0 };
  };

  WorkerForceLayout.prototype.getRing = function(nodeId) {
    return this.rings[nodeId] != null ? this.rings[nodeId] : 1;
  };

  WorkerForceLayout.prototype._onMessage = function(msg) {
    if (msg.type === "positions") {
      this._lastSimPositions = msg.positions;
      this._done = msg.done;
      if (msg.done) {
        this.positions = msg.targets;
        this.rings = msg.rings;
        this.communities = msg.communities;
      }
      if (this._pending) {
        var resolve = this._pending;
        this._pending = null;
        resolve();
      }
    }
  };

  /* ═══════════════════════════════════════════════════════════════
     Animator — RAF loop with layout position tweening
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
    this._layout = this._createLayout();
    this._animProgress = 1; // 0→1 for position transitions
    this._needsRender = true;
    this._ambientMotion = false;
    this._instantLayout = false;
    this._layoutGeneration = 0; // 渐进式布局代数守卫
    this._lastLayoutSignature = null;
  }

  /* 优先使用 Web Worker 布局；不可用时回退到主线程内联布局。 */
  Animator.prototype._createLayout = function() {
    if (typeof Worker === "function") {
      try {
        var workerLayout = new WorkerForceLayout();
        if (workerLayout.isWorker) return workerLayout;
      } catch (err) {
        /* fall through to inline layout */
      }
    }
    return new ForceDirectedLayout();
  };

  Animator.prototype.fitViewport = function(options) {
    options = options || {};
    if (!this._nodes.length || !this.renderer.width || !this.renderer.height) return;

    var centerId = options.centerId != null ? options.centerId : this._layout.centerId;
    var centerTarget = centerId != null ? this._layout.getTarget(centerId) : null;
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

    for (var i = 0; i < this._nodes.length; i++) {
      var nd = this._nodes[i];
      var target = this._layout.getTarget(nd.id);
      var isCenter = this._layout.centerId != null && nd.id === this._layout.centerId;
      var pad = this.renderer.nodeWorldRadius(nd, isCenter) + 28;
      var prominent = Number(nd.degree || 0) >= 2 || Number(nd.memory_count || 0) >= 3;
      var labelPad = prominent
        ? Math.min(120, 20 + String(nd.label || "").length * 6)
        : 0;
      minX = Math.min(minX, target.tx - pad);
      maxX = Math.max(maxX, target.tx + pad + labelPad);
      minY = Math.min(minY, target.ty - pad);
      maxY = Math.max(maxY, target.ty + pad);
    }

    if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minY) || !Number.isFinite(maxY)) return;

    var boundsW = Math.max(1, maxX - minX);
    var boundsH = Math.max(1, maxY - minY);
    var padding = this.renderer.width < 520 ? 0.9 : 0.92;
    var fitScale = Math.min(
      (this.renderer.width * padding) / boundsW,
      (this.renderer.height * padding) / boundsH
    );
    var scale = clamp(fitScale, CFG.ZOOM_MIN, 1.65);
    var cx = (minX + maxX) / 2;
    var cy = (minY + maxY) / 2;

    if (centerTarget) {
      var centerBias = this.renderer.width < 520 ? 0.36 : 0.62;
      cx = lerp(cx, centerTarget.tx, centerBias);
      cy = lerp(cy, centerTarget.ty, centerBias * 0.78);
    }

    this.renderer.viewport.scale = scale;
    this.renderer.viewport.ox = -cx;
    this.renderer.viewport.oy = -cy;
    this._needsRender = true;
  };

  Animator.prototype.start = function() {
    if (this._running) return;
    this._running = true;
    var self = this;
    this._rafId = requestAnimationFrame(function() { self._tick(); });
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
    var tier = this.renderer.configureData(nodes, edges);
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this._ambientMotion = tier === 0 && !reduceMotion &&
      nodes.length <= CFG.AMBIENT_NODE_LIMIT && edges.length <= CFG.AMBIENT_EDGE_LIMIT;
    this._instantLayout = tier >= 2 || reduceMotion;
  };

  Animator.prototype.layoutGraph = function(centerId) {
    var self = this;
    /* Save previous positions for animation */
    this._nodes.forEach(function(n) {
      n._prevX = n.x;
      n._prevY = n.y;
    });
    /* 布局缓存：结构未变且上次布局已完成时复用结果，跳过整轮 compute()。 */
    var signature = this._graphSignature();
    if (signature === this._lastLayoutSignature && this._layout._done) {
      if (centerId != null) this._layout.centerId = centerId;
      this._finishLayout(centerId, true);
      return;
    }
    this._lastLayoutSignature = signature;

    /* 中等及以上图使用渐进式布局：分片跑迭代，边算边显示，不阻塞主线程。
       支持 Worker 布局（异步消息驱动）与内联布局（同步分片）两种实现。 */
    if (this._nodes.length > CFG.PROGRESSIVE_LAYOUT_THRESHOLD) {
      this.stop();
      this._animProgress = 1;
      this._layoutGeneration += 1;
      /* tier>0 时清空上一张图的边子集/社区束，避免渐进过程中画出过期边；
         布局完成后由 _finishLayout 的 prepareGraph 统一重建。 */
      if (this.renderer.performanceTier > 0) {
        this.renderer._structuralEdges = [];
        this.renderer._communityBundles = [];
      }
      this._layout.begin(this._nodes, this._edges, centerId);
      this._progressiveLayout(centerId, this._layoutGeneration);
    } else {
      this._layout.compute(this._nodes, this._edges, centerId);
      this._finishLayout(centerId);
    }
  };

  /* 渐进式布局主循环：每帧跑一批迭代并渲染当前状态。
     Worker 布局的 runLayoutSteps 返回 Promise，等消息回包后继续；
     内联布局同步执行，await 立即放行。 */
  Animator.prototype._progressiveLayout = async function(centerId, generation) {
    var self = this;
    /* 代数守卫：若期间又加载了新图，丢弃这条过期链路。 */
    if (generation !== this._layoutGeneration) return;
    await this._layout.runLayoutSteps(8);
    if (generation !== this._layoutGeneration) return;
    this._applyLayoutPositions();
    if (!this._layout._done) {
      this._renderFrame();
      requestAnimationFrame(function() {
        self._progressiveLayout(centerId, generation);
      });
    } else {
      if (generation === this._layoutGeneration) this._finishLayout(centerId);
    }
  };

  /* 把布局当前位置同步到节点坐标（内联读 _sim，Worker 读消息回包）。 */
  Animator.prototype._applyLayoutPositions = function() {
    var layout = this._layout;
    if (layout.isWorker) {
      var positions = layout._lastSimPositions;
      for (var id in positions) {
        var nd = this._nodeMap[id];
        if (!nd) continue;
        nd.x = positions[id].x;
        nd.y = positions[id].y;
        nd._prevX = null;
        nd._prevY = null;
      }
      return;
    }
    var sim = layout._sim;
    for (var i = 0; i < sim.length; i++) {
      this._nodes[i].x = sim[i].x;
      this._nodes[i].y = sim[i].y;
      this._nodes[i]._prevX = null;
      this._nodes[i]._prevY = null;
    }
  };

  Animator.prototype._finishLayout = function(centerId, immediate) {
    var self = this;
    this.renderer.prepareGraph(this._nodes, this._edges, this._layout);
    this.fitViewport({ centerId: centerId });
    this._animProgress = (immediate || this._instantLayout) ? 1 : 0;
    if (this._instantLayout) {
      this._nodes.forEach(function(node) {
        if (node.fixed) return;
        var target = self._layout.getTarget(node.id);
        node.x = target.tx;
        node.y = target.ty;
        node._prevX = null;
        node._prevY = null;
      });
    }
    this._needsRender = true;
    this.start();
  };

  Animator.prototype._renderFrame = function() {
    this.renderer.clear();
    var sel = this.renderer._selection;
    var hoverId = this.interaction.getHoverId();
    this.renderer.render(this._nodes, this._edges, this._nodeMap, sel, hoverId, this._layout, 1);
    this._needsRender = false;
  };

  Animator.prototype._graphSignature = function() {
    /* 轻量签名：排序后的节点 id + 边 key。O(N log N)，远低于力布局成本。 */
    var nodeIds = this._nodes.map(function(n) { return String(n.id); }).sort().join(",");
    var edgeKeys = this._edges.map(function(e) {
      return e.source + ">" + e.target;
    }).sort().join(",");
    return nodeIds + "|" + edgeKeys;
  };

  Animator.prototype.recenter = function(centerId) {
    /* 只移动视口复用现有布局，不再触发全量力布局重算（此前 tier 0 会重算）。 */
    this._layout.centerId = centerId == null ? null : centerId;
    if (centerId == null) {
      this.fitViewport({ centerId: null });
    } else {
      var target = this._layout.getTarget(centerId);
      this.renderer.viewport.ox = -target.tx;
      this.renderer.viewport.oy = -target.ty;
      this.renderer.viewport.scale = Math.max(this.renderer.viewport.scale, 0.28);
    }
    this._needsRender = true;
    this.start();
  };

  Animator.prototype._tick = function() {
    if (!this._running) return;
    this._rafId = null;

    /* Animate positions toward layout targets */
    var dirty = this._needsRender;
    if (this._animProgress < 1) {
      dirty = true;
      this._animProgress = Math.min(1, this._animProgress + CFG.ANIM_SPEED);
      var ap = easeInOutCubic(this._animProgress);

      for (var i = 0; i < this._nodes.length; i++) {
        var nd = this._nodes[i];
        if (nd.fixed) continue;
        var target = this._layout.getTarget(nd.id);
        if (nd._prevX == null) { nd._prevX = nd.x; nd._prevY = nd.y; }
        nd.x = lerp(nd._prevX, target.tx, ap);
        nd.y = lerp(nd._prevY, target.ty, ap);
      }
      if (this._animProgress >= 1) {
        /* Lock to exact targets */
        for (var j = 0; j < this._nodes.length; j++) {
          var nd2 = this._nodes[j];
          if (nd2.fixed) continue;
          var tgt = this._layout.getTarget(nd2.id);
          nd2.x = tgt.tx; nd2.y = tgt.ty;
          nd2._prevX = null; nd2._prevY = null;
        }
      }
    } else if (this._ambientMotion) {
      /* 环境漂浮：位置每帧更新（廉价），渲染按图规模降帧，避免永久满帧重绘。 */
      var now = Date.now() / 1000;
      for (var k = 0; k < this._nodes.length; k++) {
        var floatNode = this._nodes[k];
        if (floatNode.fixed) continue;
        var home = this._layout.getTarget(floatNode.id);
        var ring = this._layout.getRing(floatNode.id);
        var weight = clamp(Number(floatNode.weight || 0), 0, 20);
        var amp = ring === 0 ? 1.2 : 2.0 + Math.sqrt(weight) * 0.4;
        var phase = (floatNode.id % 17) * 0.37;
        floatNode.x = lerp(floatNode.x, home.tx + Math.sin(now * 0.65 + phase) * amp, CFG.IDLE_DAMPING);
        floatNode.y = lerp(floatNode.y, home.ty + Math.cos(now * 0.55 + phase) * amp, CFG.IDLE_DAMPING);
      }
      this._ambientFrame = (this._ambientFrame || 0) + 1;
      var ambientStride = this._nodes.length > 300 ? 4 : this._nodes.length > 100 ? 2 : 1;
      if (this._ambientFrame % ambientStride === 0) dirty = true;
    }

    if (dirty || this._needsRender) {
      this.renderer.clear();
      var sel = this.renderer._selection;
      var hoverId = this.interaction.getHoverId();
      this.renderer.render(this._nodes, this._edges, this._nodeMap, sel, hoverId, this._layout, this._animProgress);
      this._needsRender = false;
    }

    if (this._animProgress < 1 || this._ambientMotion) {
      var self = this;
      this._rafId = requestAnimationFrame(function() { self._tick(); });
    } else {
      this._running = false;
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
      onRenderRequest: function() {
        if (self.animator) self.animator.wake();
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
        labelScore: Number(node.degree || 0) * 2 +
          Number(node.memory_count || 0) * 3 +
          Number(node.entry_count || 0) +
          Number(node.weight || 0),
        color: TYPE_COLORS[node.type] || TYPE_COLORS.other,
      });
    });

    var edges = [];
    var edgeSeen = {};
    rawEdges.forEach(function(edge) {
      var eid = edge.id != null ? Number(edge.id) : (edge.source + ":" + edge.target + ":" + edge.memory_id);
      if (edgeSeen[eid]) return;
      edgeSeen[eid] = true;
      var bendSeed = String(eid).split("").reduce(function(sum, character) {
        return sum + character.charCodeAt(0);
      }, 0);
      edges.push({
        id: eid, source: Number(edge.source), target: Number(edge.target),
        relation_type: edge.relation_type || "related",
        memory_id: Number(edge.memory_id || 0),
        weight: Number(edge.weight || 1),
        confidence: Number(edge.confidence || 0.8),
        __color: relationColor(edge.relation_type),
        _bendSign: bendSeed % 2 ? 1 : -1,
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

    /* Apply centered force layout with animation */
    this.animator.layoutGraph(centerId);

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
    this.animator.recenter(null);
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

  Graph2D.prototype.getDiagnostics = function() {
    return {
      performanceTier: this.renderer ? this.renderer.performanceTier : 0,
      sourceNodes: this._nodes ? this._nodes.length : 0,
      sourceEdges: this._edges ? this._edges.length : 0,
      renderedNodes: this.renderer ? this.renderer._drawnNodes.length : 0,
      renderedEdges: this.renderer ? this.renderer._drawnEdges.length : 0,
      structuralEdges: this.renderer ? this.renderer._structuralEdges.length : 0,
      communityBundles: this.renderer ? this.renderer._communityBundles.length : 0,
      animatorRunning: Boolean(this.animator && this.animator._running),
      ambientMotion: Boolean(this.animator && this.animator._ambientMotion),
    };
  };

  function relationColor(type) {
    var palette = ["#2a9e96", "#c58c2a", "#df6d62", "#78a94b", "#74868a"];
    var h = String(type || "related").split("").reduce(function(a, c) { return a * 31 + c.charCodeAt(0); }, 7);
    return palette[Math.abs(h) % palette.length];
  }

  /* ═══════════════════════════════════════════════════════════════
     Export
     ═══════════════════════════════════════════════════════════════ */
  window.Graph2D = new Graph2D();
})();
