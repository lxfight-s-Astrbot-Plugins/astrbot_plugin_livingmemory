# Gravity-driven 3D memory motion

Goal: replace prescribed phase-based circles with hierarchical central-gravity motion. Retain semantic parent assignment, spatial depth, terminal shells, stable focus pivots, pause/background behavior and batched rendering. PRs #357–#360 are merged into master.

Scope: visual, softened central-gravity approximation with normalized masses. Community anchors stay fixed; each node moves relative to its semantic parent. No all-pairs force evaluation, new dependencies, persistence/API changes, extra controls, explanatory UI copy, or runtime self-check/repair loops. Remove the superseded phase/speed/basis implementation and its constant-radius test.

## Stage 1: Gravity state and core behavior
**Goal**: preserve the existing type/relationship hierarchy; initialize spatial relative positions and coherent tangential velocities; integrate softened central acceleration using fixed-step velocity Verlet.
**Success Criteria**: curvature follows attraction, speed increases near the center, same-system angular momentum has a consistent direction, and trajectories stay within the interior/terminal radial budgets. Energy error stays bounded in a sustained motion test.
**Tests**: replace constant-radius behavior with varying radius/speed and shared-spin tests; retain parent/hold/depth/envelope cases; add frame-partition and branch-order cases.
**Project Structure Impact**: rename the existing orbital module/test to gravity, with the renderer consuming the same position buffer. No compatibility wrappers.
**Status**: Complete

The previous constant-circle model fails the varying-radius regression. Core cases cover bounded energy error over 600 simulated seconds, equivalent trajectories at 30/120 FPS, shared angular-momentum direction, phase-by-phase volume, and terminal-shell ordering. A smooth vertical initial-velocity field prevents temporary flattening without runtime impulses or noise. Following visual feedback, interior spacing is shared by remaining branch depth instead of decaying geometrically, while terminal paths move inward to a compact radius band. Two regression cases demonstrate the previous deep-branch crowding and excessive terminal radius.

## Stage 2: Renderer integration and lifecycle
**Goal**: integrate gravity state into the existing 30 FPS render loop, without per-edge force calculations or geometry rebuilds.
**Success Criteria**: selected/hovered ancestry remains stationary; release resumes saved velocities; hidden/inactive views stop simulation; long frame delays do not cause a physics catch-up stall.
**Tests**: sandbox interaction, pause/resume, visibility, reduced-motion opt-in, rotation/zoom and mobile resizing.
**Project Structure Impact**: existing renderer references and frame-time budget; no new UI.
**Status**: Complete

Sandbox checks pass for selected rotation/zoom, mobile resizing, pause/resume with saved velocities, page/tab suspension, reduced-motion opt-in, and a three-second stalled-frame case capped to 0.1 seconds of physics catch-up.

## Stage 3: Display-scale verification and delivery
**Goal**: verify gravity motion with sparse and dense graphs, document the approximation, and create a focused follow-up PR.
**Success Criteria**: 5,000 nodes / 100,000 edges retain three draw calls and shared indices; no scene flattening or branch/terminal inversion.
**Tests**: frontend tests, affected asset/documentation tests, dashboard/documentation builds, browser scale check and scene capture.
**Project Structure Impact**: bilingual WebUI guidance, synthetic screenshot, built dashboard asset and PR description.
**Status**: Complete

Validation: 136 frontend tests, 17 affected asset/documentation/full-snapshot tests, dashboard build and documentation build pass. The local Chrome 5,000-node/100,000-edge case renders every relation at approximately 26 FPS with three draw calls and unchanged edge indices; gravity takes about 0.52 ms per frame on average (0.9 ms at the 95th percentile). Sparse anonymous topology and dense synthetic scene checks pass. A 17-second browser capture demonstrates the compact layout and motion. VitePress retains its existing large-chunk advisory.

References: [REBOUND gravity softening](https://rebound.hanno-rein.de/simulation/variables/), [REBOUND leapfrog integration](https://rebound.hanno-rein.de/integrators/leapfrog/).

Rollback: revert the gravity follow-up and rebuild dashboard assets; the merged orbital and stellar features remain available.
