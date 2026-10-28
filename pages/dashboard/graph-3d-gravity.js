const TYPE_ORDER = { person: 0, topic: 1, summary: 2, fact: 3, other: 4 };
const PARENTS = { person: [], topic: ["person"], summary: ["topic", "person"], fact: ["summary", "topic", "person"], other: ["summary", "topic", "person"] };
const MASSES = { person: 70, topic: 25, summary: 10 };
const STEP = 1 / 120;

export class MemoryGravity {
  constructor(graph, layout) {
    const count = graph.nodes.length;
    this.base = layout.positions;
    this.hubs = layout.hubs;
    this.communityRadii = layout.communityRadii;
    this.parents = new Int32Array(count).fill(-1);
    this.offsets = new Float64Array(count * 3);
    this.velocities = new Float64Array(count * 3);
    this.accelerations = new Float64Array(count * 3);
    this.mu = new Float64Array(count);
    this.softeningSquared = new Float64Array(count);
    this._remainder = 0;
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
    const branchDepth = new Uint8Array(count);
    for (const parent of this.parents) if (parent >= 0) childCounts[parent]++;
    for (let index = this.order.length - 1; index >= this.hubs.length; index--) {
      const i = this.order[index], parent = this.parents[i];
      if (childCounts[i]) branchDepth[parent] = Math.max(branchDepth[parent], branchDepth[i] + 1);
    }
    for (const i of this.order) {
      const parent = this.parents[i];
      if (parent < 0) continue;
      const community = layout.communityIds[i], extent = this.communityRadii[community];
      // Bound apsides instead of clamping live positions: branches stay inside terminal paths.
      const maximum = childCounts[i] ? (extent * 0.22 - envelopes[parent]) / (branchDepth[i] + 1) : extent * 0.64;
      const minimum = childCounts[i] ? maximum * 0.6 : extent * 0.56;
      const rank = childRanks[parent]++;
      const spacing = minimum + (maximum - minimum) * Math.sqrt((rank + 0.5) / childCounts[parent]);
      const dx = this.base[i * 3] - this.base[parent * 3];
      const dy = this.base[i * 3 + 1] - this.base[parent * 3 + 1];
      const dz = this.base[i * 3 + 2] - this.base[parent * 3 + 2];
      const distance = Math.hypot(dx, dy, dz);
      const radius = Math.min(maximum, Math.max(minimum, distance * 0.6 + spacing * 0.4));
      envelopes[i] = envelopes[parent] + radius;
      const radial = [dx / distance, dy / distance, dz / distance];
      const longitude = community * 2.3999632297, inclination = community * 0.83;
      const spin = [Math.cos(longitude) * Math.sin(inclination), Math.sin(longitude) * Math.sin(inclination), Math.cos(inclination)];
      const tangent = [spin[1] * radial[2] - spin[2] * radial[1], spin[2] * radial[0] - spin[0] * radial[2], spin[0] * radial[1] - spin[1] * radial[0]];
      const tangentLength = Math.hypot(...tangent);
      for (let axis = 0; axis < 3; axis++) tangent[axis] /= tangentLength;
      const vertical = [radial[1] * tangent[2] - radial[2] * tangent[1], radial[2] * tangent[0] - radial[0] * tangent[2], radial[0] * tangent[1] - radial[1] * tangent[0]];
      // A smooth vertical velocity field preserves volume without reversing the shared spin.
      const pitch = 0.55 * Math.sin(2 * Math.atan2(dy, dx) + community * 0.719);
      const cosine = Math.cos(pitch), sine = Math.sin(pitch);
      const softened = extent * 0.18;
      const mass = roots.has(parent) ? 120 : MASSES[graph.nodes[parent].type];
      this.softeningSquared[i] = softened * softened;
      this.mu[i] = (2 * Math.PI / 60) ** 2 * (extent * extent * 0.36 + softened * softened) ** 1.5 * mass / 120;
      const periapsis = radius * (childCounts[i] ? 0.62 : 0.82);
      const apoDistance = Math.hypot(radius, softened), periDistance = Math.hypot(periapsis, softened);
      // Energy and angular momentum set the turning radii in the softened potential.
      const speed = Math.sqrt(2 * this.mu[i] * periapsis * periapsis /
        (apoDistance * periDistance * (apoDistance + periDistance)));
      const attraction = -this.mu[i] / (apoDistance * apoDistance * apoDistance);
      for (let axis = 0; axis < 3; axis++) {
        this.offsets[i * 3 + axis] = radial[axis] * radius;
        this.velocities[i * 3 + axis] = (tangent[axis] * cosine + vertical[axis] * sine) * speed;
        this.accelerations[i * 3 + axis] = radial[axis] * radius * attraction;
      }
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
    this._remainder += seconds;
    const steps = Math.floor(this._remainder / STEP);
    this._remainder -= steps * STEP;
    for (let step = 0; step < steps; step++) {
      for (let index = this.hubs.length; index < this.order.length; index++) {
        const i = this.order[index], offset = i * 3;
        if (this.held[i]) continue;
        for (let axis = 0; axis < 3; axis++) this.offsets[offset + axis] +=
          this.velocities[offset + axis] * STEP + this.accelerations[offset + axis] * STEP * STEP * 0.5;
        const x = this.offsets[offset], y = this.offsets[offset + 1], z = this.offsets[offset + 2];
        const squared = x * x + y * y + z * z + this.softeningSquared[i];
        const attraction = -this.mu[i] / (squared * Math.sqrt(squared));
        for (let axis = 0; axis < 3; axis++) {
          const acceleration = this.offsets[offset + axis] * attraction;
          this.velocities[offset + axis] += (this.accelerations[offset + axis] + acceleration) * STEP * 0.5;
          this.accelerations[offset + axis] = acceleration;
        }
      }
    }
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
      for (let axis = 0; axis < 3; axis++) positions[offset + axis] = positions[parent * 3 + axis] +
        this.offsets[offset + axis];
    }
  }
}
