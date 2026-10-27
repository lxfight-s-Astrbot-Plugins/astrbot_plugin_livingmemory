(function(global) {
  "use strict";

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
      : Math.min(12, Math.max(2, Math.round(Math.sqrt(nodes.length / 6))));
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

  global.GraphTopology = { buildTopologySeed: buildTopologySeed };
})(typeof self !== "undefined" ? self : typeof window !== "undefined" ? window : globalThis);
