(() => {
  "use strict";

  /* ================================================================
     Graph2D — Centered Knowledge Graph
     Center node anchors an organic force-directed canvas
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
  };

  const TYPE_COLORS = {
    topic: "#78a94b", person: "#2a9e96", fact: "#c58c2a",
    summary: "#df6d62", other: "#74868a",
  };

  /* ── CSS helpers ───────────────────────────────────────────── */
  function isDark() {
    return (document.documentElement.getAttribute("data-theme") || "light") === "dark";
  }

  /* ── Math helpers ──────────────────────────────────────────── */
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

  /* ── Event helpers ─────────────────────────────────────────── */
  function getPos(e, el) {
    var rect = el.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  /* ═══════════════════════════════════════════════════════════════
     ForceDirectedLayout — true force-directed graph layout
     No fixed center, no BFS rings — nodes repel, edges spring,
     gentle gravity keeps the graph centered.
     ═══════════════════════════════════════════════════════════════ */
  function ForceDirectedLayout() {
    this.centerId = null;   // focus node id (for viewport + visual emphasis)
    this.positions = {};    // id → {tx, ty}
    this.rings = {};        // id → 0 for focus, 1 for others (backward compat)
    this.communities = {};
  }

  ForceDirectedLayout.prototype._hashUnit = function(value, salt) {
    var str = String(value) + ":" + String(salt || 0);
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 100000) / 100000;
  };

  ForceDirectedLayout.prototype._layoutRadius = function(node) {
    var w = clamp(Number(node.weight || 0), 0, 20);
    var mr = clamp(Number(node.memory_count || 0), 0, 15);
    var radius = CFG.NODE_RADIUS_BASE + Math.sqrt(w) * 0.75 + Math.sqrt(mr) * 0.4;
    return clamp(radius, CFG.NODE_RADIUS_MIN, CFG.NODE_RADIUS_MAX);
  };

  ForceDirectedLayout.prototype._buildTopologySeed = function(nodes, edges) {
    var adjacency = {};
    nodes.forEach(function(node) { adjacency[node.id] = []; });
    edges.forEach(function(edge) {
      if (!adjacency[edge.source] || !adjacency[edge.target]) return;
      adjacency[edge.source].push(edge.target);
      adjacency[edge.target].push(edge.source);
    });

    var targetCount = nodes.length < 8
      ? 1
      : clamp(Math.round(Math.sqrt(nodes.length / 6)), 2, 12);
    var ranked = nodes.slice().sort(function(a, b) {
      var degreeDiff = adjacency[b.id].length - adjacency[a.id].length;
      if (degreeDiff) return degreeDiff;
      var weightDiff = Number(b.weight || 0) - Number(a.weight || 0);
      return weightDiff || String(a.id).localeCompare(String(b.id));
    });

    var hubs = [];
    var hubSet = new Set();
    ranked.forEach(function(node) {
      if (hubs.length >= targetCount) return;
      var touchesHub = adjacency[node.id].some(function(id) { return hubSet.has(id); });
      if (!touchesHub || hubs.length === 0) {
        hubs.push(node.id);
        hubSet.add(node.id);
      }
    });
    for (var ri = 0; hubs.length < targetCount && ri < ranked.length; ri++) {
      if (!hubSet.has(ranked[ri].id)) {
        hubs.push(ranked[ri].id);
        hubSet.add(ranked[ri].id);
      }
    }

    var assignment = {};
    var distance = {};
    var queue = [];
    hubs.forEach(function(id, index) {
      assignment[id] = index;
      distance[id] = 0;
      queue.push(id);
    });
    for (var qi = 0; qi < queue.length; qi++) {
      var current = queue[qi];
      var neighbors = adjacency[current].slice().sort(function(a, b) {
        return String(a).localeCompare(String(b));
      });
      neighbors.forEach(function(neighbor) {
        var nextDistance = distance[current] + 1;
        if (distance[neighbor] == null || nextDistance < distance[neighbor]) {
          distance[neighbor] = nextDistance;
          assignment[neighbor] = assignment[current];
          queue.push(neighbor);
        }
      });
    }

    /* Keep disconnected nodes visible, but collect them into one deliberate
       island. Giving every orphan its own community creates hundreds of
       outliers and forces the viewport to shrink the connected graph. */
    var orphanCommunity = hubs.length;
    nodes.forEach(function(node) {
      if (assignment[node.id] == null) assignment[node.id] = orphanCommunity;
    });

    var groups = {};
    nodes.forEach(function(node) {
      var community = assignment[node.id];
      if (!groups[community]) groups[community] = [];
      groups[community].push(node);
    });
    var orderedGroups = Object.entries(groups).sort(function(a, b) {
      var aIsOrphan = Number(a[0]) === orphanCommunity;
      var bIsOrphan = Number(b[0]) === orphanCommunity;
      if (aIsOrphan !== bIsOrphan) return aIsOrphan ? 1 : -1;
      return b[1].length - a[1].length || Number(a[0]) - Number(b[0]);
    });

    var remapped = {};
    orderedGroups.forEach(function(entry, index) {
      entry[1].forEach(function(node) { remapped[node.id] = index; });
    });

    /* Pack communities as deterministic islands. Their footprint grows with
       membership, so large datasets stay separated instead of collapsing
       into a single cloud when bridge edges pull across communities. */
    var centers = {};
    var footprints = {};
    var placed = [];
    var goldenAngle = Math.PI * (3 - Math.sqrt(5));
    orderedGroups.forEach(function(entry, index) {
      var footprint = Math.max(150, Math.sqrt(entry[1].length) * 31 + 54);
      footprints[index] = footprint;
      if (index === 0) {
        centers[index] = { x: 0, y: 0 };
        placed.push({ x: 0, y: 0, radius: footprint });
        return;
      }

      var selected = null;
      for (var attempt = 0; attempt < 260; attempt++) {
        var angle = (index * 13 + attempt) * goldenAngle - Math.PI / 2;
        var radius = 120 + Math.sqrt(attempt + 1) * (footprint + 92);
        var candidate = {
          x: Math.cos(angle) * radius,
          y: Math.sin(angle) * radius * 0.76,
        };
        var clear = placed.every(function(other) {
          var dx = candidate.x - other.x;
          var dy = candidate.y - other.y;
          var required = footprint + other.radius + 88;
          return dx * dx + dy * dy >= required * required;
        });
        if (clear) {
          selected = candidate;
          break;
        }
      }
      if (!selected) {
        var fallbackAngle = index * goldenAngle;
        var fallbackRadius = (footprint + 360) * Math.sqrt(index + 1);
        selected = {
          x: Math.cos(fallbackAngle) * fallbackRadius,
          y: Math.sin(fallbackAngle) * fallbackRadius * 0.76,
        };
      }
      centers[index] = selected;
      placed.push({ x: selected.x, y: selected.y, radius: footprint });
    });

    var positions = {};
    orderedGroups.forEach(function(entry, groupIndex) {
      var members = entry[1].slice().sort(function(a, b) {
        return adjacency[b.id].length - adjacency[a.id].length ||
          Number(b.weight || 0) - Number(a.weight || 0);
      });
      var center = centers[groupIndex];
      members.forEach(function(node, index) {
        var radius = index === 0 ? 0 : 22 * Math.sqrt(index);
        var angle = index * 2.3999632297;
        positions[node.id] = {
          x: center.x + Math.cos(angle) * radius,
          y: center.y + Math.sin(angle) * radius,
          clusterX: center.x,
          clusterY: center.y,
        };
      });
    });

    return { assignment: remapped, positions: positions, centers: centers, footprints: footprints };
  };

  /* Begin force layout setup — can be stepped progressively without blocking. */
  ForceDirectedLayout.prototype.begin = function(nodes, edges, focusId) {
    var self = this;
    this.positions = {};
    this.rings = {};
    this._sim = [];
    this._simEdges = [];
    this._step = 0;
    this._iterations = 0;
    this._largeGraph = false;
    this._topology = null;
    this._done = true;

    var n = nodes.length;
    if (n === 0) return;
    if (n === 1) {
      this.rings[nodes[0].id] = 0;
      this.positions[nodes[0].id] = { tx: 0, ty: 0 };
      this.centerId = nodes[0].id;
      return;
    }
    this._done = false;

    /* Seed positions — pseudo-random spiral from node id (deterministic) */
    var largeGraph = n > 220;
    this._largeGraph = largeGraph;
    var topology = this._buildTopologySeed(nodes, edges);
    this._topology = topology;
    this.communities = topology.assignment;
    var sim = nodes.map(function(nd, i) {
      var seeded = topology.positions[nd.id] || { x: 0, y: 0, clusterX: 0, clusterY: 0 };
      nd.community = topology.assignment[nd.id] || 0;
      return {
        id: nd.id,
        node: nd,
        community: nd.community,
        clusterX: seeded.clusterX,
        clusterY: seeded.clusterY,
        x: seeded.x,
        y: seeded.y,
        vx: 0,
        vy: 0,
        radius: self._layoutRadius(nd),
      };
    });
    this._sim = sim;

    var indexById = {};
    sim.forEach(function(s, i) { indexById[s.id] = i; });

    /* Build edge simulation list */
    var simEdges = [];
    edges.forEach(function(edge) {
      var si = indexById[edge.source];
      var ti = indexById[edge.target];
      if (si == null || ti == null) return;
      var weight = clamp(Number(edge.weight || 1), 0.4, 12);
      var confidence = clamp(Number(edge.confidence || 0.8), 0.2, 1);
      simEdges.push({
        source: si,
        target: ti,
        weight: weight,
        confidence: confidence,
        sameCommunity: sim[si].community === sim[ti].community,
        distanceJitter: self._hashUnit(String(edge.id) + ":" + edge.source + ":" + edge.target, 61),
      });
    });
    this._simEdges = simEdges;

    /* Mark focus node (treated gently in gravity, not locked) */
    var focusIndex = focusId != null ? indexById[focusId] : -1;
    if (focusIndex >= 0) {
      sim[focusIndex].isFocus = true;
      this.centerId = focusId;
    } else {
      this.centerId = null;
    }

    /* ── N-body force simulation ── */
    this._iterations = n > 2000 ? 35
      : n > 1000 ? 55
      : n > 500 ? 90
      : n > 220 ? 150
      : n > 100 ? 350
      : CFG.FORCE_ITERATIONS;

    var repelPair = function(a, b, cooled, effectiveRange) {
      var dx = a.x - b.x;
      var dy = a.y - b.y;
      var distSq = dx * dx + dy * dy;
      if (distSq < 0.01) {
        var kick = self._hashUnit(a.id + ":" + b.id, 43) * Math.PI * 2;
        dx = Math.cos(kick) * 0.1;
        dy = Math.sin(kick) * 0.1;
        distSq = dx * dx + dy * dy;
      }
      var dist = Math.sqrt(distSq);
      var minSep = (a.radius + b.radius) * 2.2 + 16;
      var repulse = CFG.FORCE_REPULSION * cooled / Math.max(distSq, minSep * minSep * 0.25);

      if (dist < effectiveRange) {
        var falloff = 1 - dist / effectiveRange;
        repulse *= falloff * falloff;
      } else {
        repulse *= 0.05;
      }
      if (dist < minSep) repulse += (minSep - dist) * 0.35;

      var fx = dx / dist * repulse;
      var fy = dy / dist * repulse;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    };
    this._repelPair = repelPair;

    /* 空间网格桶跨迭代复用（数值键 + 数组复用），避免每迭代重建对象。 */
    this._buckets = {};
    this._usedKeys = [];
  };

  /* Run a batch of force iterations. */
  ForceDirectedLayout.prototype.runLayoutSteps = function(count) {
    if (this._done) return;
    var self = this;
    var sim = this._sim;
    var simEdges = this._simEdges;
    var iterations = this._iterations;
    var largeGraph = this._largeGraph;
    var n = sim.length;
    var repelPair = this._repelPair;
    var buckets = this._buckets;
    var usedKeys = this._usedKeys;

    var end = Math.min(this._step + count, iterations);
    for (var step = this._step; step < end; step++) {
      var alpha = 1 - step / iterations;
      var cooled = 0.3 + alpha * 0.7;

      /* Large graphs use a spatial grid so repulsion stays near-linear. */
      if (largeGraph) {
        var cellSize = 92;
        for (var uk = 0; uk < usedKeys.length; uk++) buckets[usedKeys[uk]].length = 0;
        usedKeys.length = 0;
        for (var gi = 0; gi < sim.length; gi++) {
          var gx = Math.floor(sim[gi].x / cellSize);
          var gy = Math.floor(sim[gi].y / cellSize);
          var gkey = gx * 1000000 + gy;
          var bucket = buckets[gkey];
          if (!bucket) { bucket = buckets[gkey] = []; usedKeys.push(gkey); }
          bucket.push(gi);
        }
        for (var si = 0; si < sim.length; si++) {
          var source = sim[si];
          var sourceX = Math.floor(source.x / cellSize);
          var sourceY = Math.floor(source.y / cellSize);
          for (var bx = -1; bx <= 1; bx++) {
            for (var by = -1; by <= 1; by++) {
              var nearby = buckets[(sourceX + bx) * 1000000 + (sourceY + by)];
              if (!nearby) continue;
              for (var bi = 0; bi < nearby.length; bi++) {
                if (nearby[bi] <= si) continue;
                repelPair(source, sim[nearby[bi]], cooled, cellSize * 1.8);
              }
            }
          }
        }
      } else {
        var effectiveRange = 280 + Math.min(120, n * 1.2);
        for (var i = 0; i < sim.length; i++) {
          for (var j = i + 1; j < sim.length; j++) {
            repelPair(sim[i], sim[j], cooled, effectiveRange);
          }
        }
      }

      /* Spring attraction along edges with adaptive strength */
      simEdges.forEach(function(edge) {
        var s = sim[edge.source];
        var t = sim[edge.target];
        var dx = t.x - s.x;
        var dy = t.y - s.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 0.001;

        /* Base distance varies by edge weight */
        var baseDistance = CFG.FORCE_LINK_DISTANCE + edge.distanceJitter * 34;
        var weightFactor = Math.min(1.5, Math.sqrt(edge.weight || 1) * 0.3);
        var desired = (baseDistance - weightFactor * 15) * (edge.sameCommunity ? 0.82 : 1.55);

        /* Adaptive spring strength - weaker for long edges */
        var lengthRatio = dist / desired;
        var adaptiveStrength = CFG.FORCE_LINK_STRENGTH * edge.confidence * cooled;
        if (!edge.sameCommunity) adaptiveStrength *= largeGraph ? 0.025 : 0.3;
        if (lengthRatio > 2) {
          adaptiveStrength *= 0.5;
        }

        var force = (dist - desired) * adaptiveStrength;
        var fx = (dx / dist) * force;
        var fy = (dy / dist) * force;
        s.vx += fx; s.vy += fy;
        t.vx -= fx; t.vy -= fy;
      });

      /* Gentle centering gravity with mass-based scaling */
      for (var k = 0; k < sim.length; k++) {
        var sn = sim[k];
        /* Gravity scales with node degree/weight to keep important nodes more central */
        var massFactor = 1 + Math.sqrt(sn.node.weight || 0) * 0.1 + Math.sqrt(sn.node.degree || 0) * 0.05;
        var gravity = CFG.FORCE_GRAVITY * cooled / massFactor;
        if (sn.isFocus) gravity *= 1.8;
        var anchorStrength = largeGraph ? 0.045 * cooled : gravity * 1.45;
        sn.vx -= (sn.x - sn.clusterX) * anchorStrength;
        sn.vy -= (sn.y - sn.clusterY) * anchorStrength;
        sn.vx -= sn.x * gravity * (largeGraph ? 0.015 : 0.12);
        sn.vy -= sn.y * gravity * (largeGraph ? 0.015 : 0.12);
      }

      /* Damping + position update */
      sim.forEach(function(sn) {
        sn.vx *= CFG.FORCE_DAMPING;
        sn.vy *= CFG.FORCE_DAMPING;
        var speed = Math.sqrt(sn.vx * sn.vx + sn.vy * sn.vy);
        if (speed > CFG.FORCE_MAX_SPEED) {
          sn.vx = sn.vx / speed * CFG.FORCE_MAX_SPEED;
          sn.vy = sn.vy / speed * CFG.FORCE_MAX_SPEED;
        }
        sn.x += sn.vx;
        sn.y += sn.vy;
      });
    }
    this._step = end;
    if (this._step >= iterations) this.end();
  };

  /* Finish layout: drift correction + rings/positions assignment. */
  ForceDirectedLayout.prototype.end = function() {
    if (this._done) return;
    this._done = true;
    var self = this;
    var sim = this._sim;
    var largeGraph = this._largeGraph;
    var topology = this._topology;

    /* Correct any residual bridge-edge drift without flattening the internal
       topology. Each large-graph community ends at its packed anchor. */
    if (largeGraph) {
      var communityStats = {};
      sim.forEach(function(sn) {
        if (!communityStats[sn.community]) {
          communityStats[sn.community] = { x: 0, y: 0, count: 0 };
        }
        communityStats[sn.community].x += sn.x;
        communityStats[sn.community].y += sn.y;
        communityStats[sn.community].count += 1;
      });
      sim.forEach(function(sn) {
        var stats = communityStats[sn.community];
        var center = topology.centers[sn.community];
        if (!stats || !center || !stats.count) return;
        sn.x += center.x - stats.x / stats.count;
        sn.y += center.y - stats.y / stats.count;
      });
    }

    /* Assign rings: 0 = focus node, 1 = others (for backward compat) */
    sim.forEach(function(sn) {
      self.rings[sn.id] = sn.isFocus ? 0 : 1;
      self.positions[sn.id] = { tx: sn.x, ty: sn.y };
    });
  };

  /* Synchronous wrapper — small graphs / fallback. */
  ForceDirectedLayout.prototype.compute = function(nodes, edges, focusId) {
    this.begin(nodes, edges, focusId);
    if (!this._done) this.runLayoutSteps(this._iterations);
  };

  /* Get target position for a node */
  ForceDirectedLayout.prototype.getTarget = function(nodeId) {
    var p = this.positions[nodeId];
    return p || { tx: 0, ty: 0 };
  };

  /* Get ring (0 = focus, 1 = normal) */
  ForceDirectedLayout.prototype.getRing = function(nodeId) {
    return this.rings[nodeId] != null ? this.rings[nodeId] : 1;
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
  }

  Renderer.prototype.configureData = function(nodes, edges) {
    var self = this;
    this.performanceTier = performanceTier(nodes.length, edges.length);
    this._adjacency = {};
    this._nodeEdges = {};
    this._memoryEdges = {};
    this._structuralEdges = [];
    this._communityBundles = [];
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

  Renderer.prototype.drawBackground = function(dark, animateDecorations) {
    var ctx = this.ctx;
    var step = clamp(30 * this.viewport.scale, 22, 42);
    var ox = ((this.viewport.ox * this.viewport.scale) % step + step) % step;
    var oy = ((this.viewport.oy * this.viewport.scale) % step + step) % step;

    ctx.save();
    ctx.fillStyle = themeColor("--graph-surface", dark ? "#11130f" : "#f8fbfa");
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.fillStyle = dark ? "rgba(183,243,74,0.13)" : "rgba(40,92,94,0.12)";
    for (var x = ox; x <= this.width; x += step) {
      for (var y = oy; y <= this.height; y += step) {
        ctx.beginPath();
        ctx.arc(x, y, 0.75, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    var majorStep = step * 5;
    ctx.strokeStyle = dark ? "rgba(183,243,74,0.055)" : "rgba(40,92,94,0.05)";
    ctx.lineWidth = 1;
    for (var mx = ox % majorStep; mx <= this.width; mx += majorStep) {
      ctx.beginPath();
      ctx.moveTo(mx, 0);
      ctx.lineTo(mx, this.height);
      ctx.stroke();
    }
    for (var my = oy % majorStep; my <= this.height; my += majorStep) {
      ctx.beginPath();
      ctx.moveTo(0, my);
      ctx.lineTo(this.width, my);
      ctx.stroke();
    }

    if (animateDecorations) {
      var scanY = (Date.now() * 0.018) % Math.max(this.height, 1);
      ctx.fillStyle = dark ? "rgba(183,243,74,0.13)" : "rgba(42,167,157,0.1)";
      ctx.fillRect(0, scanY, this.width, 1);
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
    ctx.save();
    for (var e = 0; e < visibleEdges.length; e++) {
      var edge = visibleEdges[e];
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
    });
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
    var labelWidth = ctx.measureText(label).width;
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

  Renderer.prototype._labelIntersects = function(box) {
    for (var i = 0; i < this._labelBoxes.length; i++) {
      var other = this._labelBoxes[i];
      if (box.x1 <= other.x2 && box.x2 >= other.x1 && box.y1 <= other.y2 && box.y2 >= other.y1) {
        return true;
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

  Interaction.prototype._requestRender = function() {
    if (this.cb.onRenderRequest) this.cb.onRenderRequest();
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
      this._requestRender();
      return;
    }

    if (this._panning) {
      vr.ox = this._panStart.ox + (pos.x - this._panStart.mx) / vr.scale;
      vr.oy = this._panStart.oy + (pos.y - this._panStart.my) / vr.scale;
      this._requestRender();
      return;
    }

    var hit = this.renderer.hitTestNode(pos.x, pos.y);
    if (hit) {
      if (this._hoverId !== hit.id || this._hoverType !== "node") {
        this._hoverId = hit.id; this._hoverType = "node";
        if (this.cb.onNodeHover) this.cb.onNodeHover(hit.id);
        this._requestRender();
      }
      this.canvas.style.cursor = "pointer";
      return;
    }

    var hitE = this.renderer.hitTestEdge(pos.x, pos.y);
    if (hitE) {
      if (this._hoverId !== hitE.id || this._hoverType !== "edge") {
        this._hoverId = hitE.id; this._hoverType = "edge";
        this._requestRender();
      }
      this.canvas.style.cursor = "pointer";
      return;
    }

    if (this._hoverId !== null) {
      this._hoverId = null; this._hoverType = null;
      if (this.cb.onNodeHover) this.cb.onNodeHover(null);
      this._requestRender();
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
    this._requestRender();
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
      this._requestRender();
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
    this._layout = new ForceDirectedLayout();
    this._animProgress = 1; // 0→1 for position transitions
    this._needsRender = true;
    this._ambientMotion = false;
    this._instantLayout = false;
    this._layoutGeneration = 0; // 渐进式布局代数守卫
    this._lastLayoutSignature = null;
  }

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
    /* 布局缓存：节点/边结构未变时复用上次力布局结果，跳过整轮 compute()。 */
    var signature = this._graphSignature();
    if (signature === this._lastLayoutSignature) {
      if (centerId != null) this._layout.centerId = centerId;
      this._finishLayout(centerId, true);
      return;
    }
    this._lastLayoutSignature = signature;

    /* 大图使用渐进式布局：分片跑迭代，边算边显示，不阻塞主线程。 */
    if (this._nodes.length > 220) {
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

  Animator.prototype._progressiveLayout = function(centerId, generation) {
    var self = this;
    /* 代数守卫：若期间又加载了新图，丢弃这条过期链路。 */
    if (generation !== this._layoutGeneration) return;
    this._layout.runLayoutSteps(8);
    if (generation !== this._layoutGeneration) return;
    var sim = this._layout._sim;
    for (var i = 0; i < sim.length; i++) {
      this._nodes[i].x = sim[i].x;
      this._nodes[i].y = sim[i].y;
      this._nodes[i]._prevX = null;
      this._nodes[i]._prevY = null;
    }
    if (!this._layout._done) {
      this._renderFrame();
      requestAnimationFrame(function() {
        self._progressiveLayout(centerId, generation);
      });
    } else {
      if (generation === this._layoutGeneration) this._finishLayout(centerId);
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
      dirty = true;
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
