/* ================================================================
   graph-layout-core.js — 力导向布局核心（纯计算，无 DOM）
   供 graph-2d.js（主线程）与 graph-layout-worker.js（Web Worker）共用，
   通过 importScripts / 普通 script 标签加载，挂载到全局 GraphLayoutCore。
   ================================================================ */
(function(global) {
  "use strict";

  /* ── 配置（与 graph-2d.js 的布局参数保持一致） ─────────────────── */
  var CFG = {
    NODE_RADIUS_MIN: 4,
    NODE_RADIUS_MAX: 10,
    NODE_RADIUS_BASE: 4,
    FORCE_ITERATIONS: 400,
    FORCE_REPULSION: 1680,
    FORCE_LINK_DISTANCE: 108,
    FORCE_LINK_STRENGTH: 0.032,
    FORCE_GRAVITY: 0.0095,
    FORCE_DAMPING: 0.82,
    FORCE_MAX_SPEED: 15,
  };

  function clamp(value, lo, hi) { return Math.min(hi, Math.max(lo, value)); }

  function hashUnit(value, salt) {
    var str = String(value) + ":" + String(salt || 0);
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return ((h >>> 0) % 100000) / 100000;
  }

  function layoutRadius(node) {
    var w = clamp(Number(node.weight || 0), 0, 20);
    var mr = clamp(Number(node.memory_count || 0), 0, 15);
    var radius = CFG.NODE_RADIUS_BASE + Math.sqrt(w) * 0.75 + Math.sqrt(mr) * 0.4;
    return clamp(radius, CFG.NODE_RADIUS_MIN, CFG.NODE_RADIUS_MAX);
  }

  function buildTopologySeed(nodes, edges) {
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

    var centers = {};
    var placed = [];
    var goldenAngle = Math.PI * (3 - Math.sqrt(5));
    orderedGroups.forEach(function(entry, index) {
      var footprint = Math.max(150, Math.sqrt(entry[1].length) * 31 + 54);
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

    return { assignment: remapped, positions: positions, centers: centers };
  }

  function begin(nodes, edges, focusId) {
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

    var largeGraph = n > 220;
    this._largeGraph = largeGraph;
    var topology = buildTopologySeed(nodes, edges);
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
        radius: layoutRadius(nd),
      };
    });
    this._sim = sim;

    var indexById = {};
    sim.forEach(function(s, i) { indexById[s.id] = i; });

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
        distanceJitter: hashUnit(String(edge.id) + ":" + edge.source + ":" + edge.target, 61),
      });
    });
    this._simEdges = simEdges;

    var focusIndex = focusId != null ? indexById[focusId] : -1;
    if (focusIndex >= 0) {
      sim[focusIndex].isFocus = true;
      this.centerId = focusId;
    } else {
      this.centerId = null;
    }

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
        var kick = hashUnit(a.id + ":" + b.id, 43) * Math.PI * 2;
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

    this._buckets = {};
    this._usedKeys = [];
  }

  function runLayoutSteps(count) {
    if (this._done) return;
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

      simEdges.forEach(function(edge) {
        var s = sim[edge.source];
        var t = sim[edge.target];
        var dx = t.x - s.x;
        var dy = t.y - s.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 0.001;

        var baseDistance = CFG.FORCE_LINK_DISTANCE + edge.distanceJitter * 34;
        var weightFactor = Math.min(1.5, Math.sqrt(edge.weight || 1) * 0.3);
        var desired = (baseDistance - weightFactor * 15) * (edge.sameCommunity ? 0.82 : 1.55);

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

      for (var k = 0; k < sim.length; k++) {
        var sn = sim[k];
        var massFactor = 1 + Math.sqrt(sn.node.weight || 0) * 0.1 + Math.sqrt(sn.node.degree || 0) * 0.05;
        var gravity = CFG.FORCE_GRAVITY * cooled / massFactor;
        if (sn.isFocus) gravity *= 1.8;
        var anchorStrength = largeGraph ? 0.045 * cooled : gravity * 1.45;
        sn.vx -= (sn.x - sn.clusterX) * anchorStrength;
        sn.vy -= (sn.y - sn.clusterY) * anchorStrength;
        sn.vx -= sn.x * gravity * (largeGraph ? 0.015 : 0.12);
        sn.vy -= sn.y * gravity * (largeGraph ? 0.015 : 0.12);
      }

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
  }

  function end() {
    if (this._done) return;
    this._done = true;
    var self = this;
    var sim = this._sim;
    var largeGraph = this._largeGraph;
    var topology = this._topology;

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

    sim.forEach(function(sn) {
      self.rings[sn.id] = sn.isFocus ? 0 : 1;
      self.positions[sn.id] = { tx: sn.x, ty: sn.y };
    });
  }

  function compute(nodes, edges, focusId) {
    this.begin(nodes, edges, focusId);
    if (!this._done) this.runLayoutSteps(this._iterations);
  }

  function getTarget(nodeId) {
    var p = this.positions[nodeId];
    return p || { tx: 0, ty: 0 };
  }

  function getRing(nodeId) {
    return this.rings[nodeId] != null ? this.rings[nodeId] : 1;
  }

  function createForceLayout() {
    return {
      centerId: null,
      positions: {},
      rings: {},
      communities: {},
      _sim: [],
      _simEdges: [],
      _step: 0,
      _iterations: 0,
      _largeGraph: false,
      _topology: null,
      _repelPair: null,
      _buckets: {},
      _usedKeys: [],
      _done: true,
      begin: begin,
      runLayoutSteps: runLayoutSteps,
      end: end,
      compute: compute,
      getTarget: getTarget,
      getRing: getRing,
    };
  }

  global.GraphLayoutCore = { createForceLayout: createForceLayout };
})(typeof self !== "undefined" ? self : window);
