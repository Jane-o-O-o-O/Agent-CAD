# CAD V2 HTTP API

The exact generated request/response schemas are available at `/docs` and `/openapi.json` on the running backend. Implementation: `backend/app/interfaces/api/cad_v2_routes.py`. All paths below start with `/api/v1/cad-v2` and require the current user's credentials; model arguments cannot override ownership.

| Method and path | Purpose |
|---|---|
| `POST /jobs` | Submit exactly one nonempty `prompt` or `ir`; optional `name` and `request_id`; answer an initial-generation question with `prompt` plus `clarification_job_id`; HTTP 202 |
| `GET /jobs/{job_id}` | Durable job state, stage, error, report and published revision ID |
| `POST /jobs/{job_id}/cancel` | Cancel queued/running work; terminate the worker if active |
| `GET /jobs/{job_id}/events` | SSE `event: job` snapshots with persistent integer IDs; resume using `Last-Event-ID` or `after` |
| `GET /documents` | List the current owner's documents |
| `GET /documents/{document_id}` | Document and current revision |
| `GET /documents/{document_id}/revisions` | Immutable revision history |
| `GET /documents/{document_id}/revisions/{revision_id}` | Exact IR, parameter/feature/assembly diff, report and artifact manifest |
| `POST /documents/{document_id}/edits` | Rebuild parameter/feature/instance edits or a natural-language edit against `expected_revision`; HTTP 202 |
| `POST /documents/{document_id}/restore` | Rebuild `revision_id` into a new revision against `expected_revision`; HTTP 202 |
| `GET /documents/{document_id}/revisions/{revision_id}/artifacts/{format}` | Download a published `step`, `stl`, `glb`, `ir`, `python`, `dxf`, `svg`, `bom_json` or `bom_csv` artifact from that exact revision |

JSON success responses use `{ "code": 0, "msg": "success", "data": ... }`. A 202 response proves admission only. A `completed` job identifies a published, validated revision; `awaiting_input` asks for missing design information without building or publishing. `failed` or `cancelled` jobs never replace the last valid revision. SSE ends at all four of these states. Snapshot percentages describe stages, not predicted remaining time. Revision downloads return bytes, not the JSON envelope.

Natural-language creation extracts independent source-backed requirements before structured IR generation. Explicit local guards preserve supplied dimensions and requested features even if a model omits them. The plan gets at most one coverage replan for missing verifiable requirements; coverage gates run before execution and again against the final measured B-Rep report. A supplied IR runs compilation and kernel validation but does not acquire natural-language coverage evidence from metadata. Edits continue to use scoped `EditPlan` preservation checks, rather than independent creation requirement extraction.

## Create and edit

```json
{
  "prompt": "生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。",
  "name": "Mounting plate",
  "request_id": "plate-001"
}
```

After loading the published revision, submit:

```json
{
  "expected_revision": "the-current-revision-id",
  "parameters": {"hole_diameter": 8},
  "request_id": "plate-001-hole-8"
}
```

Parameter names come from that revision's IR; they are not universally fixed. HTTP `parameters` maps existing names to numeric values in the IR's document units. Feature changes support `feature_patches`, `add_features`, `delete_features`, `output_feature`, and explicit `expected_geometry`/`constraints` updates; see the [IR specification](cad-ir-spec.md). HTTP `feature_patches` is a list of objects containing `id` and partial `params`, `inputs`, `depends_on`, or `label`.

Supply `prompt` instead of explicit patches for a natural-language edit. The offline editor supports bounded compound requests on supported mounting plates: selecting hole subsets, changing diameter, moving or removing holes, and adding a central through-hole. Other requests use the configured structured editor model to propose an incremental `EditPlan`, including supported feature and assembly instance changes. Plans must identify requested targets, preserve unrelated geometry and constraints, and pass compilation and kernel validation. Missing design information returns a requirement question; missing model configuration returns an infrastructure failure. A prompt cannot be combined with explicit parameter, feature, instance, mate, or expectation patches.

## Assembly instance edits

Create an assembly using `POST /jobs` with an `ir` containing the optional `assembly` object described in the [IR specification](cad-ir-spec.md#assemblies). Parts reference solid features; instances reference parts and carry explicit transforms. Existing single-part documents retain the same create/edit flow.

`POST /documents/{document_id}/edits` accepts these additional fields:

| Field | JSON shape and semantics |
|---|---|
| `instance_patches` | Object keyed by existing instance ID; values may contain partial `transform`, `name`, or `part_id` |
| `add_instances` | List of new `{id, part_id, name?, transform?, editable?}` objects |
| `delete_instances` | List of editable instance IDs to remove |
| `mate_patches` | Object keyed by existing mate ID; values may contain `origin`, `reference_origin`, `axis`, `reference_axis`, `offset`, or partial `transform` |

For example, an axial shaft move in an existing assembly is:

```json
{
  "expected_revision": "the-current-revision-id",
  "instance_patches": {
    "shaft_1": {"transform": {"translation": [0, 0, -7]}}
  },
  "request_id": "shaft-axial-move-001"
}
```

`transform.translation` uses document units; `transform.rotation` is XYZ degrees. Each supplied vector has exactly three entries and may use the IR's parameter expressions. A partial transform preserves its other field. Stable IDs and editability cannot be patched. New instances must reference existing parts; deletion fails if retained mates reference that instance. The final IR allows at most 100 instances, even though the HTTP request list limit is 200.

The `fixed`, `aligned`, and `concentric` mates validate declared poses and local datums; they do not compute new placements. Moving a constrained instance may therefore require an explicitly intended `mate_patches` change or may fail validation. Exact volumetric collision checks run when the assembly's `collision_check` is enabled. The job remains unpublished if its geometry, placement, mate, or enabled collision checks fail. There is no endpoint that silently solves mates or fuses assembly components.

## Clarification and resubmission

An incomplete initial request or ambiguous edit finishes as `awaiting_input` with `error.code: "RequirementError"` and a question in `error.message`. Complete requirements proceed directly. For initial generation, submit the answer to `POST /jobs`:

```json
{
  "clarification_job_id": "the-awaiting-input-job-id",
  "prompt": "厚8mm，垂直孔距30mm",
  "request_id": "plate-answer-002"
}
```

This retains the original draft document; do not supply `ir` or `name` with an answer. For edits, submit the answer to the same edits endpoint with `clarification_job_id`:

```json
{
  "expected_revision": "the-unchanged-base-revision-id",
  "clarification_job_id": "the-awaiting-edit-job-id",
  "prompt": "只修改左侧两个孔，保持右侧孔的位置和直径不变。",
  "request_id": "hole-edit-clarification-002"
}
```

The referenced job must belong to the same owner, document, request kind and base revision, and contain a `RequirementError`. Legacy `failed` requirement jobs remain answerable. Original requirements and all question/answer turns persist across service restarts. A short answer retains earlier requirements; a remaining gap returns another `awaiting_input` job. Submit the next answer against that latest job ID. Context is bounded to 20 turns and 16000 characters. Each answer creates a new job; the previous snapshot remains immutable. One question admits one answer job, while retrying the same `request_id` and content returns that existing job. Stale answers, duplicate submissions with another request ID, geometry failures and configuration failures cannot be resumed as clarification. The workbench retains the waiting job in its URL so refreshing restores the question.

For example, a plate request containing both four and five holes first asks for the final hole count. Answering "四个孔" resolves that conflict while retaining supplied body sizes, bore diameter and placement. A bare `4` is accepted only when hole count is the unique missing field; grouped count-and-thickness questions require a labelled answer. If thickness and vertical pitch remain missing, answer them in successive requests against the latest waiting job. The next job remembers all previous answers, including across application restarts. A non-four final count requires structured planning, rather than silently generating the supported offline four-hole plate.

## Requirement evidence

For natural-language creation, a completed revision includes server-owned `report.requirements` and `report.requirement_coverage`. The requirement set retains the raw request, user answers, individual exact source quotes and extraction mode. Coverage records each expected value, actual evidence, source, participating feature IDs, verification stage and `passed/failed/unverified` status. Inspect the exact revision returned by `GET /documents/{document_id}/revisions/{revision_id}`; HTTP admission or IR metadata is not this evidence.

These fields are generated by the service. Uploaded `ir.metadata.requirements` and `ir.metadata.requirement_coverage` are stripped and cannot certify user intent. A failed coverage gate records `RequirementCoverageError` and diagnostic coverage in the job report, with no new revision. Restore reuses existing requirements from the trusted source revision report and checks the rebuilt model; a legacy or supplied-IR revision without a trusted requirement set has no fabricated coverage proof. Edit revisions use their current edit-plan protection and geometry reports.

Coverage has conservative limits: a Box labelled "rib" or a Cut labelled "slot" cannot prove requested Rib/Slot geometry or function. A requested unsupported operation, ambiguous feature binding or unavailable verifier can remain `unverified` even when a solid is buildable. Such jobs do not publish. Native typed sweep, loft and thread construction still requires measurable supported parameters and kernel validation; arbitrary manufacturing fitness is outside the guarantee.

## Revision-bound drawing and BOM downloads

Use the existing artifact route with the format name; these are manifest-backed downloads, not separate on-demand drawing or BOM generation endpoints:

```text
GET /api/v1/cad-v2/documents/{document_id}/revisions/{revision_id}/artifacts/dxf
GET /api/v1/cad-v2/documents/{document_id}/revisions/{revision_id}/artifacts/svg
GET /api/v1/cad-v2/documents/{document_id}/revisions/{revision_id}/artifacts/bom_json
GET /api/v1/cad-v2/documents/{document_id}/revisions/{revision_id}/artifacts/bom_csv
```

| Format | Worker filename | Content and media type |
|---|---|---|
| `dxf` | `drawing.dxf` | Front/top/right B-Rep projections with visible/hidden/center layers and measured dimensions; `image/vnd.dxf` |
| `svg` | `drawing.svg` | Viewable drawing from the same projection/measurement data; `image/svg+xml` |
| `bom_json` | `assembly.json` | Assembly identity, BOM and per-component transforms/geometry; `application/json` |
| `bom_csv` | `bom.csv` | Part ID/name/material/quantity/instance IDs/unit volume; `text/csv` |

The worker creates DXF/SVG from the validated final shape in millimetres, using third-angle views. Coverage is overall dimensions and supported visible axis-aligned complete cylindrical hole diameters/positions; it does not infer manufacturing fits or tolerances. Drawing evidence is recorded in `report.geometry.drawing`. BOM files are produced for assemblies only; assembly properties and quantities also appear under `report.geometry.assembly`. Inspect the revision's `artifacts` manifest for availability, including older revisions that lack these formats. Missing artifacts return 404.

Analytic shapes use `method=exact_brep_hlr` and supported native analytic/NURBS DXF curves. Shapes containing BSpline/Bezier faces use `method=occt_polygonal_hlr`, `projection_mode=bounded_tessellation`, `dxf_geometry=polygonal_segments` and `approximation_tolerance_mm=0.01`; the fresh final-B-Rep mesh uses 0.15 rad angular deflection and at most 100000 triangles. DXF/SVG display a visible projection note. These projected outlines are approximate, while supported dimensions remain exact final-B-Rep measurements. Drawing budget or meshing failure blocks the job rather than exporting an undocumented approximation.

STEP/GLB assemblies preserve component hierarchy and colors. GLB component node names are stable instance IDs and include display metadata; the preview is metre/Y-up. Drawing and BOM downloads use the same authentication and ownership checks as STEP. Responses include the artifact hash as `ETag`, `X-CAD-Revision`, `Cache-Control: private, no-cache`, and a download filename. Restore rebuilds the selected IR and produces a new validated revision and its artifacts.

## Revision and request safety

Stale edits receive a conflict rather than overwriting newer work. Equivalent request IDs deduplicate within user/operation scope; reusing an ID for different content conflicts. Ownership filters hide other users' resources. CAD JSON is size-limited before parsing. Validation errors and infrastructure failures retain structured diagnostics without exporting a false successful revision.

## Local authentication

Standalone mode uses `POST /api/v1/auth/login` with email `local@localhost` and the configured private `CAD_V2_LOCAL_TOKEN` as password. Use the returned access token in the Authorization bearer header. Never include it in a URL. The frontend performs this login and supplies authenticated fetches for SSE and downloads. `/auth/me`, `/auth/status`, `/auth/refresh`, `/auth/logout` retain the frontend's envelope contract.

This local mode represents one user. Logout clears client state; rotate the environment token and restart to revoke credentials. Integrated mode uses the existing application's user authentication. See [migration and deployment boundaries](migration-guide.md).
