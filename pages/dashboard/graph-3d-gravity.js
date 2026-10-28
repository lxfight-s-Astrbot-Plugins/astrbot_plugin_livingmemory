const MASSES = { person: 70, topic: 25, summary: 10 };
const STEP = 1 / 120;

export class MemoryGravity {
  constructor(graph, layout) {
    const count = graph.nodes.length;
    this.base = layout.positions;
    this.hubs = layout.hubs;
    this.communityIds = layout.communityIds;
    this.communityRadii = layout.communityRadii;
    this.frames = layout.frames;
    this.parents = layout.parents;
    this.order = layout.order;
    this.offsets = new Float64Array(count * 3);
    this.rest = new Float64Array(count * 3);
    this.displacements = new Float64Array(count * 3);
    this.velocities = new Float64Array(count * 3);
    this.accelerations = new Float64Array(count * 3);
    this.mu = new Float64Array(count);
    this.softeningSquared = new Float64Array(count);
    this.held = new Uint8Array(count);
    this.heldCommunities = new Uint8Array(this.hubs.length);
    this.ticks = new Float64Array(this.hubs.length);
    this.angles = new Float64Array(this.hubs.length);
    this.cosines = new Float64Array(this.hubs.length).fill(1);
    this.sines = new Float64Array(this.hubs.length);
    this.center = new Float64Array(3);
    this._remainder = 0;
    this.movingCount = count - this.hubs.length;
    const roots = new Set(this.hubs), depths = new Uint8Array(count);
    for (let i = 0; i < count; i++) {
      const hub = this.hubs[this.communityIds[i]];
      for (let axis = 0; axis < 3; axis++) this.center[axis] += this.base[hub * 3 + axis] / count;
    }
    for (const i of this.order) {
      const parent = this.parents[i];
      if (parent < 0) continue;
      depths[i] = depths[parent] + 1;
      const community = this.communityIds[i], extent = this.communityRadii[community], frame = community * 9;
      // Gravity moves a small displacement around the semantic rest shape, preserving the arms.
      const radius = extent * 0.009 / (1 + depths[i] * 0.2), softened = radius * 0.6;
      const mass = roots.has(parent) ? 120 : MASSES[graph.nodes[parent].type];
      this.softeningSquared[i] = softened * softened;
      this.mu[i] = (2 * Math.PI / 45) ** 2 * (radius * radius + softened * softened) ** 1.5 * mass / 120;
      const periapsis = radius * 0.7;
      const apoDistance = Math.hypot(radius, softened), periDistance = Math.hypot(periapsis, softened);
      const speed = Math.sqrt(2 * this.mu[i] * periapsis * periapsis /
        (apoDistance * periDistance * (apoDistance + periDistance)));
      const phase = i * 2.3999632297, cosine = Math.cos(phase), sine = Math.sin(phase);
      const attraction = -this.mu[i] / (apoDistance * apoDistance * apoDistance);
      for (let axis = 0; axis < 3; axis++) {
        const radial = this.frames[frame + axis] * cosine + this.frames[frame + 3 + axis] * sine;
        const tangent = -this.frames[frame + axis] * sine + this.frames[frame + 3 + axis] * cosine;
        this.rest[i * 3 + axis] = this.offsets[i * 3 + axis] = radial * radius;
        this.velocities[i * 3 + axis] = tangent * speed;
        this.accelerations[i * 3 + axis] = radial * radius * attraction;
      }
    }
  }

  setHeld(indices) {
    this.held.fill(0);
    this.heldCommunities.fill(0);
    this.movingCount = this.parents.length - this.hubs.length;
    for (let index of indices) {
      if (index >= 0) this.heldCommunities[this.communityIds[index]] = 1;
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
    for (let community = 0; community < this.hubs.length; community++) {
      if (!this.heldCommunities[community]) this.ticks[community] += steps;
      const speed = 0.013 + 0.002 / (1 + this.communityRadii[community] / 300);
      const angle = this.angles[community] = this.ticks[community] * STEP * speed;
      this.cosines[community] = Math.cos(angle);
      this.sines[community] = Math.sin(angle);
    }
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
      const parent = this.parents[i], offset = i * 3;
      if (parent < 0) {
        positions[offset] = this.base[offset];
        positions[offset + 1] = this.base[offset + 1];
        positions[offset + 2] = this.base[offset + 2];
        continue;
      }
      if (this.held[i]) continue;
      const community = this.communityIds[i], center = this.hubs[community] * 3, frame = community * 9;
      for (let axis = 0; axis < 3; axis++) this.displacements[offset + axis] =
        this.displacements[parent * 3 + axis] + this.offsets[offset + axis] - this.rest[offset + axis];
      const x = this.base[offset] - this.base[center] + this.displacements[offset];
      const y = this.base[offset + 1] - this.base[center + 1] + this.displacements[offset + 1];
      const z = this.base[offset + 2] - this.base[center + 2] + this.displacements[offset + 2];
      const ax = this.frames[frame + 6], ay = this.frames[frame + 7], az = this.frames[frame + 8];
      const dot = ax * x + ay * y + az * z, cosine = this.cosines[community], sine = this.sines[community];
      positions[offset] = this.base[center] + x * cosine + (ay*z-az*y) * sine + ax * dot * (1-cosine);
      positions[offset + 1] = this.base[center + 1] + y * cosine + (az*x-ax*z) * sine + ay * dot * (1-cosine);
      positions[offset + 2] = this.base[center + 2] + z * cosine + (ax*y-ay*x) * sine + az * dot * (1-cosine);
    }
  }
}
