# Galaxy structure and presentation

Goal: show a galaxy group at overview scale, thick spiral systems for larger communities and compact stellar clusters for smaller ones. Keep semantic relationships, branch/periphery ordering, stable focus, reduced motion, and large-graph performance. Starting point is released 2.8.1.

Scope: use existing NetworkX, d3-force-3d and Three.js. No dependencies, migrations, new UI headings, controls or explanatory copy. No all-pairs runtime gravity, checksum/state gates, silent fallbacks, or reconstruction of all edge geometry during animation. Keep 2D styling unchanged.

## Stage 1: Weighted communities and topology-aware centers
**Goal**: attach deterministic weighted Louvain community IDs to scoped dashboard snapshots and position their centers using aggregated real relations.
**Success Criteria**: dense groups stay together, isolates form a compact separate cluster, relation strength affects community proximity, and no source nodes/edges/memory links are lost.
**Tests**: weighted graph partitions, isolates, scope/API results, linked 3D centers and existing graph tests.
**Project Structure Impact**: graph handler computes community IDs in a thread; 3D core/worker consume IDs. Remove the 3D dependency on 2D hub/BFS seeds. First focused PR.
**Status**: Complete

Validation: 139 frontend tests and 94 scoped backend/documentation tests pass; affected Ruff checks and dashboard/documentation builds pass. The sandbox regression retains focus/zoom, pause/background handling and reduced-motion opt-in. A 5,000-node/100,000-edge run reaches approximately 26 FPS with three draw calls and unchanged edge indices. Zero-weight evidence remains visible without adding grouping/layout attraction.

## Stage 2: Continuous spatial hierarchy and galaxy forms
**Goal**: distribute semantic branches across continuous radii, with thick spiral arms for large communities and ellipsoidal clusters for small ones.
**Success Criteria**: no narrow terminal shell, no crowding from geometric depth shrinkage, branch parents stay inside their own terminal extent, and all systems retain spatial volume.
**Tests**: semantic parents, branch intervals, radial coverage, thickness, representative centers, sparse/dense layouts.
**Project Structure Impact**: 3D core owns one hierarchy calculation; worker returns hierarchy and galaxy frames. Remove the old spherical/type radius seeds.
**Status**: Complete

Semantic branches now share leaf intervals within each spiral arm, giving each arm continuous radial coverage. Small/edgeless groups use spatial clusters. The worker supplies the one semantic hierarchy and orthonormal community frames; gravity no longer rebuilds or overwrites the layout.

## Stage 3: Shape-preserving gravity motion
**Goal**: combine slow coherent system rotation with bounded local gravitational movement around semantic rest positions; retain world coordinates shared by drawing, picking and focus.
**Success Criteria**: arms remain recognizable, focus holds stay stationary, pause resumes saved state, and frame rate/visibility do not change the trajectory unexpectedly.
**Tests**: fixed-step state, bounded displacement/volume, semantic holds, shared spin, rotation/zoom and lifecycle browser checks.
**Project Structure Impact**: replace the old full-radius parent orbits in MemoryGravity; no legacy mode or compatibility wrapper. Stages 2 and 3 form the second focused PR.
**Status**: Complete

Validation: 140 frontend tests and 16 asset/documentation tests pass. New regressions cover exact layout preservation on animation startup, stable branch lengths, continuous terminal radii and bounded motion. The sandbox retains focus pivots and lifecycle behavior; a 5,000-node/100,000-edge run reaches approximately 27 FPS with three draw calls and unchanged indices. Documentation/dashboard builds pass. Ordinary motion combines slow system rotation and small energy-bounded gravitational displacements; hovered/focused systems stop bulk rotation without losing state.

## Stage 4: Relation levels, star density and lighting
**Goal**: use a small set of bundled community links in overview, reveal internal structure on approach/selection, and add GPU star dust following galaxy frames with restrained node glow.
**Success Criteria**: preserved relations are clear, full-relations mode keeps every edge, stars never count as memories or affect picking, and no new UI text is added.
**Tests**: relation aggregation/completeness, zoom levels, shader compilation, bounds and pause/background handling.
**Project Structure Impact**: renderer, worker relation metadata and galaxy dust shader; reuse the existing 2D aggregation idea without copying its visual style. Third focused PR.
**Status**: Complete

Overview uses bounded aggregated community curves, proximity reveals a per-community structural range, and hover/selection shows direct real ties. Full mode retains every relation in one shared index buffer. GPU dust follows the same held system phases and never participates in counts or picking. Ordinary glows are restrained and each high-value source selects one representative content pulsar. Real-data checks exposed foreground systems stealing detail/picking; LOD now follows the controls target and picking uses nearest projected node centers.

## Stage 5: Integrated validation and delivery
**Goal**: verify the final scene against the same anonymous and synthetic datasets, update bilingual docs/screenshots and create stacked PRs.
**Success Criteria**: 5,000 nodes / 100,000 edges remain responsive, relation indices stay shared during motion, and mobile/sandbox behavior remains correct.
**Tests**: relevant backend/frontend suites, scoped Ruff, dashboard/doc builds, opaque-origin browser interactions and actual motion capture.
**Project Structure Impact**: existing tests, docs and built assets; no release or merge until requested.
**Status**: Complete

Validation: 1,161 backend tests and 147 frontend tests pass. Dashboard and documentation builds pass; all eight appearance/theme combinations retain detail contrast. The actual local 10,000-node/200,000-edge fixture passes overview/near levels, real mouse picking, focus, complete relations and shared-index checks at approximately 25 FPS with four draw calls. Screenshots and a 16-second native motion capture use only scoped synthetic records. VitePress retains its existing large-chunk advisory; the six backend warnings are existing warnings in unrelated tests.

## Stage 6: Requested large local visual fixture
**Goal**: append 10,000 nodes, 200,000 relations and 1,200 readable synthetic memories to the requested local plugin data directory, in a dedicated display scope.
**Success Criteria**: existing records remain intact; the fixture has varied weighted communities, semantic types, isolates, weak bridges and high-value content.
**Tests**: real-store integration with existing records and archived details; render the resulting scoped snapshot through the actual community/payload functions.
**Project Structure Impact**: standalone developer seed script and usage docs, separate from presentation changes. Data stays local and is never committed. Documents are archived and no embedding provider or vector-file writes are used.
**Status**: Complete

The local write completed once in session `livingmemory:visual:galaxy`. Production snapshot/community code reads exactly 10,000 nodes, 200,000 relations and 1,200 memories, partitioned into 12 thematic communities plus one isolate group. Browser checks cover near/overview relation levels, real mouse picking, focus, complete-edge rendering and pause. The local complete-mode run reaches approximately 25 FPS with four draw calls and unchanged indices.

Rollback: revert the relevant PRs and rebuild dashboard assets, or return to the 2.8.1 tag. Community IDs are display metadata and do not modify stored memories.
