# Stellar presentation and camera pivots

Goal: extend the orbital motion in PR #357 with representative-star halos, importance-driven pulsars, and correct rotation centers. Keep the actual controls pivot separate from screen composition, and preserve the pause/background behavior.

Scope: no new dependencies or memory persistence changes. Standardize the existing full-snapshot memory-to-node association so content nodes can use source-memory importance. Do not infer importance from graph degree or add explanatory UI copy.

## Stage 1: Source associations and value cues
**Goal**: full graph memories retain their node associations; high-importance fact/summary nodes receive a pulsar score.
**Success Criteria**: person/topic hubs do not become pulsars merely due to high degree; content without source evidence is not assigned a value.
**Tests**: scoped full-graph node associations and source importance/type cases.
**Project Structure Impact**: existing graph snapshot query and 3D preparation.
**Status**: Complete

## Stage 2: Stellar shading and camera geometry
**Goal**: batch halos and pulsar emission in the existing point shader, use community-weighted overview centers, and focus OrbitControls on actual selected coordinates.
**Success Criteria**: rotating around a focused target keeps it stationary on screen; projection offsets make room for details; reset/restore retains camera composition.
**Tests**: weighted community center, sandbox projection/pivot checks, interrupted focus and resize behavior.
**Project Structure Impact**: existing orbital metadata, renderer and camera methods; no extra draw objects per node.
**Status**: Complete

## Stage 3: Integrated verification
**Goal**: verify orbital motion, effects, controls and theme readability together at display scale.
**Success Criteria**: pause and background stop updates; position/index buffers remain shared; no per-edge geometry rebuilds.
**Tests**: frontend and scoped backend suites, dashboard/doc builds, sandbox interaction and 5,000-node/100,000-edge checks.
**Project Structure Impact**: bilingual instructions, screenshots and bundled assets; a follow-up PR based on #357.
**Status**: Complete

Validation: 126 frontend tests and 124 related backend/resource/documentation tests passed. The sandbox regression reproduced the previous 67-unit focus-pivot error; corrected focus now matches actual node coordinates and stays fixed on screen through rotation, zoom and viewport changes. Halo/pulsar attributes, shader compilation, pause/resume and a 5,000-node/100,000-edge workload pass. Spatial volume and line brightness are isolated in the next follow-up PR.

Rollback: revert this PR and rebuild frontend assets. Orbital motion in #357 and the previously merged community/theme fixes remain available.

## Follow-up: spatial volume and line readability
**Goal**: preserve grouping while replacing planar community centers and narrow orbital inclinations with genuinely spatial distributions. Brighten overview/focus relations and account for full-graph density.
**Success Criteria**: local layouts and animated systems retain visible depth; representative centers span 3D; complete relations and selected pivots remain correct.
**Tests**: local depth, multi-community depth/volume, orbital swept extents, sandbox rotation and scale checks.
**Project Structure Impact**: existing 3D core, orbital math, relation material and documentation; no new dependencies or API fields.
**Status**: Complete

Validation: 129 frontend tests and 17 affected asset/documentation/full-snapshot tests passed. Three regression cases fail with the previous layout and pass with the spatial distribution, including a non-coplanar representative check. Browser checks pass for motion, selected rotation/zoom, mobile resizing, theme contrast, and the opaque-origin sandbox. A local 5,000-node/100,000-edge run renders all relations at approximately 28 FPS with three draw calls and unchanged index buffers. Dashboard and documentation builds pass; VitePress reports its existing large-chunk advisory.

Rollback: revert this spatial/line follow-up and rebuild dashboard assets; the preceding orbital and camera changes remain available.
