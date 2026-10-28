# Dynamic memory orbits

Goal: add continuously moving, type-aware orbital systems on top of the merged community layout and theme fixes. Community representatives remain fixed; topics orbit associated people; summaries orbit topics; relationship/preference facts prefer people, while other facts prefer summaries/topics. Use existing relation weights to choose parents and retain all original graph edges.

Scope: frontend animation only. No new backend fields, persistent memory changes, dependencies, runtime validation gates or automatic fallbacks. One small pause/resume control is added; no headings or explanatory UI copy.

## Stage 1: Orbital hierarchy and math
**Goal**: deterministic, cycle-free parent assignment and nested orbits with bounded community extents.
**Success Criteria**: typed relationships select the expected parent, satellites move with their parent, and freezing a target also freezes its ancestry without phase jumps.
**Tests**: parent preferences, strongest ties, community boundaries, constant orbital radius, nested motion and selective holds.
**Project Structure Impact**: one pure `graph-3d-orbits.js` module; Worker emits compact community IDs, representatives and radii.
**Status**: Complete

## Stage 2: Rendering and interaction
**Goal**: visible orbital animation at a controlled frame rate using the existing shared position buffer.
**Success Criteria**: indexed edges follow moving nodes; focus and hover remain usable; pause/resume, reduced motion, hidden pages and destruction stop unnecessary work.
**Tests**: real sandbox browser checks for motion, focus, holds, pause/resume, visibility, mode switches and unchanged edge buffers.
**Project Structure Impact**: existing renderer, Graph UI, controls, styles and translations. Animation math and scheduling stay separate; no additional abstraction layer.
**Status**: Complete

## Stage 3: Scale verification and delivery
**Goal**: document orbit rules and verify 5,000-node/100,000-edge animation.
**Success Criteria**: no per-frame edge rebuilding or force simulation, no animation jump on resume, and previous theme behavior remains readable.
**Tests**: frontend suite, dashboard/doc checks and browser performance/interaction checks.
**Project Structure Impact**: bilingual WebUI instructions and rebuilt assets; create a separate PR for the new motion behavior.
**Status**: Complete

Validation: 124 frontend tests and 16 dashboard/documentation tests passed. The sandbox browser verifies default movement, stationary camera, nested holds, pause/resume without catch-up, hidden pages, reduced-motion opt-in and unchanged edge-index buffers under a 5,000-node/100,000-edge workload. Stellar glow, importance-driven pulsars and corrected camera pivots are implemented in a separate follow-up PR.

Rollback: revert this PR and rebuild dashboard assets. Merged community layout and theme fixes remain available; no data migration is involved.
