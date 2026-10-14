# CAD V2 HTTP API

The exact generated request/response schemas are available at `/docs` and `/openapi.json` on the running backend. Implementation: `backend/app/interfaces/api/cad_v2_routes.py`. All paths below start with `/api/v1/cad-v2` and require the current user's credentials; model arguments cannot override ownership.

| Method and path | Purpose |
|---|---|
| `POST /jobs` | Submit exactly one nonempty `prompt` or `ir`; optional `name` and `request_id`; HTTP 202 |
| `GET /jobs/{job_id}` | Durable job state, stage, error, report and published revision ID |
| `POST /jobs/{job_id}/cancel` | Cancel queued/running work; terminate the worker if active |
| `GET /jobs/{job_id}/events` | SSE `event: job` snapshots with persistent integer IDs; resume using `Last-Event-ID` or `after` |
| `GET /documents` | List the current owner's documents |
| `GET /documents/{document_id}` | Document and current revision |
| `GET /documents/{document_id}/revisions` | Immutable revision history |
| `GET /documents/{document_id}/revisions/{revision_id}` | Exact IR, parameter/feature diff, report and artifact manifest |
| `POST /documents/{document_id}/edits` | Rebuild parameter/feature edits against `expected_revision`; HTTP 202 |
| `POST /documents/{document_id}/restore` | Rebuild `revision_id` into a new revision against `expected_revision`; HTTP 202 |
| `GET /documents/{document_id}/revisions/{revision_id}/artifacts/{format}` | Download `step`, `stl`, `glb`, `ir` or `python` from that exact revision |

JSON success responses use `{ "code": 0, "msg": "success", "data": ... }`. A 202 response proves admission only. A `completed` job identifies a published, validated revision; `failed` or `cancelled` jobs never replace the last valid revision. SSE snapshot percentages describe stages, not predicted remaining time. Revision downloads return bytes, not the JSON envelope.

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

Parameter names come from that revision's IR; they are not universally fixed. Numeric edits use the IR's document units. A supported explicit natural-language diameter edit can supply `prompt` instead. Feature changes support `feature_patches`, `add_features`, `delete_features`, `output_feature`, and explicit `expected_geometry`/`constraints` updates; see the [migration guide](migration-guide.md). General natural-language feature patching is not yet implemented.

Stale edits receive a conflict rather than overwriting newer work. Equivalent request IDs deduplicate within user/operation scope; reusing an ID for different content conflicts. Ownership filters hide other users' resources. CAD JSON is size-limited before parsing. Validation errors and infrastructure failures retain structured diagnostics without exporting a false successful revision.

## Local authentication

Standalone mode uses `POST /api/v1/auth/login` with email `local@localhost` and the configured private `CAD_V2_LOCAL_TOKEN` as password. Use the returned access token in the Authorization bearer header. Never include it in a URL. The frontend performs this login and supplies authenticated fetches for SSE and downloads. `/auth/me`, `/auth/status`, `/auth/refresh`, `/auth/logout` retain the frontend's envelope contract.

This local mode represents one user. Logout clears client state; rotate the environment token and restart to revoke credentials. Integrated mode uses the existing application's user authentication. See [migration and deployment boundaries](migration-guide.md).
