# CAD IR 2.0 specification

Implementation: `backend/app/domain/cad_v2/{models,expressions,compiler,errors,contracts}.py`.
The IR records design intent; it contains no Python expressions with calls, imported modules, file paths, or executable modeling code. Pydantic models reject unknown fields. Public schema is available from `CADDocumentIR.model_json_schema()`.

## Document and parameters

A document contains `schema_version: "2.0"`, `document_id`, `units` (`mm`, `cm`, `m`, `inch`), `coordinate_system: "right-handed-Z-up"`, `parameters`, ordered `features`, `output_feature`, `dependencies`, `constraints`, `expected_geometry`, optional `assembly`, and application metadata. For a single-part document, the final feature is the output when `output_feature` is absent. Output must be a solid, not an unextruded sketch. An assembly uses its explicit part definitions and instances as the exported output; the feature graph and its references remain validated.

Parameters are named ASCII identifiers and have `kind` (`length`, `angle`, `integer`, `scalar`), exactly one `value` or `expression`, `editable`, optional `label`, `min`, and `max`. Angles are degrees. Length values and literal geometric coordinates use the document unit. Expressions calculate in document units; the compiler normalizes geometry, expected dimensions, coordinates, tolerances, and length parameters to millimetres once. Volume expectations use the cube of the length conversion factor. Dimensionless values and angles are unchanged. Parameter kinds drive unit normalization and integer validation; this version does **not** implement general dimensional algebra for expressions, so planners must not mix angular and linear parameters.

Supported arithmetic is `+`, `-`, `*`, `/`, `**`, unary signs and parentheses with numeric literals, parameter names, and the built-in constant `pi` (`math.pi`). The name `pi` is reserved: it cannot be defined as a parameter and is excluded from the parameter dependency graph. Expressions such as `pi*radius**2*height` are supported; `math.pi` and `pi()` are not. There are no calls, attributes, indexing, comprehensions or Python execution. Limits: 512 characters, 64 AST nodes, finite values bounded by 10¹², exponent magnitude at most 8, dependency depth at most 64. Missing references, cycles, invalid arithmetic and violated parameter bounds produce typed failures.

## Features

Every feature has a stable `id`, discriminating PascalCase `type`, typed `params`, ordered `inputs`, additional `depends_on`, `editable`, optional `label`, and `validation_conditions`. IDs are public ASCII names of at most 64 characters. Inputs and all explicit dependency edges must refer to earlier features. Duplicate IDs, unknown references, cycles and incompatible input shapes are rejected before loading a geometry kernel. Maximum document size is 500 features and 500 parameters.

| Type | Inputs | Parameters and meaning |
|---|---|---|
| Box | none | `width` X, `depth` Y, `height` Z; `origin` minimum XYZ corner |
| Cylinder | none | `radius`, `height`; `origin` base centre, cylinder axis +Z |
| Sphere | none | `radius`, `center` |
| Rectangle | none | `width`, `height`, `origin`, `plane` XY/XZ/YZ; minimum local XY corner |
| Circle | none | `radius`, `origin`, `plane`; origin is centre |
| Polyline | none | closed 2D `points` (3–1000), `origin`, `plane`; `closed: true` |
| BSpline | none | periodic closed interpolating 2D `points` (3–100 distinct points), `origin`, `plane`; `closed: true`, no repeated closing point |
| Path3D | none | open 3D `points` (2–100), `interpolation` `polyline` or `bspline`; optional BSpline endpoint `tangents` pair of nonzero directions |
| Sweep | profile, path | `is_frenet` (default false); one closed section, with path start inside it and section plane normal parallel to start tangent |
| Loft | 2–12 distinct ordered profiles | `ruled` (default false); one closed section per station, on distinct parallel planes ordered in one axial direction |
| HelicalThread | body | `major_diameter`, `minor_diameter`, `pitch`, `length`, `profile_angle` (default 60 degrees), `axis_origin`, `axis_direction`, `internal`, `left_hand` |
| Extrude | sketch | nonzero `amount` along sketch normal; negative allowed |
| Revolve | sketch | `angle` (0,360], `axis_origin`, `axis_direction` |
| Hole | body | positive `diameter`, `positions` (1–500), `direction`, `through`; positive `depth` required for blind holes |
| Pocket | body, sketch | positive `depth`, `direction` |
| Fillet | body | positive `radius`, semantic `selector` |
| Chamfer | body | positive `length`, semantic `selector` |
| Union / Cut / Intersection | at least two distinct bodies | empty params |
| LinearPattern | shape | integer `count` 1–100, `spacing` vector, `fuse` (default false) |
| CircularPattern | shape | integer `count` 1–100, `angle` (0,360], `axis_origin`, `axis_direction`, `fuse` |
| Transform | shape | XYZ `translation`, XYZ degree `rotation` about world axes in that order |

Patterns include the original as instance zero. Circular instances use angles `i × angle / count`; a full circle omits the duplicate 360° copy. Nested patterns have a maximum estimated 1000 instances. Unfused patterns may produce multiple solids; declare the expected solid count explicitly. Direction vectors must be nonzero and are normalized. Hole positions are axis points; a through-hole cutter traverses the body's full projected extent. Blind holes start at their supplied position and proceed in the specified direction.

`BSpline` interpolates its supplied points using the pinned build123d/OpenCascade spline algorithm. Smooth lofts between these sections create bounded freeform solid surfaces. This is not arbitrary freeform surface fitting: control-point/weight/knot editing, open surface bodies, and intersecting sections are outside this contract. Sweep and Loft profiles have no inner wires; create bores using subsequent supported cuts. The backend rejects misplaced sweep sections rather than guessing a new pose. BSpline path tangents are endpoint directions, not lengths, and are normalized without length-unit scaling. Loft stations may have lateral offsets; station order is measured along the first section's normal.

`HelicalThread` physically sweeps and cuts a single-start cylindrical V groove; it is not a cosmetic annotation or an ISO thread certification. Positive minor diameter must be below major diameter. `length/pitch` is between 1 and 64. The included profile angle is between 10 and 120 degrees, and `(major_diameter-minor_diameter)*tan(profile_angle/2)` must be below 95 percent of pitch. The requested interval must have a complete coaxial cylindrical wall: major diameter for external threads, or an existing minor-diameter bore for internal threads. The backend verifies available stock, actual material removal, one connected valid solid, and valley/crest material probes. Final-output and STEP-reimported solids repeat those material probes; downstream thread transforms/patterns currently produce `unverified` without a final-frame verifier. Tapered, multi-start, rounded-root, toleranced standard and arbitrary non-cylindrical threads are not represented by this feature. Their dimensions and geometry must not be silently substituted with this V-groove contract.

Sweep/Loft/thread outputs remain exact B-Rep solids and retain the existing STEP reimport acceptance thresholds. For solids with BSpline/Bezier faces, engineering drawing contours use OpenCascade polygonal hidden-line removal on a fresh final-shape tessellation, with a 0.01 mm deflection target, 0.15 rad angular target and 100,000-triangle limit. Their DXF/SVG curves are polygonal approximations, recorded as `method: occt_polygonal_hlr`, `projection_mode: bounded_tessellation` and `approximation_tolerance_mm: 0.01`; dimensions still measure the precise final B-Rep. Solids with analytical faces retain exact HLR and native analytical/NURBS DXF curves. This drawing approximation does not relax solid validation or STEP roundtrip checks.

## Assemblies

`assembly` is an additive IR 2.0 object. Omitting it, or setting it to `null`, preserves the single-part workflow. Its implementation is in `backend/app/infrastructure/cad_v2/assembly.py`.

| Field | Definition |
|---|---|
| `id`, `name` | Assembly identifier and display name; defaults `"assembly"`, `"Assembly"` |
| `parts` | 1–100 reusable part definitions |
| `instances` | 1–100 independently placed component occurrences |
| `mates` | Up to 300 explicit placement requirements; defaults to an empty list |
| `collision_check` | Whether to measure every pair's volumetric intersection; default `true` |
| `collision_tolerance` | Maximum permitted intersection volume, in document units cubed; default `1e-7`, allowed range 0–1; normalized to mm³ |

A part requires `id`, `name`, and `feature_id`. The referenced feature must produce exactly one positive-volume solid; a multi-solid pattern cannot be used as one assembly part. Optional fields are `color` (four RGBA channels in [0,1], default `[0.35,0.55,0.8,1]`), `material` (descriptive text), and local-frame `expected_geometry` using the existing expectation schema with `solids: 1`.

An instance requires stable `id` and `part_id`, with optional `name`, `editable` (default `true`), and `transform`. A transform has `translation` and `rotation`, each a three-element vector defaulting to `[0,0,0]`. Coordinates and angles may use parameter expressions. Rotation applies about world X, then Y, then Z, in degrees; translation then places the component in document units. Multiple instances can reference the same part definition. Part IDs, instance IDs and mate IDs must each be unique within their own collection; missing references and self-referencing mates are rejected.

Assembly construction keeps separately named, located solids without fusing them. Global `expected_geometry` applies to the assembly envelope and aggregate material volume; each part's expectations apply in its own local frame. When the global solid count is omitted, it becomes the number of instances before document serialization. An explicitly supplied count is checked. Component placements are independently checked against their declared transforms, and part feature checks run for each instance.

### Placement requirements

Mates **verify explicit instance poses; they do not solve or change those poses**. Datums are declared local origins and axes, not automatically selected faces or edges. Every mate requires `id`, `type`, and `instance_id`. Optional `reference_instance_id` selects another component's frame; omission means the world frame.

| Type | Measured requirement and relevant fields |
|---|---|
| `fixed` | Complete relative pose equals `transform`, whose translation and rotation both default to zero |
| `aligned` | Directed `axis` and `reference_axis` align, and the signed separation of `origin` from `reference_origin`, projected onto the reference axis, equals `offset`; lateral movement and rotation about that axis are not constrained |
| `concentric` | Datum axis lines coincide; parallel and antiparallel directions are accepted; movement and rotation along/about the common axis remain unconstrained |

`origin` and `reference_origin` default to `[0,0,0]`; `axis` and `reference_axis` default to `[0,0,1]` and must be nonzero; `offset` defaults to zero. Origins, offsets and transform translations use document units and support arithmetic expressions. `tolerance` defaults to `1e-4` document units and `angular_tolerance` defaults to `1e-4` degrees; both must be greater than zero and at most 1. The supported types do not provide a general constraint solver, moving joints, or a minimum-clearance requirement.

When `collision_check` is enabled, exact B-Rep Boolean intersection measures the volume shared by each pair of world-placed solids. Touching faces pass when their intersection has no positive volume beyond the tolerance. Collision and mate failures block publication. Setting `collision_check: false` explicitly disables that check; the validation report records whether it ran.

For example, given existing solid features `flange`, `shaft`, and `spacer`, each modeled in its own local frame, the assembly object can be:

```json
{
  "id": "drive",
  "name": "Flange, shaft and spacer",
  "parts": [
    {"id": "flange_part", "name": "Flange", "feature_id": "flange", "color": [0.2, 0.4, 0.8, 1]},
    {"id": "shaft_part", "name": "Shaft", "feature_id": "shaft", "material": "Steel"},
    {"id": "spacer_part", "name": "Spacer", "feature_id": "spacer", "material": "Brass"}
  ],
  "instances": [
    {"id": "flange_1", "part_id": "flange_part"},
    {"id": "shaft_1", "part_id": "shaft_part", "transform": {"translation": [0, 0, -5]}},
    {"id": "spacer_1", "part_id": "spacer_part", "transform": {"translation": [0, 0, 5]}}
  ],
  "mates": [
    {"id": "ground", "type": "fixed", "instance_id": "flange_1"},
    {"id": "shaft_axis", "type": "concentric", "instance_id": "shaft_1", "reference_instance_id": "flange_1"},
    {"id": "spacer_face", "type": "aligned", "instance_id": "spacer_1", "reference_instance_id": "flange_1", "reference_origin": [0, 0, 5]}
  ]
}
```

This fragment declares placement requirements; compatible part dimensions and bore clearances must still be supplied in `features` and checked by execution.

### Assembly exports

STEP and GLB retain component hierarchy, separate solids/meshes, colors and stable instance IDs as component node names. GLB component `extras` also contain `instance_id`, `part_id`, and `display_name`; coordinates use metres and Y-up, converted from the source millimetre/Z-up geometry. Export checks STEP aggregate and per-component mass/bounds/solid count and placement after reimport, and checks GLB component identities and node poses. STL contains the placed mesh without assembly identity or color metadata.

Assemblies additionally produce `assembly.json` (`bom_json`) and `bom.csv` (`bom_csv`). JSON includes `id`, `name`, `component_count`, `bom`, and measured `components`. Each BOM row contains `part_id`, `name`, `feature_id`, `material`, `color`, `quantity`, `instance_ids`, and `unit_volume_mm3`; an unused definition has quantity zero and no measured unit volume. CSV contains `part_id`, `name`, `material`, `quantity`, semicolon-separated `instance_ids`, and `unit_volume_mm3`. These are revision artifacts, available through the [artifact API](api-v2.md).

## Semantic selection

Selectors use `kind: edge|face`, optional `geometry_type` (the kernel geometry name), `parallel` direction, `center`, `near` point, or centre-coordinate bounds `min` / `max`, plus `tolerance` and `expected_count` (default 1). Fillet and chamfer require edge selectors. A selector must match exactly the expected count. No edge/face indices exist in the schema. Ambiguous or empty results fail with `InvalidTopologyError`, never silently select the first edge. The kernel may report a narrowly bounded tolerance repair; it cannot alter the required radius or dimensions.

## Expectations and constraints

`expected_geometry` supports:

- `bbox`: three positive dimensions, including parameter expressions.
- `solids`: expected positive integer, default 1 for a single part, or the instance count for an assembly when omitted.
- `volume`, `volume_min`, `volume_max`: positive volume expectations.
- `holes`: groups containing `diameter`, `positions`, `direction`, `through`, optional `depth` and `feature_id`. A nonempty list is the complete expected final hole-profile inventory, not a subset. The validator counts unique inward full cylindrical profiles by axis and radius; complex counterbores may involve several profiles and need an explicit inventory.
- `tolerance`: absolute linear tolerance in document units, default 0.0001.

Constraints have `id`, `type` (`dimension`, `volume`, `solid_count`, `hole`, `symmetry`, `distance`, `custom`), optional `feature_id`, `expected`, `axis`, `tolerance`, `description`. A `hole` constraint requires the same typed hole payload as `expected_geometry.holes` and uses the same expression and unit normalization; blind holes require positive depth. Typed constraint declarations do not themselves prove geometry correctness. The geometry validator returns measured evidence or explicitly `unverified` for unsupported checks. Final-output `dimension`, `volume`, and `solid_count` constraints are measured when `feature_id` is omitted or equals `output_feature`; arbitrary intermediate-feature checks remain unsupported. Feature `validation_conditions` currently produce `unverified` checks for reachable features, so they block publication rather than serving as a success signal. Refer to `geometry-validation.md` for actual verification coverage.

Keep editable dimensions and their expectations linked to the same parameter expression. Changing a hole diameter should change the geometric feature and the corresponding expectation, while retaining board dimensions and centres. Literal expectations stay fixed when a feature is directly patched: a conflicting edit must fail geometry validation rather than change the expected answer.

## Example: mounting plate

```json
{
  "schema_version": "2.0",
  "document_id": "plate-example",
  "units": "mm",
  "parameters": {
    "width": {"kind": "length", "value": 80},
    "depth": {"kind": "length", "value": 50},
    "thickness": {"kind": "length", "value": 8},
    "hole_diameter": {"kind": "length", "value": 6},
    "pitch_x": {"kind": "length", "value": 60},
    "pitch_y": {"kind": "length", "value": 30}
  },
  "features": [
    {"id": "plate", "type": "Box", "params": {
      "width": "width", "depth": "depth", "height": "thickness"
    }},
    {"id": "mounting_holes", "type": "Hole", "inputs": ["plate"], "params": {
      "diameter": "hole_diameter", "through": true,
      "positions": [
        ["(width-pitch_x)/2", "(depth-pitch_y)/2", 0],
        ["(width+pitch_x)/2", "(depth-pitch_y)/2", 0],
        ["(width-pitch_x)/2", "(depth+pitch_y)/2", 0],
        ["(width+pitch_x)/2", "(depth+pitch_y)/2", 0]
      ]
    }}
  ],
  "output_feature": "mounting_holes",
  "expected_geometry": {
    "bbox": ["width", "depth", "thickness"],
    "solids": 1,
    "holes": [{
      "feature_id": "mounting_holes", "diameter": "hole_diameter", "through": true,
      "positions": [
        ["(width-pitch_x)/2", "(depth-pitch_y)/2", 0],
        ["(width+pitch_x)/2", "(depth-pitch_y)/2", 0],
        ["(width-pitch_x)/2", "(depth+pitch_y)/2", 0],
        ["(width+pitch_x)/2", "(depth+pitch_y)/2", 0]
      ]
    }]
  }
}
```

## Compiler and edit API

```python
from app.domain.cad_v2.compiler import compile_ir, compile_to_python, apply_edits

compiled = compile_ir(ir_dict)
script = compile_to_python(ir_dict)
edited = apply_edits(ir_dict, parameters={"hole_diameter": 8})
```

`CompiledIR` contains numeric normalized `features`, `resolved_parameters`, `expected_geometry`, `constraints`, optional normalized `assembly`, output ID and source document `source_ir`. Replay Python is a fixed trusted program plus a JSON literal; injected metadata remains inert data. Replay needs the installed project and its CAD dependencies. Run `python model.py /path/to/output`; it calls `backend.validate` for the rebuilt part or assembly before export and refuses export when any constraint fails or remains unverified. The worker additionally creates DXF/SVG drawings; the replay script calls the backend's STEP/STL/GLB and assembly BOM exporter.

`apply_edits` copies the document, applies parameter edits, partial feature params, additions or deletions, an optional explicit `output_feature` switch, and compiles before returning. It respects editability and cannot change protected feature fields (`id`, `type`). For an explicit user design change, `expected_geometry` merges only supplied top-level fields while `constraints` replaces the declared list; lists such as `holes` are replaced whole. Unspecified expectations remain unchanged. These intent changes belong to the edit API, never automatic repair. It never removes dangling references or weakens requirements automatically. Geometry execution and immutable revision publication happen in the application service; failed edits do not mutate the supplied document.

Assembly edits add the following keyword arguments, all requiring an existing assembly:

| Argument | Allowed changes |
|---|---|
| `instance_patches` | Map from instance ID to partial `transform`, `name`, or `part_id`; ID and editability are protected |
| `add_instances` | List of complete new instance objects referring to existing part definitions |
| `delete_instances` | List of editable instance IDs; a remaining mate referencing a deleted instance causes rejection |
| `mate_patches` | Map from mate ID to partial `origin`, `reference_origin`, `axis`, `reference_axis`, `offset`, or `transform`; ID, type, references and tolerances are protected |

Transform objects merge by field: supplying only `translation` preserves the existing rotation, but a supplied three-element vector replaces that whole vector. When instances are added/deleted and the old global `solids` equals the old instance count, the compiler updates that count unless the edit explicitly supplies `expected_geometry.solids`; other expectations remain unchanged. There is no part-definition or mate add/delete operation in this incremental API.

```python
edited = apply_edits(
    assembly_ir,
    instance_patches={"shaft_1": {"transform": {"translation": [0, 0, -7]}}},
    add_instances=[{"id": "spacer_2", "part_id": "spacer_part",
                    "transform": {"translation": [0, 0, 10]}}],
)
```

Changing placement does not silently update fixed or aligned mate requirements. An explicitly intended mate change must be provided in `mate_patches`; all resulting placements and collisions still require kernel validation. The structured natural-language editor uses a more restricted instance patch (transform/name only), associates edits with quoted intent spans and instance/mate IDs, and requests clarification for ambiguous or conflicting intent. See the [HTTP API](api-v2.md) for clarification resubmission.

## Error taxonomy and tests

CAD domain failures subclass `CADError` and expose `code`, `message`, `feature_id`, `details`, `recoverable`. Codes include RequirementError, RequirementCoverageError, SchemaValidationError, ParameterError, DependencyError, GeometryKernelError, BooleanOperationError, InvalidTopologyError, ConstraintViolationError, ExportError, ExecutionTimeoutError, InfrastructureError. RequirementCoverageError means the generated plan or measured result failed or could not verify source-backed requirements; it does not publish a revision. Static failures are separate from kernel and infrastructure failures. Application-level ownership lookups, revision conflicts and cancellation are additionally normalized by the service/API; `StoreNotFound`, `StoreConflict` and cancellation status are not all `CADError` subclasses.

Run `python -m pytest tests/cad_v2/test_ir.py -q` from `backend`. Domain tests exercise schema rejection, malicious arithmetic, non-finite values, unit conversion, graph validation, resource limits, typed errors, replay literal safety and atomic edits. Real kernel acceptance belongs to integration and benchmark tests; passing this suite alone does not prove a solid or engineering file is correct.
