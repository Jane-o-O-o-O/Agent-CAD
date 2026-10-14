# Migrating to Agent-CAD V2

V2 is additive: `/api/v1/cad/*` remains the 2D DXF interface, while `/api/v1/cad-v2/*` adds precise 3D jobs and immutable revisions. Existing 2D data is not rewritten. New CAD IR uses schema version `2.0`; there is no automatic DXF-to-3D or legacy-document conversion.

## Choose the deployment path

| Path | Use | Dependencies and identity |
|---|---|---|
| Integrated application: `app.main:app` | Existing users, sessions, legacy DXF and AgentScope chat plus V2 | Existing Mongo, Redis, auth and chat sandbox/browser; owner comes from existing authentication |
| Standalone CAD: `app.interfaces.cad_v2_app:app` | CAD workbench/API without the original service stack | Python/kernel dependencies, local persistent directory, one private bearer token representing one local user |

Both paths use the same V2 router, service, executor and revision store. They must **not** simultaneously supervise the same data directory. Run one API worker per V2 store. Do not use multiple replicas or SQLite on NFS. The standalone compose file is not a replacement deployment of all original services.

## Preserve existing work first

Record the current Git branch/commit and save uncommitted changes using the project's normal workflow. Keep exported DXF files and any existing application backups. The legacy `CADService` stores its drawing documents in process memory; V2 does not retroactively make those old documents durable. Export needed drawings before stopping that process.

For an existing V2 deployment, stop accepting new jobs and shut the service down gracefully before taking a whole-directory backup of `CAD_V2_DATA_DIR`. Keep the SQLite database and its auxiliary files together with all `artifacts/` files. A database-only backup cannot restore model downloads. Retain the service-visible absolute data-root path: current artifact records contain absolute paths. Do not point an old build at an untested newer store without preserving a restorable copy.

## Run the standalone implementation locally

Use Python 3.12 on Linux with Landlock ABI ≥3 and libseccomp2 for the default strict worker. A host that cannot enforce this policy rejects geometry jobs with `IsolationUnavailableError`; health only proves the HTTP process is alive. Trusted local development can explicitly set `CAD_V2_ISOLATION=process-dev`, whose reduced isolation is recorded in every execution report. From `backend`, install the standalone lock in a separate virtual environment:

```bash
uv venv .venv-cad-v2 --python 3.12
uv pip sync --python .venv-cad-v2/bin/python requirements-cad-v2.txt
```

Build the existing frontend from `frontend`:

```bash
npm ci
npm run type-check
npm run build
```

Start from `backend`, with a persistent directory and a private token in the environment:

```bash
export CAD_V2_DATA_DIR="$PWD/.cad-v2-data"
export CAD_V2_LOCAL_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv-cad-v2/bin/python -m uvicorn app.interfaces.cad_v2_app:app --host 127.0.0.1 --port 8000 --workers 1
```

Use a stable external data directory for deployment; the path above is a local development example. Keep the generated token private and retain it in your approved secret configuration if the service must survive shell restarts. The service rejects an absent token or one shorter than 16 characters. API requests require `Authorization: Bearer <token>`; public health/config endpoints do not grant access to CAD data. The built SPA is served when `frontend/dist` exists, or set `CAD_V2_FRONTEND_DIST` to its directory. Open `/chat/cad-v2` and log in with email `local@localhost` and the private `CAD_V2_LOCAL_TOKEN` as the password. The standalone server provides login, refresh and logout endpoints compatible with the existing frontend. Logout clears browser credentials; revoke the fixed token by rotating it in the service configuration. A copied token is not invalidated merely by logging out.

A 3D request can be tested through `POST /api/v1/cad-v2/jobs` with this body:

```json
{
  "prompt": "生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。",
  "request_id": "first-plate"
}
```

HTTP 202 means submitted. Follow the returned job using the status endpoint or SSE; only `completed` plus a published revision and a passed report means generated. The example uses the deterministic explicit plate parser and needs no model key. A different prompt outside that grammar may require a configured model or explicit CAD IR; it must not silently generate a similar plate.

## Container and CI files

`backend/Dockerfile.cad-v2` and `docker-compose.cad-v2.yml` describe the standalone CAD service and frontend. Configure the token and a persistent data volume before starting; retain the volume across updates. The service should be reachable only through the intended local/reverse-proxy boundary. The compose configuration and exact launch commands are maintained in the root README.

`.github/workflows/cad-v2.yml` defines the V2 regression/build gate. A workflow file being present is not evidence that GitHub Actions has run it. Docker was not available in the implementation environment, so container-image build and full Docker-stack startup remain explicit deployment checks. The full backend `uv sync --locked --no-dev` installation also succeeded, and integrated `app.main` OpenAPI generation confirmed 58 registered paths including 11 CAD V2 paths. Neither result proves Mongo/Redis startup or a working full service stack. Local Python/Node builds, real-kernel tests and GUI evidence are reported separately in the final report.

## Configuration changes

| Variable | Purpose / current default |
|---|---|
| `CAD_V2_DATA_DIR` | Persistent SQLite and artifacts; default `backend/.cad-v2-data` |
| `CAD_V2_LOCAL_TOKEN` | Standalone bearer secret, at least 16 characters; no usable default |
| `CAD_V2_FRONTEND_DIST` | Optional built SPA directory |
| `CAD_V2_MAX_CONCURRENCY` | Service geometry-job concurrency; default 2 |
| `CAD_V2_MAX_QUEUE` | Admitted task budget; default 32 |
| `CAD_V2_MAX_REPAIRS` | Service repair budget; default 3, hard maximum 5 attempts |
| `CAD_V2_TIMEOUT_SECONDS` | Geometry worker wall-time budget from the service; default 90 seconds |
| `CAD_V2_ISOLATION` | Default `strict`: requires Linux Landlock ABI ≥3 and libseccomp2 and fails closed when unavailable. Explicit `process-dev` is for trusted local development only, with no filesystem/network confinement. |
| `CAD_V2_PYTHON` | Optional compatible interpreter for the separate geometry worker |
| `CAD_V2_API_BASE` | OpenAI-compatible API base including `/v1` when the provider requires it; fallback `API_BASE` |
| `CAD_V2_API_KEY` | Model secret from environment; fallback `API_KEY`; not required for supplied IR or supported offline prompts |
| `CAD_V2_MODEL` / `CAD_V2_PLANNER_MODEL` / `CAD_V2_EDITOR_MODEL` | General/planner model; existing `MODEL_NAME` fallback, then `gpt-6.1-sol` |
| `CAD_V2_MODEL_TIMEOUT` / `CAD_V2_MODEL_RETRIES` | Per-model-call timeout (default 60 s) and extra retries (default 2; maximum 3) |

Keep secrets out of repository files, screenshots, logs and downloaded IR. The model gateway uses JSON-object mode plus mandatory local Pydantic validation, rather than assuming provider-side schema enforcement. One design has now completed the live `gpt-6.1-sol` service path after three model calls during development: the first exposed missing `pi` support, the second exposed unsupported final-output solid-count validation, and the third published all five revision artifacts. An independent checker bug after publication was corrected; the same STEP then passed 9/9 checks without rebuilding or calling the model again. The original failures, unchanged-IR replay and final recheck remain in `backend/benchmarks/cad_v2/live/01`–`05` JSON records; see [live evidence](architecture-v2.md#live-provider-spot-check). This is one design's spot check, not a generalized model success-rate measurement; paid calls are separate from deterministic CI. Network/provider failures are reported as infrastructure errors and are not “repaired” by changing geometry.

## Move callers to the new contract

1. Keep existing 2D clients on their current endpoints and DXF tools. For a new precise solid, submit a full 3D prompt or CAD IR to `POST /cad-v2/jobs`.
2. Store the stable job ID and document ID. Subscribe to `/jobs/{id}/events` or query `/jobs/{id}`; resume SSE using its event ID. A failed job contains diagnostics, not a usable new revision.
3. On completion, load `/documents/{id}/revisions/{revision}`. Use its parameter definitions and validation report; bind the selected GLB preview and every download to this revision ID.
4. Send edits with the exact current `expected_revision`. A conflict means reload the document; do not overwrite a newer revision by guessing its ID.
5. Restore an earlier revision through `/documents/{id}/restore`, supplying that historical `revision_id` and the current `expected_revision`. Restore rebuilds and creates a new revision, preserving history.

The integrated AgentScope flow now exposes `cad_v2_generate`, `cad_v2_job_status`, `cad_v2_edit` and `cad_v2_revision`. These use the authenticated session owner and the same service as HTTP. No tool lets model-supplied arguments select another owner. Use the returned `/chat/cad-v2?document=...&revision=...` link to inspect the exact result. Long-running legacy chat still depends on its original sandbox/browser setup; using standalone V2 does not require that stack.

## Edit geometry and design intent explicitly

The HTTP edit body supports numeric `parameters`, partial `feature_patches` (params, inputs, dependencies, label), `add_features`, `delete_features`, and optional `output_feature`. Changing the final feature must be explicit when the document already names an output. Adding an unused branch does not automatically select it for export.

A deliberate design change can also supply a partial `expected_geometry` object and a replacement `constraints` array. Unmentioned expected fields, such as outer dimensions, remain unchanged; supplied lists such as `holes` replace that list. Removing a feature requires removing or updating its references and affected expectations explicitly. This is an authorized edit to engineering intent, not an automatic failure repair.

For example, a previously verified solid plate gains a centred Ø6 through hole as follows (replace the revision ID):

```json
{
  "expected_revision": "current-revision-id",
  "add_features": [{
    "id": "bore",
    "type": "Hole",
    "inputs": ["plate"],
    "params": {"diameter": 6, "positions": [[40, 25, 0]], "through": true}
  }],
  "output_feature": "bore",
  "expected_geometry": {
    "volume": "80*50*8-3.141592653589793*3**2*8",
    "holes": [{"feature_id": "bore", "diameter": 6, "positions": [[40, 25, 0]], "through": true}]
  }
}
```

This example assumes an existing 80×50×8 mm Box feature `plate`, whose unchanged bbox remains constrained. The compiler still validates the full graph; the kernel validates the actual hole and new volume. Without the explicit new volume expectation, the old full-plate volume would correctly fail. The automatic repair policy cannot make that intent change for the user.

Natural-language editing supports complete offline grammars for multiple hole subset edits, moves, additions and deletions. More general wording, feature changes and assembly edits use the configured structured editor model and validated incremental patches. Unsupported or ambiguous intent produces a `RequirementError`; submit the answer with `clarification_job_id` and the unchanged base revision. See [API](api-v2.md) for the continuation contract. Model-generated patches do not bypass geometry validation. A typed `custom`, unsupported symmetry/distance or unsupported intermediate-feature constraint stays `unverified` and blocks publication. Dimension, volume and solid-count constraints may name the final `output_feature` explicitly or leave `feature_id` absent.

## Validation and format differences

STEP is the precise engineering solid. STL and GLB are tessellated derivatives. IR preserves parameters and expressions; Python is a trusted replay program requiring the project's installed CAD modules. Download formats from the same revision, not from a mixture of jobs. New revisions also include DXF/SVG three-view engineering drawings; assemblies include BOM JSON/CSV. Historical revisions retain their original artifact set.

Run a Python replay as `python model.py /path/to/output`. It rebuilds and validates the B-Rep before exporting; file existence alone is not validation. The worker also reimports its STEP and compares measured properties. Passing these geometric checks does not establish material, manufacturing-process or structural-strength certification.

All coordinates within one IR document use its declared unit. The compiler converts mm/cm/m/inch consistently. The offline creation parser rejects mixed-unit prompts; provide one unit or convert the request into IR. Parameter kinds do not supply a general dimensional-analysis engine for arbitrary expressions. Safe arithmetic includes the reserved built-in `pi`; no parameter may shadow it, and calls or attributes such as `pi()` or `math.pi` remain forbidden.

## Backup, rollback and remaining deployment gates

Keep V2 data volumes independent of the checkout and frontend build output. For backup, stop the supervisor cleanly and copy the complete data root to a new backup location, recording the application commit and absolute data path. For restore, use the recorded compatible application version and the same service-visible path. Do not remove data or overwrite the only backup during an upgrade.

To roll the application back, first preserve current changes and V2 data, stop the new service, and switch to the recorded tested Git branch or commit using your normal release process. For example, `git switch <tested-pre-v2-branch>` selects an existing rollback branch without rewriting history. An older 2D-only application will not read V2 revisions; keep their store and artifacts intact for a subsequent V2 deployment. Do not use a destructive reset or delete the persistent volume as a rollback mechanism.

Before production use, verify the target OS resource limits, persistent local disk permissions, chosen bearer/production auth flow, reverse-proxy SSE behavior, cancellation, service restart, backup restore and STEP downloads in that environment. The default strict worker adds Landlock filesystem enforcement and seccomp default-deny syscall rules to process/resource limits. This does not create a VM or filesystem/network namespaces. Unsupported hosts fail closed; the actual isolation mode is persisted. Multi-host scaling, SQLite/NFS, arbitrary geometry healing, general assembly solving and broad live-model reliability remain outside the validated scope. Three bounded healing strategies and scoped structured edits are implemented; see the [extension report](extension-report.md) for current evidence and the distinct blocked Docker/cloud gates.

See [architecture](architecture-v2.md), [IR specification](cad-ir-spec.md), [geometry validation](geometry-validation.md), [benchmark report](benchmark-report.md), and [final report](final-report.md) for exact implementation and evidence.

## Deployment acceptance gates

Run `bash scripts/smoke_cad_v2_docker.sh` on a Docker host exposing Landlock ABI ≥3 to build the CAD frontend/API, test actual denied operations, verify seven artifacts for each of three revisions, restart the API and verify all 21 downloads remain unchanged. Run `bash scripts/smoke_full_stack_docker.sh` separately for the original seven-service stack startup/API gate. It uses a new Compose project, loopback-only frontend, mock model and authentication bypass; it is an isolated test, not a production configuration or a chat/browser/Claw acceptance test. Both scripts preserve their volumes. CI runs strict tests, browser acceptance, and both container gates; no paid model call is part of CI.
