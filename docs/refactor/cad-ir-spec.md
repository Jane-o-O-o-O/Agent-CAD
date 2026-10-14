# CAD IR 2.0 specification

Implementation: `backend/app/domain/cad_v2/{models,expressions,compiler,errors,contracts}.py`.
The IR records design intent; it contains no Python expressions with calls, imported modules, file paths, or executable modeling code. Pydantic models reject unknown fields. Public schema is available from `CADDocumentIR.model_json_schema()`.

## Document and parameters

A document contains `schema_version: "2.0"`, `document_id`, `units` (`mm`, `cm`, `m`, `inch`), `coordinate_system: "right-handed-Z-up"`, `parameters`, ordered `features`, `output_feature`, `dependencies`, `constraints`, `expected_geometry`, and application metadata. The final feature is the output when `output_feature` is absent. Output must be a solid, not an unextruded sketch.

Parameters are named ASCII identifiers and have `kind` (`length`, `angle`, `integer`, `scalar`), exactly one `value` or `expression`, `editable`, optional `label`, `min`, and `max`. Angles are degrees. Length values and literal geometric coordinates use the document unit. Expressions calculate in document units; the compiler normalizes geometry, expected dimensions, coordinates, tolerances, and length parameters to millimetres once. Volume expectations use the cube of the length conversion factor. Dimensionless values and angles are unchanged. Parameter kinds drive unit normalization and integer validation; this version does **not** implement general dimensional algebra for expressions, so planners must not mix angular and linear parameters.

Supported arithmetic is `+`, `-`, `*`, `/`, `**`, unary signs and parentheses with numeric literals and parameter names. There are no calls, attributes, indexing, comprehensions or Python execution. Limits: 512 characters, 64 AST nodes, finite values bounded by 10¹², exponent magnitude at most 8, dependency depth at most 64. Missing references, cycles, invalid arithmetic and violated parameter bounds produce typed failures.

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

## Semantic selection

Selectors use `kind: edge|face`, optional `geometry_type` (the kernel geometry name), `parallel` direction, `center`, `near` point, or centre-coordinate bounds `min` / `max`, plus `tolerance` and `expected_count` (default 1). Fillet and chamfer require edge selectors. A selector must match exactly the expected count. No edge/face indices exist in the schema. Ambiguous or empty results fail with `InvalidTopologyError`, never silently select the first edge. The kernel may report a narrowly bounded tolerance repair; it cannot alter the required radius or dimensions.

## Expectations and constraints

`expected_geometry` supports:

- `bbox`: three positive dimensions, including parameter expressions.
- `solids`: expected positive integer, default 1.
- `volume`, `volume_min`, `volume_max`: positive volume expectations.
- `holes`: groups containing `diameter`, `positions`, `direction`, `through`, optional `depth` and `feature_id`.
- `tolerance`: absolute linear tolerance in document units, default 0.0001.

Constraints have `id`, `type` (`dimension`, `volume`, `solid_count`, `hole`, `symmetry`, `distance`, `custom`), optional `feature_id`, `expected`, `axis`, `tolerance`, `description`. Typed constraint declarations do not themselves prove geometry correctness. The geometry validator returns measured evidence or explicitly `unverified` for unsupported checks. Feature `validation_conditions` are likewise declarations, not a success signal. Refer to `geometry-validation.md` for actual verification coverage.

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

`CompiledIR` contains numeric normalized `features`, `resolved_parameters`, `expected_geometry`, `constraints`, output ID and original `source_ir`. Replay Python is a fixed trusted program plus a JSON literal; injected metadata remains inert data. Replay needs the installed project and its CAD dependencies.

`apply_edits` copies the document, applies parameter edits, partial feature params, additions or deletions, and compiles before returning. It respects editability and cannot change protected feature fields (`id`, `type`, verification expectations). It never removes dangling references or weakens requirements automatically. Geometry execution and immutable revision publication happen in the application service; failed edits do not mutate the supplied document.

## Error taxonomy and tests

All public failures subclass `CADError` and expose `code`, `message`, `feature_id`, `details`, `recoverable`. Codes include RequirementError, SchemaValidationError, ParameterError, DependencyError, GeometryKernelError, BooleanOperationError, InvalidTopologyError, ConstraintViolationError, ExportError, ExecutionTimeoutError, InfrastructureError. Static failures are separate from kernel and infrastructure failures.

Run `python -m pytest tests/cad_v2/test_ir.py -q` from `backend`. Domain tests exercise schema rejection, malicious arithmetic, non-finite values, unit conversion, graph validation, resource limits, typed errors, replay literal safety and atomic edits. Real kernel acceptance belongs to integration and benchmark tests; passing this suite alone does not prove a solid or engineering file is correct.
