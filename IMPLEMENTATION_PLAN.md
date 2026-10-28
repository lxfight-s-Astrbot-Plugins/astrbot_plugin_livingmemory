# Community-aware 3D graph layout

Goal: reuse the existing topology grouping to form separated 3D communities, place representative nodes near each community center, and distinguish node types through size as well as color. Keep layout computation in the existing Blob Worker and retain complete graph data and memory sources.

Scope: this PR changes layout and node presentation. Theme conflicts are handled in an independent PR. No new dependencies, backend routes, storage fields, validation gates or automatic fallbacks.

## Stage 1: Share topology grouping
**Goal**: extract the existing pure topology calculation without changing 2D behavior.
**Success Criteria**: both layout workers use the same implementation; existing 2D layout tests pass.
**Tests**: existing 2D layout/interaction tests and deterministic community fixtures.
**Project Structure Impact**: one shared `graph-topology.js`; update classic-worker and bundle inputs.
**Status**: Complete

## Stage 2: Community layout and node hierarchy
**Goal**: separate community centers, arrange local systems around representative nodes, and apply type-dependent sizes.
**Success Criteria**: connected communities remain separated despite bridge links; peripheral and disconnected nodes remain visible; dense bridges do not dominate the default overview.
**Tests**: clustered graphs with hubs, bridges, isolated nodes and parallel relations; relative type size and deterministic 3D positions.
**Project Structure Impact**: existing 3D core, renderer and worker; typed layout results also carry community representatives and overview indices.
**Status**: Complete

## Stage 3: Visual and scale checks
**Goal**: verify anonymized local graph topology and dense multi-community fixtures in the AstrBot sandbox.
**Success Criteria**: community structure and node hierarchy are visible; camera focus, complete relations and idle rendering continue to work.
**Tests**: frontend suite, dashboard artifact check, browser interactions and 5,000-node/100,000-edge workload.
**Project Structure Impact**: update bilingual WebUI documentation and an anonymized/synthetic screenshot; never publish real labels or memory text.
**Status**: Complete

Validation: frontend tests and dashboard/documentation checks pass. Browser comparisons use a read-only export of the available local graph (68 nodes/68 edges), with labels, relations and memory IDs anonymized, plus a 1,200-node hub-heavy fixture. The sandbox scale check covers 5,000 nodes/100,000 edges, focus/reset, complete relations, touch input and idle rendering. No original memory text or labels are included in this PR.

Rollback: revert this PR and rebuild dashboard assets; there are no data migrations.
