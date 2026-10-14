# V2 architecture audit — 2026-10-14

## Reproducible starting point

Baseline: `8a228ea` on `codex/source-cad-demo`, parent `b677930`. The worktree was clean. Prior conversation test counts and local changes are **not present** in this checkout and are not a baseline. Work continues on `refactor/agent-cad-v2`; commits remain local.

Read: AGENTS.md, .cursor/skills/starter.md, READMEs, technical design, dependency manifests, compose files, application entry points, CAD service/tool/routes, task/SSE infrastructure, authentication and frontend routes/viewers. Three independent audits covered backend, frontend and real kernel feasibility before implementation.

## Existing execution paths

* Workbench: `frontend/src/pages/CADPage.vue` → `/api/v1/cad/*` → `application/services/cad_service.py` → ezdxf. Prompts use plate-specific regular expressions and defaults. Documents are a mutable in-process dictionary; a batch can partially apply. Version counters do not retain history.
* Chat: session → AgentService → AgentDomainService → RedisStreamTask → AgentTaskRunner → AgentScopeFlow → CADToolkit. `cad_generate_dxf` uses CADService; `cad_generate_dxf_from_spec` independently writes DXF and does not create a document. Geometry validation checks parsability and drawing entities, not engineering intent.
* Frontend: Vue 3, Vite 4, TypeScript; DXF viewer is two-dimensional. Three.js is only transitive. Existing Axios authentication and SSE conventions can be reused. MainLayout has no dedicated CAD navigation. Static CAD routes must precede `/chat/:sessionId`.
* Backend: Python 3.12, FastAPI/Pydantic v2, AgentScope; Mongo uses PyMongo AsyncMongoClient (some older documentation says Motor). Startup requires Mongo and Redis; old chat also creates a browser sandbox. RedisStreamTask is an in-process asyncio task registry with Redis messages, not a durable CAD queue.

## Baseline measurements

| Check | Actual result |
|---|---|
| Legacy source CAD preview | `python examples/generate_cad_preview.py --output /tmp/cad-baseline --no-render`: seven DXF checks pass (units, outline, four holes, diameter, centers, rounded corners, audit) |
| Legacy annotation completeness | Only `120 mm` and `80 mm` text; hole annotation/complete dimension graphics absent |
| Original frontend `npm ci --ignore-scripts` | Failed EINTEGRITY: Huawei mirror returned invalid archive |
| Frontend temporary copy with only registry URL replacement | Same versions/integrities; 237 packages installed, type-check and production build passed (16.17 s) |
| Frontend bundle | Main 3777.42 kB / gzip 1020.55 kB; DXF 745.23 kB / gzip 187.77 kB; size warning |
| Full Docker stack | Not run: Docker unavailable in this environment |
| Existing infrastructure-dependent pytest suite | Not run: no running Mongo/Redis/backend/sandbox; no fabricated passing count |
| Existing 3D/STEP benchmark | Not available: baseline has no precise 3D backend; improvement percentage is N/A |

Independent feasibility probe (outside repository): Python 3.12.14, build123d **0.11.1**, OCP **7.9.3.1.1**. Real 80×50×8 plate with four Ø6 through holes: valid one-solid B-Rep, four cylindrical faces, correct bbox, volume 31095.22131576613 mm³; STEP reimport volume 31095.221315766117 mm³. STEP/STL/GLB exported. Modeling/export probe 0.1555 s excluding import. This is feasibility evidence, not an implemented V2 benchmark.

## Risks and design decisions

1. Retain `/api/v1/cad/*` and existing 2D documents unchanged; add `/api/v1/cad-v2/*` with typed IR and explicit immutable revisions.
2. Separate domain schema/errors/contracts, application workflow, kernel/storage/executor adapters and HTTP/UI. Importing CAD domain must not initialize model providers, Mongo, Redis or browser automation.
3. Use build123d/OpenCascade for exact solids; GLB/STL are derived previews/manufacturing meshes. Execute only a bounded trusted IR interpreter in a subprocess, never model-provided Python.
4. Persist jobs/revisions and use compare-and-swap publication. Reuse authentication, API envelope and SSE. Avoid a new external queue service. A standalone local mode is required for real reproducible tests in this environment; production integration remains explicitly distinguished.
5. Geometry success requires actual topology/measurements plus STEP reimport. Unknown or ambiguous verification is reported, never silently counted as passed.
6. Preserve failed edit history without replacing the last valid revision. Every download/preview names the revision. Repair is bounded, makes an explicit local patch and may not weaken user dimensions or validation expectations.
7. Fix the npm lock download source while retaining locked versions/integrity, then add direct viewer dependencies. Large existing bundle remains a measured baseline risk.

## Implementation and acceptance sequence

0. Audit and baseline (this commit).
1. Domain/backend boundaries and normalized errors; legacy compatibility regression.
2. Versioned typed CAD IR, safe expressions, dependencies, patches and static tests.
3. Real kernel adapter, semantic selectors, exports, isolated executor and integration tests.
4. Requirement/planning gateway, persistent jobs, bounded execution and authenticated API.
5. Measured geometry/STEP verification and bounded repair with real failure evidence.
6. Parameter/feature edits, immutable revisions, CAS, restore and failure preservation.
7. Three.js viewer, parameter/history panels, SSE and revision-bound downloads.
8. At least 20 basic + 10 medium real-kernel benchmark cases, with explicit metric denominators.
9. Deployment/CI/runbooks, complete regression and honest acceptance report.

Some mutually dependent modules may be developed concurrently, but commits and reported phase gates record actual tested scope. Acceptance scenarios A–E are evaluated against real artifacts, not screenshots or HTTP status alone.
