"""Independent, source-backed checks of design intent against the active IR graph."""
from __future__ import annotations

import json
import math
from typing import ClassVar, Literal

from pydantic import Field, JsonValue, StrictBool, StrictFloat, StrictInt, model_validator

from .compiler import CompiledIR
from .errors import RequirementCoverageError, SchemaValidationError
from .models import StrictModel
from . import models as ir_models


def _native_parameter_names(feature_type: str) -> set[str]:
    native = getattr(ir_models, f"{feature_type}Params", None)
    names = set(native.model_fields) if native is not None else set()
    if feature_type == "Cylinder":
        names.add("axis_direction")
    return names


def _native_parameter_branches() -> list[dict]:
    branches = []
    for name in vars(ir_models):
        if not name.endswith("Params"):
            continue
        feature_type = name[:-6]
        names = sorted(_native_parameter_names(feature_type))
        if names:
            branches.append({"properties": {
                "feature_type": {"const": feature_type}, "parameter": {"enum": [None, *names]},
                "params": {"anyOf": [{"type": "null"}, {"type": "object", "additionalProperties": False,
                                                         "properties": {key: {} for key in names}}]},
            }})
    return branches


class InputSpecification(StrictModel):
    feature_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    params: dict[str, JsonValue] = Field(min_length=1, max_length=32)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler.resolve_ref_schema(handler(core_schema))
        schema["oneOf"] = _native_parameter_branches()
        return schema

    @model_validator(mode="after")
    def native_parameters(self):
        if not self.params.keys() <= _native_parameter_names(self.feature_type):
            raise ValueError("Input specifications require native geometric parameters")
        if len(json.dumps(self.params, allow_nan=False)) > 16000:
            raise ValueError("input specification exceeds the parameter budget")
        return self


class GeometryRequirement(StrictModel):
    CHECK_FIELDS: ClassVar[dict[str, set[str]]] = {
        "feature": {"feature_type"}, "feature_count": {"feature_type", "count"},
        "feature_dimension": {"feature_type", "parameter", "value"},
        "body_dimension": {"axis", "value"}, "hole_count": {"count"},
        "hole_diameter": {"value"}, "hole_through": {"through"},
        "hole_pitch": {"axis", "value"},
        "feature_position": {"feature_type", "position"}, "unsupported": set(),
        "feature_parameters": {"feature_type", "params"},
        "allowed_features": {"feature_types"}, "solid_count": {"count"},
        "assembly_count": {"count"},
        "feature_inputs": {"feature_type", "inputs"},
    }
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
    source_index: int = Field(default=0, ge=0, le=20)
    source_quote: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    kind: Literal[
        "feature", "feature_count", "feature_dimension", "body_dimension",
        "hole_count", "hole_diameter", "hole_through", "hole_pitch",
        "feature_position", "unsupported",
        "feature_parameters", "allowed_features", "solid_count", "assembly_count",
        "feature_inputs",
    ]
    feature_type: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    parameter: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    axis: Literal["x", "y", "z"] | None = None
    value: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    count: StrictInt | None = Field(default=None, ge=0, le=1000)
    through: StrictBool | None = None
    position: tuple[float, float, float] | None = None
    params: dict[str, JsonValue] | None = Field(default=None, min_length=1, max_length=32)
    feature_types: list[str] | None = Field(default=None, min_length=1, max_length=32)
    inputs: list[InputSpecification] | None = Field(default=None, min_length=1, max_length=12)
    scope: Literal["single", "all"] = "single"

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        schema = handler.resolve_ref_schema(schema)
        branches = []
        for kind, required in cls.CHECK_FIELDS.items():
            allowed = required | ({"feature_type"} if kind == "unsupported" else set())
            properties = {"kind": {"const": kind}}
            for name in ("feature_type", "parameter", "axis", "value", "count", "through", "position", "params", "feature_types", "inputs"):
                if name not in allowed:
                    properties[name] = {"type": "null"}
                elif name in required:
                    properties[name] = {"not": {"type": "null"}}
            branches.append({"properties": properties, "required": ["kind", *sorted(required)]})
        schema["oneOf"] = branches
        schema["allOf"] = [{"if": {"properties": {"kind": {"enum": ["feature_dimension", "feature_parameters"]}}},
                            "then": {"oneOf": _native_parameter_branches()}}]
        return schema

    @model_validator(mode="after")
    def coherent_check(self):
        required = self.CHECK_FIELDS[self.kind]
        allowed = required | ({"feature_type"} if self.kind == "unsupported" else set())
        for name in ("feature_type", "parameter", "axis", "value", "count", "through", "position", "params", "feature_types", "inputs"):
            if name in required and getattr(self, name) is None:
                raise ValueError(f"{self.kind} requires {name}")
            if name not in allowed and getattr(self, name) is not None:
                raise ValueError(f"{self.kind} does not accept {name}")
        if self.value is not None and (not math.isfinite(self.value) or self.value < 0):
            raise ValueError("requirement measurements must be finite and nonnegative")
        if self.position is not None and not all(math.isfinite(value) for value in self.position):
            raise ValueError("requirement positions must be finite")
        if self.kind in {"feature_parameters", "feature_dimension"}:
            names = _native_parameter_names(self.feature_type)
            requested = set(self.params) if self.params is not None else {self.parameter}
            if not requested <= names:
                raise ValueError(f"Unknown native {self.feature_type} parameters: {sorted(requested - names)}")
        if self.params is not None and len(json.dumps(self.params, allow_nan=False)) > 16000:
            raise ValueError("requirement parameter payload exceeds the limit")
        return self


class RequirementExtraction(StrictModel):
    requirements: list[GeometryRequirement] = Field(min_length=1, max_length=200)


class RequirementSet(RequirementExtraction):
    raw_request: str = Field(min_length=1, max_length=16000)
    answers: list[str] = Field(default_factory=list, max_length=20)
    extraction_mode: Literal["deterministic", "independent-model"] = "deterministic"
    model: str | None = None

    @model_validator(mode="after")
    def grounded_sources(self):
        sources = [self.raw_request, *self.answers]
        identifiers = set()
        for requirement in self.requirements:
            if requirement.id in identifiers:
                raise ValueError("requirement IDs must be unique")
            identifiers.add(requirement.id)
            if (requirement.source_index >= len(sources)
                    or requirement.source_quote not in sources[requirement.source_index]):
                raise ValueError("requirement source quotes must occur verbatim in the user's request or answers")
        return self


def _active_features(compiled: CompiledIR, *, include_tools: bool = False):
    by_id = {feature.id: feature for feature in compiled.features}
    active = set()
    roots = ([part["feature_id"] for part in compiled.assembly["parts"]]
             if compiled.assembly else [compiled.output_feature])

    def visit(identifier):
        if identifier in active:
            return
        active.add(identifier)
        feature = by_id[identifier]
        # Cut tools remove material; features inside a tool cannot satisfy a
        # positive requirement such as a mounting hole or reinforcing feature.
        for child in (feature.inputs[:1] if feature.type == "Cut" and not include_tools else feature.inputs):
            visit(child)

    for root in roots:
        visit(root)
    return [feature for feature in compiled.features if feature.id in active]


def verify_requirement_coverage(
    requirements: RequirementSet | dict, compiled: CompiledIR, *, geometry: dict | None = None,
) -> dict:
    """Verify planning intent, independently of later B-Rep/topology validation.

    Ambiguous semantic binding is deliberately unverified. Model labels,
    metadata and claimed coverage never establish a requirement's satisfaction.
    """
    try:
        requirements = RequirementSet.model_validate(requirements)
    except ValueError as exc:
        raise SchemaValidationError("Independent CAD requirements are invalid") from exc
    active = _active_features(compiled)
    construction = _active_features(compiled, include_tools=True)
    by_id = {feature.id: feature for feature in compiled.features}
    holes = [feature for feature in active if feature.type == "Hole"]
    patterned_holes = bool(holes and any(feature.type in {"LinearPattern", "CircularPattern"} for feature in active))
    tolerance = 1e-4
    checks = []

    def close(left, right):
        return isinstance(left, (int, float)) and not isinstance(left, bool) and math.isclose(left, right, abs_tol=tolerance, rel_tol=1e-9)

    def matches(actual, expected):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(key in actual and matches(actual[key], value) for key, value in expected.items())
        if isinstance(expected, (list, tuple)):
            return isinstance(actual, (list, tuple)) and len(actual) == len(expected) and all(matches(a, b) for a, b in zip(actual, expected))
        if isinstance(expected, bool) or expected is None or isinstance(expected, str):
            return type(actual) is type(expected) and actual == expected
        return close(actual, expected)

    for requirement in requirements.requirements:
        status, reason, actual, evidence = "unverified", "No deterministic verifier for this requirement", None, []
        verification_stage = "plan"
        candidates = [feature for feature in active if feature.type == requirement.feature_type]
        kind = requirement.kind
        if kind == "feature":
            actual = len(candidates)
            evidence = [feature.id for feature in candidates]
            if candidates:
                status, reason = "passed", "Typed feature contributes to the output graph"
            elif requirement.feature_type in {"Rib", "Slot", "Shell", "Gear", "Surface"}:
                status = "unverified" if any(feature.type in {"Union", "Cut", "Intersection"} for feature in active) else "failed"
                reason = "Generic boolean geometry cannot establish this semantic feature"
            else:
                status, reason = "failed", "Requested typed feature is absent from the output graph"
        elif kind == "feature_count":
            if requirement.count == 0:
                candidates = [feature for feature in construction if feature.type == requirement.feature_type]
            actual, evidence = len(candidates), [feature.id for feature in candidates]
            status, reason = ("passed" if actual == requirement.count else "failed"), "Active typed feature count"
        elif kind == "allowed_features":
            actual = sorted({feature.type for feature in construction})
            evidence = [feature.id for feature in construction]
            status = "passed" if set(actual) <= set(requirement.feature_types) else "failed"
            reason = "Every contributing operation, including boolean tools, belongs to the allowed types"
        elif kind == "feature_parameters":
            actual = [{**feature.params, **({"axis_direction": [0, 0, 1]} if feature.type == "Cylinder" else {})}
                      for feature in candidates]
            bound = [feature for feature, params in zip(candidates, actual) if matches(params, requirement.params)]
            evidence = [feature.id for feature in bound]
            passed = bool(candidates) and (len(bound) == len(candidates) if requirement.scope == "all" else len(bound) == 1)
            status, reason = ("passed" if passed else "failed"), "Exact native parameter subset binds a unique typed feature, or every feature for scope all"
            spatial = {"origin", "center", "axis_origin", "axis_direction", "positions", "direction", "points", "plane", "tangents"}
            transforms = [feature.params for feature in active if feature.type == "Transform"]
            transforms += [instance["transform"] for instance in (compiled.assembly or {}).get("instances", [])]
            if spatial & requirement.params.keys() and any(
                any(abs(value) > tolerance for key in ("translation", "rotation") for value in transform.get(key, []))
                for transform in transforms
            ):
                status, reason = "unverified", "Spatial native parameters need world-space binding after nonidentity transforms"
        elif kind == "feature_inputs":
            bound = []
            actual = []
            for feature in candidates:
                children = [by_id[identifier] for identifier in feature.inputs]
                actual.append([{"feature_type": child.type, "params": child.params} for child in children])
                if len(children) == len(requirement.inputs) and all(
                    child.type == expected.feature_type and matches(child.params, expected.params)
                    for child, expected in zip(children, requirement.inputs)
                ):
                    bound.append(feature)
            evidence = [feature.id for feature in bound]
            passed = bool(candidates) and (len(bound) == len(candidates) if requirement.scope == "all" else len(bound) == 1)
            status, reason = ("passed" if passed else "failed"), "Ordered input types and native parameters on the actual contributing operation"
        elif kind == "solid_count":
            verification_stage = "geometry"
            actual = (geometry or {}).get("solids")
            if actual is not None:
                status = "passed" if actual == requirement.count else "failed"
            reason = "Final B-Rep solid traversal"
        elif kind == "assembly_count":
            assembly = compiled.assembly or {}
            parts, instances = assembly.get("parts", []), assembly.get("instances", [])
            actual = {"parts": len(parts), "instances": len(instances)}
            once_each = sorted(item["part_id"] for item in instances) == sorted(item["id"] for item in parts)
            if len(parts) != requirement.count or len(instances) != requirement.count or not once_each or not assembly.get("collision_check"):
                status = "failed"
            elif geometry is not None:
                verification_stage = "geometry"
                actual["components"] = geometry.get("assembly", {}).get("component_count")
                status = "passed" if actual["components"] == requirement.count else "failed"
            else:
                verification_stage = "geometry"
            reason = "Distinct part and instance identities, one occurrence per part, measured components and required collision validation"
        elif kind in {"feature_dimension", "feature_position"}:
            evidence = [feature.id for feature in candidates]
            if not candidates:
                status, reason = "failed", "Requested feature is absent from the output graph"
            elif len(candidates) > 1 and requirement.scope != "all":
                reason = "Multiple candidate features; semantic target cannot be bound uniquely"
            elif kind == "feature_dimension":
                actual = [feature.params.get(requirement.parameter) for feature in candidates]
                if any(value is None or not isinstance(value, (int, float)) or isinstance(value, bool) for value in actual):
                    reason = "Requested feature measurement has no numeric compiled parameter"
                else:
                    status = "passed" if all(close(value, requirement.value) for value in actual) else "failed"
                    reason = "Resolved feature parameters in millimeters or native angle/count units"
            else:
                actual = [feature.params.get("origin", feature.params.get("center")) for feature in candidates]
                if any(position is None for position in actual):
                    reason = "Requested feature has no directly verifiable origin"
                else:
                    status = "passed" if all(len(position) == 3 and all(close(a, b) for a, b in zip(position, requirement.position)) for position in actual) else "failed"
                    reason = "Compiled feature origins"
        elif kind == "body_dimension":
            axis = "xyz".index(requirement.axis)
            bbox = compiled.expected_geometry.get("bbox")
            # An expectation alone is not proof: independently require matching
            # dimensions on the single primitive body that feeds the output.
            primitives = [feature for feature in active if feature.type in {"Box", "Cylinder", "Sphere", "Extrude", "Revolve", "Sweep", "Loft"}]
            boxes = [feature for feature in primitives if feature.type == "Box"]
            measured_bbox = (geometry or {}).get("bbox", {}).get("size")
            if geometry is not None:
                verification_stage = "geometry"
                if not isinstance(measured_bbox, (list, tuple)) or len(measured_bbox) != 3:
                    reason = "Final geometry did not provide a measured body envelope"
                else:
                    actual = measured_bbox[axis]
                    status = "passed" if close(actual, requirement.value) else "failed"
                    reason = "Final B-Rep bounding box measured independently of IR expectations"
            elif bbox is not None and not close(bbox[axis], requirement.value):
                actual, status, reason = bbox[axis], "failed", "IR envelope expectation contradicts the user's size"
            elif len(primitives) == 1 and len(boxes) == 1 and not any(feature.type in {"Transform", "LinearPattern", "CircularPattern", "Intersection"} for feature in active):
                actual = boxes[0].params[{"x": "width", "y": "depth", "z": "height"}[requirement.axis]]
                evidence = [boxes[0].id]
                if close(actual, requirement.value) and bbox is None:
                    verification_stage = "geometry"
                    reason = "Primitive size matches; final body envelope remains to be measured"
                else:
                    status = "passed" if close(actual, requirement.value) else "failed"
                    reason = "Primitive body measurement and independent envelope expectation"
            else:
                verification_stage = "geometry"
                reason = "Overall size needs an independently measurable body envelope"
        elif kind.startswith("hole_"):
            evidence = [feature.id for feature in holes]
            if patterned_holes:
                reason = "Patterned-hole multiplicity requires a dedicated geometric verifier"
            elif not holes and kind == "hole_count" and requirement.count == 0:
                if len(active) == 1 and active[0].type in {"Box", "Cylinder", "Sphere"} and compiled.assembly is None:
                    actual, status, reason = 0, "passed", "A single solid primitive without material removal has no holes"
                else:
                    reason = "Absence of typed Hole features cannot prove a boolean or other constructed body has no holes"
            elif not holes:
                status, reason = "failed", "Requested holes are absent from the output graph"
            elif kind == "hole_count":
                actual = sum(len(feature.params["positions"]) for feature in holes)
                status, reason = ("passed" if actual == requirement.count else "failed"), "Active Hole positions"
            elif kind == "hole_diameter":
                actual = [feature.params["diameter"] for feature in holes]
                status, reason = ("passed" if all(close(value, requirement.value) for value in actual) else "failed"), "Resolved Hole diameters"
            elif kind == "hole_through":
                actual = [feature.params["through"] for feature in holes]
                status, reason = ("passed" if all(value is requirement.through for value in actual) else "failed"), "Typed through/blind hole definition"
            elif kind == "hole_pitch":
                axis = "xyz".index(requirement.axis)
                coordinates = sorted({round(position[axis], 9) for feature in holes for position in feature.params["positions"]})
                if len(coordinates) != 2:
                    reason = "Hole pitch is only verified for a two-coordinate pattern"
                else:
                    actual = coordinates[1] - coordinates[0]
                    status, reason = ("passed" if close(actual, requirement.value) else "failed"), "Difference between compiled hole center coordinates"
        checks.append({"id": requirement.id, "source_index": requirement.source_index,
                       "source_quote": requirement.source_quote, "kind": kind, "status": status,
                       "reason": reason, "actual": actual, "feature_ids": evidence,
                       "verification_stage": verification_stage,
                       "expected": {name: (requirement.model_dump(mode="json")[name]) for name in ("feature_type", "parameter", "axis", "value", "count", "through", "position", "params", "feature_types", "inputs")
                                    if getattr(requirement, name) is not None}})
    status = "failed" if any(check["status"] == "failed" for check in checks) else "unverified" if any(check["status"] == "unverified" for check in checks) else "passed"
    return {"status": status, "checks": checks,
            "scope": "Independent source-backed requirements against compiled active feature graph; B-Rep validation remains required"}


def require_coverage(
    requirements: RequirementSet | dict, compiled: CompiledIR, *, geometry: dict | None = None,
    allow_geometry_pending: bool = False,
) -> dict:
    report = verify_requirement_coverage(requirements, compiled, geometry=geometry)
    failing = [check for check in report["checks"] if check["status"] != "passed"]
    deferred_only = (geometry is None and allow_geometry_pending and failing
                     and all(check["status"] == "unverified" and check["verification_stage"] == "geometry" for check in failing))
    if report["status"] != "passed" and not deferred_only:
        raise RequirementCoverageError(
            "生成方案未完整满足原始需求 / Generated design does not verify all requested features",
            details={"requirement_coverage": report},
        )
    return report
