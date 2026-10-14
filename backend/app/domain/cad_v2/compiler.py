"""Deterministically normalize bounded CAD IR into trusted kernel operations."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any

from pydantic import Field, ValidationError

from .errors import DependencyError, ParameterError, SchemaValidationError
from .expressions import evaluate_expression, resolve_parameters
from .models import CADDocumentIR, StrictModel

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4}
SKETCHES = {"Rectangle", "Circle", "Polyline"}
PRIMITIVES = {"Box", "Cylinder", "Sphere", *SKETCHES}
BOOLEANS = {"Union", "Cut", "Intersection"}


class CompiledFeature(StrictModel):
    id: str
    type: str
    inputs: list[str]
    depends_on: list[str]
    params: dict[str, Any]
    validation_conditions: list[dict[str, Any]] = Field(default_factory=list)


class CompiledIR(StrictModel):
    schema_version: str = "2.0"
    document_id: str
    units: str = "mm"
    features: list[CompiledFeature]
    output_feature: str
    resolved_parameters: dict[str, float]
    expected_geometry: dict[str, Any]
    constraints: list[dict[str, Any]]
    source_ir: dict[str, Any]
    assembly: dict[str, Any] | None = None


def validate_ir(ir: CADDocumentIR | dict[str, Any]) -> CADDocumentIR:
    try:
        return CADDocumentIR.model_validate(ir)
    except ValidationError as exc:
        # Do not echo arbitrary request values into public diagnostics.
        errors = [{"location": list(error["loc"]), "type": error["type"]}
                  for error in exc.errors(include_url=False, include_input=False)]
        raise SchemaValidationError("CAD IR schema is invalid", details={"errors": errors}) from exc


def _validate_dependencies(ir: CADDocumentIR) -> None:
    by_id = {feature.id: feature for feature in ir.features}
    if len(by_id) != len(ir.features):
        raise DependencyError("feature IDs must be unique")
    graph: dict[str, set[str]] = {}
    for feature in ir.features:
        references = set(feature.inputs + feature.depends_on + ir.dependencies.get(feature.id, []))
        unknown = references - by_id.keys()
        if unknown:
            raise DependencyError("unknown feature reference", feature_id=feature.id,
                                  details={"references": sorted(unknown)})
        graph[feature.id] = references
    if set(ir.dependencies) - by_id.keys():
        raise DependencyError("dependency graph names an unknown feature")
    active, visited = set(), set()

    def visit(feature_id: str) -> None:
        if feature_id in active:
            raise DependencyError("feature dependency cycle", feature_id=feature_id)
        if feature_id in visited:
            return
        if len(active) >= 200:
            raise DependencyError("feature dependency depth exceeds 200", feature_id=feature_id)
        active.add(feature_id)
        for dependency in graph[feature_id]:
            visit(dependency)
        active.remove(feature_id)
        visited.add(feature_id)

    for feature_id in graph:
        visit(feature_id)
    seen: set[str] = set()
    shape_kinds: dict[str, str] = {}
    for feature in ir.features:
        if graph[feature.id] - seen:
            raise DependencyError("features must appear after all dependencies", feature_id=feature.id)
        count = len(feature.inputs)
        expected_count = 0 if feature.type in PRIMITIVES else 2 if feature.type == "Pocket" else 1
        if feature.type in BOOLEANS:
            if count < 2 or len(set(feature.inputs)) != count:
                raise DependencyError("boolean requires distinct input features", feature_id=feature.id)
        elif count != expected_count:
            raise DependencyError("invalid feature input count", feature_id=feature.id,
                                  details={"expected": expected_count, "actual": count})
        input_kinds = [shape_kinds[reference] for reference in feature.inputs]
        if feature.type in {"Extrude", "Revolve"} and input_kinds != ["sketch"]:
            raise DependencyError("operation requires a sketch input", feature_id=feature.id)
        if feature.type == "Pocket" and input_kinds != ["solid", "sketch"]:
            raise DependencyError("Pocket requires body then sketch", feature_id=feature.id)
        if feature.type in BOOLEANS | {"Hole", "Fillet", "Chamfer"} and any(kind != "solid" for kind in input_kinds):
            raise DependencyError("operation requires solid inputs", feature_id=feature.id)
        shape_kinds[feature.id] = "sketch" if feature.type in SKETCHES else (
            input_kinds[0] if feature.type in {"Transform", "LinearPattern", "CircularPattern"} else "solid")
        seen.add(feature.id)
    output = ir.output_feature or ir.features[-1].id
    if output not in by_id:
        raise DependencyError("unknown output feature", feature_id=output)
    if shape_kinds[output] != "solid":
        raise DependencyError("output feature must be a solid", feature_id=output)
    for constraint in ir.constraints:
        if constraint.feature_id is not None and constraint.feature_id not in by_id:
            raise DependencyError("constraint references unknown feature", feature_id=constraint.feature_id)
    for hole in ir.expected_geometry.holes:
        if hole.feature_id is not None and hole.feature_id not in by_id:
            raise DependencyError("expected hole references unknown feature", feature_id=hole.feature_id)
    if ir.assembly is not None:
        assembly = ir.assembly
        parts = {part.id: part for part in assembly.parts}
        instances = {instance.id: instance for instance in assembly.instances}
        if len(parts) != len(assembly.parts) or len(instances) != len(assembly.instances):
            raise DependencyError("assembly part and instance IDs must be unique within their collections")
        if len({mate.id for mate in assembly.mates}) != len(assembly.mates):
            raise DependencyError("assembly mate IDs must be unique")
        for part in assembly.parts:
            if shape_kinds.get(part.feature_id) != "solid":
                raise DependencyError("assembly part must reference a solid feature", feature_id=part.feature_id)
            if part.expected_geometry.solids != 1:
                raise ParameterError("an assembly part definition must contain exactly one solid")
            for hole in part.expected_geometry.holes:
                if hole.feature_id is not None and hole.feature_id not in by_id:
                    raise DependencyError("part hole references unknown feature", feature_id=hole.feature_id)
        for instance in assembly.instances:
            if instance.part_id not in parts:
                raise DependencyError("assembly instance references unknown part", details={"instance_id": instance.id, "part_id": instance.part_id})
        for mate in assembly.mates:
            if mate.instance_id not in instances or (mate.reference_instance_id is not None and mate.reference_instance_id not in instances):
                raise DependencyError("assembly mate references unknown instance", details={"mate_id": mate.id})
            if mate.instance_id == mate.reference_instance_id:
                raise DependencyError("assembly mate cannot reference its own instance", details={"mate_id": mate.id})


def compile_ir(ir: CADDocumentIR | dict[str, Any]) -> CompiledIR:
    document = validate_ir(ir)
    _validate_dependencies(document)
    values = resolve_parameters(document.parameters)
    scale = UNIT_TO_MM[document.units]

    def scalar(value: Any, power: int = 1) -> float:
        result = evaluate_expression(value, values) * scale ** power
        if not math.isfinite(result) or abs(result) > 1e15:
            raise ParameterError("normalized parameter exceeds numeric budget")
        return result

    def vector(value: Any, power: int = 1) -> list[float]:
        return [scalar(item, power) for item in value]

    def direction(value: Any) -> list[float]:
        result = vector(value, 0)
        norm = math.sqrt(sum(item * item for item in result))
        if norm < 1e-12:
            raise ParameterError("direction must be nonzero")
        return [item / norm for item in result]

    def normalize_selector(value: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(value)
        for field in ("near", "center", "min", "max"):
            if result.get(field) is not None:
                result[field] = vector(result[field])
        if result.get("parallel") is not None:
            result["parallel"] = direction(result["parallel"])
        result["tolerance"] *= scale
        if result.get("min") is not None and result.get("max") is not None:
            if any(low > high for low, high in zip(result["min"], result["max"])):
                raise ParameterError("selector min cannot exceed max")
        return result

    def normalize_hole(value: dict[str, Any]) -> dict[str, Any]:
        hole = deepcopy(value)
        hole["diameter"] = scalar(hole["diameter"])
        if hole["diameter"] <= 0:
            raise ParameterError("expected hole diameter must be positive")
        hole["positions"] = [vector(item) for item in hole["positions"]]
        hole["direction"] = direction(hole["direction"])
        if hole["depth"] is not None:
            hole["depth"] = scalar(hole["depth"])
            if hole["depth"] <= 0:
                raise ParameterError("expected hole depth must be positive")
        return hole

    compiled: list[CompiledFeature] = []
    instance_counts: dict[str, int] = {}
    for feature in document.features:
        try:
            params = feature.params.model_dump()
            for key, value in list(params.items()):
                if value is None or key in {"plane", "closed", "through", "fuse"}:
                    continue
                if key == "selector":
                    params[key] = normalize_selector(value)
                elif key in {"axis_direction", "direction"}:
                    params[key] = direction(value)
                elif key in {"origin", "center", "translation", "axis_origin", "spacing"}:
                    params[key] = vector(value)
                elif key in {"positions", "points"}:
                    params[key] = [vector(item) for item in value]
                elif key == "rotation":
                    params[key] = vector(value, 0)
                else:
                    params[key] = scalar(value, 0 if key in {"angle", "count"} else 1)
            for key in {"width", "depth", "height", "radius", "diameter", "length"} & params.keys():
                if params[key] is not None and params[key] <= 0:
                    raise ParameterError(f"{key} must be positive")
            if feature.type == "Extrude" and abs(params["amount"]) < 1e-9:
                raise ParameterError("extrusion amount must be nonzero")
            if "angle" in params and not 0 < params["angle"] <= 360:
                raise ParameterError("angle must be in (0, 360] degrees")
            if "count" in params:
                count = params["count"]
                if not count.is_integer() or not 1 <= count <= 100:
                    raise ParameterError("pattern count must be an integer from 1 to 100")
                params["count"] = int(count)
            if feature.type == "LinearPattern" and params["count"] > 1 and not any(params["spacing"]):
                raise ParameterError("pattern spacing must be nonzero")
            if feature.type == "Hole":
                positions = [tuple(position) for position in params["positions"]]
                if len(set(positions)) != len(positions):
                    raise ParameterError("hole positions must be distinct")
            if feature.type == "Polyline":
                points = params["points"]
                if len(set(tuple(point) for point in points)) < 3:
                    raise ParameterError("polyline requires three distinct points")
            count = max((instance_counts[item] for item in feature.inputs), default=1)
            if feature.type in {"LinearPattern", "CircularPattern"}:
                count *= params["count"]
            if count > 1000:
                raise ParameterError("nested pattern instance budget exceeded")
            instance_counts[feature.id] = count
        except ParameterError as exc:
            if exc.feature_id is None:
                exc.feature_id = feature.id
            raise
        compiled.append(CompiledFeature(
            id=feature.id, type=feature.type, inputs=feature.inputs,
            depends_on=sorted(set(feature.depends_on + document.dependencies.get(feature.id, []))),
            params=params, validation_conditions=feature.validation_conditions,
        ))
    def normalize_expected(expected: dict[str, Any]) -> dict[str, Any]:
        expected = deepcopy(expected)
        if expected["bbox"] is not None:
            expected["bbox"] = vector(expected["bbox"])
            if any(item <= 0 for item in expected["bbox"]):
                raise ParameterError("expected bbox dimensions must be positive")
        for key in ("volume", "volume_min", "volume_max"):
            if expected[key] is not None:
                expected[key] = scalar(expected[key], 3)
                if expected[key] <= 0:
                    raise ParameterError("expected volume must be positive")
        if expected["volume_min"] is not None and expected["volume_max"] is not None and expected["volume_min"] > expected["volume_max"]:
            raise ParameterError("minimum volume cannot exceed maximum")
        expected["tolerance"] *= scale
        expected["holes"] = [normalize_hole(hole) for hole in expected["holes"]]
        return expected

    expected = normalize_expected(document.expected_geometry.model_dump())
    assembly = document.assembly.model_dump(mode="json") if document.assembly is not None else None
    if assembly is not None:
        # Assembly material is deliberately never fused. Each instance contributes
        # exactly one solid; the default single-part count does not apply.
        if "solids" not in document.expected_geometry.model_fields_set:
            expected["solids"] = len(assembly["instances"])
        for part in assembly["parts"]:
            part["expected_geometry"] = normalize_expected(part["expected_geometry"])
        for instance in assembly["instances"]:
            instance["transform"]["translation"] = vector(instance["transform"]["translation"])
            instance["transform"]["rotation"] = vector(instance["transform"]["rotation"], 0)
        for mate in assembly["mates"]:
            for key in ("origin", "reference_origin"):
                mate[key] = vector(mate[key])
            for key in ("axis", "reference_axis"):
                mate[key] = direction(mate[key])
            mate["offset"] = scalar(mate["offset"])
            mate["transform"]["translation"] = vector(mate["transform"]["translation"])
            mate["transform"]["rotation"] = vector(mate["transform"]["rotation"], 0)
            mate["tolerance"] *= scale
        assembly["collision_tolerance"] *= scale ** 3
    constraints = []
    for constraint in document.constraints:
        item = constraint.model_dump()
        power = 3 if item["type"] == "volume" else 0 if item["type"] == "solid_count" else 1
        if item["type"] == "hole":
            item["expected"] = normalize_hole(item["expected"])
        elif isinstance(item["expected"], (float, int, str)):
            item["expected"] = scalar(item["expected"], power)
        elif isinstance(item["expected"], (tuple, list)):
            item["expected"] = vector(item["expected"], power)
        item["tolerance"] *= scale ** power
        constraints.append(item)
    normalized_values = {
        name: value * scale if document.parameters[name].kind == "length" else value
        for name, value in values.items()
    }
    source_ir = document.model_dump(mode="json")
    if assembly is not None:
        source_ir["expected_geometry"]["solids"] = expected["solids"]
    return CompiledIR(
        document_id=document.document_id, features=compiled,
        output_feature=document.output_feature or document.features[-1].id,
        resolved_parameters=normalized_values, expected_geometry=expected,
        constraints=constraints, source_ir=source_ir, assembly=assembly,
    )


def compile_to_python(ir: CADDocumentIR | dict[str, Any], *, healing_policy: dict[str, Any] | None = None) -> str:
    """A replay script with literal data, never Python supplied by the model."""
    compiled = compile_ir(ir)
    literal = repr(json.dumps(compiled.source_ir, sort_keys=True, ensure_ascii=True, allow_nan=False))
    policy_literal = repr(json.dumps(healing_policy or {}, sort_keys=True, ensure_ascii=True, allow_nan=False))
    return (
        '# Generated by the trusted Agent-CAD IR 2.0 compiler.\n'
        'import json\nimport sys\nfrom pathlib import Path\n'
        'from app.domain.cad_v2.compiler import compile_ir\n'
        'from app.infrastructure.cad_v2.backend import Build123dBackend\n'
        'from app.infrastructure.cad_v2.drawings import export_drawings\n'
        'from app.domain.cad_v2.errors import ConstraintViolationError\n\n'
        f'IR = json.loads({literal})\n'
        f'HEALING_POLICY = json.loads({policy_literal})\n'
        'backend = Build123dBackend(healing_policy=HEALING_POLICY)\n'
        'result = backend.build(compile_ir(IR))\n'
        'report = backend.validate(result)\n'
        'if report["status"] != "passed":\n'
        '    raise ConstraintViolationError("Replay validation did not pass", details=report)\n'
        'output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("cad-output")\n'
        'backend.export(result, output)\n'
        'export_drawings(result.shape, output, title="Agent-CAD engineering drawing")\n'
    )


generate_python = compile_to_python


def apply_edits(
    ir: CADDocumentIR | dict[str, Any], *, parameters: dict[str, Any] | None = None,
    feature_patches: dict[str, dict[str, Any]] | None = None,
    add_features: list[dict[str, Any]] | None = None, delete_features: list[str] | None = None,
    output_feature: str | None = None, expected_geometry: dict[str, Any] | None = None,
    constraints: list[dict[str, Any]] | None = None,
    instance_patches: dict[str, dict[str, Any]] | None = None,
    add_instances: list[dict[str, Any]] | None = None,
    delete_instances: list[str] | None = None,
    mate_patches: dict[str, dict[str, Any]] | None = None,
) -> CADDocumentIR:
    """Apply an atomic validated patch; the caller publishes only after geometry validation."""
    document = validate_ir(ir)
    payload = document.model_dump(mode="json")
    if document.assembly is not None and "solids" not in document.expected_geometry.model_fields_set:
        payload["expected_geometry"]["solids"] = len(document.assembly.instances)
    for name, update in (parameters or {}).items():
        if name not in document.parameters:
            raise ParameterError("cannot edit unknown parameter", details={"parameter": name})
        if not document.parameters[name].editable:
            raise ParameterError("parameter is not editable", details={"parameter": name})
        if isinstance(update, dict):
            if set(update) - {"value", "expression"}:
                raise ParameterError("parameter patches may change only value or expression")
            if "value" in update and "expression" not in update:
                payload["parameters"][name]["expression"] = None
            if "expression" in update and "value" not in update:
                payload["parameters"][name]["value"] = None
            payload["parameters"][name].update(update)
        elif isinstance(update, (float, int)) and not isinstance(update, bool):
            payload["parameters"][name].update(value=update, expression=None)
        else:
            raise ParameterError("parameter edit must be a number or a value/expression patch")
    features = {feature["id"]: feature for feature in payload["features"]}
    for feature_id, patch in (feature_patches or {}).items():
        if feature_id not in features:
            raise DependencyError("cannot patch unknown feature", feature_id=feature_id)
        feature = features[feature_id]
        if not feature["editable"]:
            raise ParameterError("feature is not editable", feature_id=feature_id)
        if set(patch) - {"params", "inputs", "depends_on", "label"}:
            raise ParameterError("feature patch contains protected fields", feature_id=feature_id)
        for key, value in patch.items():
            if key == "params":
                if not isinstance(value, dict):
                    raise ParameterError("feature params patch must be an object", feature_id=feature_id)
                feature["params"].update(value)
            else:
                feature[key] = value
    deleted = set(delete_features or [])
    for feature_id in deleted:
        if feature_id not in features:
            raise DependencyError("cannot delete unknown feature", feature_id=feature_id)
        if not features[feature_id]["editable"]:
            raise ParameterError("feature is not editable", feature_id=feature_id)
    payload["features"] = [feature for feature in payload["features"] if feature["id"] not in deleted]
    payload["features"].extend(deepcopy(add_features or []))
    if output_feature is not None:
        payload["output_feature"] = output_feature
    # These are explicit user edits to design intent, not automatic repair.
    # Unspecified expectations remain intact; lists are deliberately replaced whole.
    if expected_geometry is not None:
        payload["expected_geometry"].update(deepcopy(expected_geometry))
    if constraints is not None:
        payload["constraints"] = deepcopy(constraints)
    if any((instance_patches, add_instances, delete_instances, mate_patches)):
        if payload["assembly"] is None:
            raise ParameterError("instance edits require an assembly document")
        assembly = payload["assembly"]
        instances = {instance["id"]: instance for instance in assembly["instances"]}
        for instance_id, patch in (instance_patches or {}).items():
            if instance_id not in instances:
                raise DependencyError("cannot patch unknown instance", details={"instance_id": instance_id})
            instance = instances[instance_id]
            if not instance["editable"]:
                raise ParameterError("instance is not editable", details={"instance_id": instance_id})
            if set(patch) - {"transform", "name", "part_id"}:
                raise ParameterError("instance patch contains protected fields", details={"instance_id": instance_id})
            for key, value in patch.items():
                if key == "transform":
                    if not isinstance(value, dict):
                        raise ParameterError("instance transform patch must be an object")
                    instance[key].update(deepcopy(value))
                else:
                    instance[key] = deepcopy(value)
        deleted_instances = set(delete_instances or [])
        for instance_id in deleted_instances:
            if instance_id not in instances:
                raise DependencyError("cannot delete unknown instance", details={"instance_id": instance_id})
            if not instances[instance_id]["editable"]:
                raise ParameterError("instance is not editable", details={"instance_id": instance_id})
        original_count = len(assembly["instances"])
        assembly["instances"] = [i for i in assembly["instances"] if i["id"] not in deleted_instances]
        assembly["instances"].extend(deepcopy(add_instances or []))
        if "solids" not in (expected_geometry or {}) and payload["expected_geometry"]["solids"] == original_count:
            payload["expected_geometry"]["solids"] = len(assembly["instances"])
        mates = {mate["id"]: mate for mate in assembly["mates"]}
        for mate_id, patch in (mate_patches or {}).items():
            if mate_id not in mates:
                raise DependencyError("cannot patch unknown assembly mate", details={"mate_id": mate_id})
            if set(patch) - {"origin", "reference_origin", "axis", "reference_axis", "offset", "transform"}:
                raise ParameterError("mate patch contains protected fields", details={"mate_id": mate_id})
            for key, value in patch.items():
                if key == "transform":
                    if not isinstance(value, dict):
                        raise ParameterError("mate transform patch must be an object")
                    mates[mate_id][key].update(deepcopy(value))
                else:
                    mates[mate_id][key] = deepcopy(value)
    # Dependent/expected references deliberately remain: dangling references reject
    # a deletion rather than silently weakening the design.
    result = validate_ir(payload)
    compile_ir(result)
    return result
