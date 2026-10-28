const TYPE_ORDER = { person: 0, topic: 1, summary: 2, fact: 3, other: 4 };
const PARENTS = { person: [], topic: ["person"], summary: ["topic", "person"], fact: ["summary", "topic", "person"], other: ["summary", "topic", "person"] };
const PERIODS = { person: 240, topic: 110, summary: 75, fact: 36, other: 90 };

export class MemoryOrbits {
  constructor(graph, layout) {
    const count = graph.nodes.length;
    this.base = layout.positions;
    this.hubs = layout.hubs;
    this.communityRadii = layout.communityRadii;
    this.parents = new Int32Array(count).fill(-1);
    this.radii = new Float32Array(count);
    this.phases = new Float64Array(count);
    this.speeds = new Float32Array(count);
    this.basis = new Float32Array(count * 6);
    this.held = new Uint8Array(count);
    this.center = new Float64Array(3);
    this.movingCount = count - this.hubs.length;
    const roots = new Set(this.hubs);
    for (let i = 0; i < count; i++) {
      const hub = this.hubs[layout.communityIds[i]];
      for (let axis = 0; axis < 3; axis++) this.center[axis] += this.base[hub * 3 + axis] / count;
      if (roots.has(i)) continue;
      const node = graph.nodes[i], community = layout.communityIds[i];
      const atomType = node.metadata?.atom_type;
      const preferences = node.type === "fact" && (atomType === "relational" || atomType === "preference")
        ? ["person", "summary", "topic"] : (PARENTS[node.type] || PARENTS.other);
      let parent = this.hubs[community], bestRank = Infinity, bestWeight = -Infinity;
      for (const edgeIndex of graph.adjacency[i]) {
        const link = graph.links[edgeIndex];
        const other = link.source === i ? link.target : link.source;
        if (other === i || layout.communityIds[other] !== community) continue;
        const rank = preferences.indexOf(graph.nodes[other].type);
        if (rank < 0) continue;
        if (rank < bestRank || (rank === bestRank && link.weight > bestWeight)) {
          parent = other; bestRank = rank; bestWeight = link.weight;
        }
      }
      this.parents[i] = parent;
    }
    this.order = [...this.hubs, ...graph.nodes.map((_, i) => i).filter(i => !roots.has(i)).sort((a, b) =>
      (TYPE_ORDER[graph.nodes[a].type] ?? TYPE_ORDER.other) - (TYPE_ORDER[graph.nodes[b].type] ?? TYPE_ORDER.other) || a - b)];
    const childCounts = new Uint32Array(count), childRanks = new Uint32Array(count), envelopes = new Float32Array(count);
    for (const parent of this.parents) if (parent >= 0) childCounts[parent]++;
    for (const i of this.order) {
      const parent = this.parents[i];
      if (parent < 0) continue;
      const node = graph.nodes[i];
      const community = layout.communityIds[i], extent = this.communityRadii[community];
      // Branch envelopes stay within 0.25R; leaf orbits stay outside them at every phase.
      const maximum = childCounts[i] ? (extent * 0.25 - envelopes[parent]) * 0.8 : extent * 0.7;
      const minimum = childCounts[i] ? maximum * 0.45 : extent * 0.55;
      const rank = childRanks[parent]++;
      const spacing = minimum + (maximum - minimum) * Math.sqrt((rank + 0.5) / childCounts[parent]);
      const dx = this.base[i * 3] - this.base[parent * 3];
      const dy = this.base[i * 3 + 1] - this.base[parent * 3 + 1];
      const dz = this.base[i * 3 + 2] - this.base[parent * 3 + 2];
      const radius = Math.min(maximum, Math.max(minimum, Math.hypot(dx, dy, dz) * 0.6 + spacing * 0.4));
      this.radii[i] = radius;
      envelopes[i] = envelopes[parent] + radius;
      const rotation = community * 0.719 + parent * 0.37 + i * 0.719;
      const tilt = (((i * 0.61803398875 + community * 0.17) % 1) - 0.5) * Math.PI;
      const cr = Math.cos(rotation), sr = Math.sin(rotation), ct = Math.cos(tilt), st = Math.sin(tilt);
      const u = [cr, sr, 0], v = [-sr * ct, cr * ct, st];
      for (let axis = 0; axis < 3; axis++) {
        this.basis[i * 6 + axis] = u[axis] * radius;
        this.basis[i * 6 + 3 + axis] = v[axis] * radius;
      }
      this.phases[i] = Math.atan2(dx * v[0] + dy * v[1] + dz * v[2], dx * u[0] + dy * u[1]);
      this.speeds[i] = Math.PI * 2 / (PERIODS[node.type] || PERIODS.other) / (0.75 + 0.5 * radius / maximum);
    }
  }

  setHeld(indices) {
    this.held.fill(0);
    this.movingCount = this.parents.length - this.hubs.length;
    for (let index of indices) {
      while (index >= 0 && !this.held[index]) {
        this.held[index] = 1;
        if (this.parents[index] >= 0) this.movingCount--;
        index = this.parents[index];
      }
    }
  }

  advance(seconds, positions) {
    for (const i of this.order) {
      const parent = this.parents[i];
      const offset = i * 3;
      if (parent < 0) {
        positions[offset] = this.base[offset];
        positions[offset + 1] = this.base[offset + 1];
        positions[offset + 2] = this.base[offset + 2];
        continue;
      }
      if (this.held[i]) continue;
      this.phases[i] += this.speeds[i] * seconds;
      const cosine = Math.cos(this.phases[i]), sine = Math.sin(this.phases[i]);
      for (let axis = 0; axis < 3; axis++) positions[offset + axis] = positions[parent * 3 + axis] +
        this.basis[i * 6 + axis] * cosine + this.basis[i * 6 + 3 + axis] * sine;
    }
  }
}
