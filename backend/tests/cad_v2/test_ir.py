"""Domain gates need no model provider, Mongo, browser or geometry kernel."""
import ast
import copy
import json

import pytest

from app.domain.cad_v2.compiler import apply_edits, compile_ir, compile_to_python, validate_ir
from app.domain.cad_v2.contracts import CADBackend, CADBuildResult
from app.domain.cad_v2.errors import (
    BooleanOperationError, CADError, ConstraintViolationError, DependencyError,
    ExecutionTimeoutError, ExportError, GeometryKernelError, InfrastructureError,
    InvalidTopologyError, ParameterError, RequirementError, SchemaValidationError,
)
from app.domain.cad_v2.expressions import evaluate_expression, expression_dependencies
from app.domain.cad_v2.models import CADDocumentIR


def box_ir():
    return {
        "document_id": "unit-test", "parameters": {
            "width": {"kind": "length", "value": 80},
            "depth": {"kind": "length", "value": 50},
            "height": {"kind": "length", "value": 8},
        },
        "features": [{"id": "plate", "type": "Box", "params": {
            "width": "width", "depth": "depth", "height": "height"}}],
        "expected_geometry": {"bbox": ["width", "depth", "height"], "volume": "width*depth*height"},
    }


@pytest.mark.parametrize("error_type", [RequirementError, SchemaValidationError, ParameterError,
    DependencyError, GeometryKernelError, BooleanOperationError, InvalidTopologyError,
    ConstraintViolationError, ExportError, ExecutionTimeoutError, InfrastructureError])
def test_normalized_errors(error_type):
    error = error_type("failed", feature_id="f", details={"expected": 4}, recoverable=True)
    assert isinstance(error, CADError)
    assert error.to_dict() == {"code": error_type.__name__, "message": "failed", "feature_id": "f",
                               "details": {"expected": 4}, "recoverable": True}
    json.dumps(error.to_dict(), allow_nan=False)


def test_backend_port_is_structural():
    class Adapter:
        def build(self, compiled):
            return CADBuildResult(shape=object(), compiled=compiled)
        def export(self, result, output_dir):
            return []
        def validate(self, result):
            return {"status": "unverified"}
        def properties(self, result):
            return result.geometry
        def query_topology(self, result, selector):
            return []
        def tessellate(self, result, tolerance=0.08):
            return {"vertices": [], "triangles": []}
        def cleanup(self):
            pass
    assert isinstance(Adapter(), CADBackend)


def test_schema_and_deterministic_compilation():
    source = box_ir()
    first, second = compile_ir(source), compile_ir(source)
    assert first == second
    assert first.features[0].params == {"width": 80, "depth": 50, "height": 8, "origin": [0, 0, 0]}
    assert first.expected_geometry["volume"] == 32000
    assert CADDocumentIR.model_validate_json(validate_ir(source).model_dump_json()) == validate_ir(source)


@pytest.mark.parametrize("field,value", [("schema_version", "1.0"), ("units", "feet"),
    ("coordinate_system", "left-handed"), ("extra", True)])
def test_schema_rejects_unsupported_fields(field, value):
    source = box_ir()
    source[field] = value
    with pytest.raises(SchemaValidationError):
        compile_ir(source)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_nonfinite_or_boolean_numeric_input(value):
    source = box_ir()
    source["features"][0]["params"]["width"] = value
    with pytest.raises(SchemaValidationError):
        compile_ir(source)


@pytest.mark.parametrize("expression", ["__import__('os').system('echo hi')", "x.real", "[1][0]",
    "abs(-2)", "True", "1e999", "10**100", "1/0", "'12'", "(lambda:1)()"])
def test_rejects_unsafe_and_unbounded_expressions(expression):
    with pytest.raises(ParameterError):
        evaluate_expression(expression, {"x": 3})


def test_parameter_dependencies_and_bounds():
    source = box_ir()
    source["parameters"]["width"] = {"kind": "length", "expression": "depth * 2 - 20", "min": 20, "max": 100}
    assert compile_ir(source).resolved_parameters["width"] == 80
    assert expression_dependencies("depth * 2 - 20") == {"depth"}
    source["parameters"]["width"]["max"] = 70
    with pytest.raises(ParameterError):
        compile_ir(source)


@pytest.mark.parametrize("parameters", [
    {"width": {"expression": "depth"}, "depth": {"expression": "width"}, "height": {"value": 8}},
    {"width": {"expression": "missing"}, "depth": {"value": 50}, "height": {"value": 8}},
])
def test_reject_parameter_cycles_and_missing_references(parameters):
    source = box_ir()
    source["parameters"] = parameters
    with pytest.raises(DependencyError):
        compile_ir(source)


@pytest.mark.parametrize("unit,scale", [("mm", 1), ("cm", 10), ("m", 1000), ("inch", 25.4)])
def test_units_convert_geometry_and_volume_once(unit, scale):
    source = box_ir()
    source["units"] = unit
    result = compile_ir(source)
    assert result.features[0].params["width"] == pytest.approx(80 * scale)
    assert result.expected_geometry["bbox"] == pytest.approx([80 * scale, 50 * scale, 8 * scale])
    assert result.expected_geometry["volume"] == pytest.approx(32000 * scale**3)
    assert result.source_ir["parameters"]["width"]["value"] == 80


@pytest.mark.parametrize("case", ["duplicate", "unknown", "cycle", "forward", "invalid_input"])
def test_feature_graph_rejects_invalid_designs(case):
    source = box_ir()
    second = {"id": "move", "type": "Transform", "inputs": ["plate"], "params": {"translation": [1, 2, 3]}}
    source["features"].append(second)
    if case == "duplicate":
        second["id"] = "plate"
    elif case == "unknown":
        second["inputs"] = ["missing"]
    elif case == "cycle":
        source["features"][0]["depends_on"] = ["move"]
    elif case == "forward":
        source["features"].reverse()
    else:
        second["inputs"] = []
    with pytest.raises(DependencyError):
        compile_ir(source)


def test_dependency_graph_is_checked_and_kept():
    source = box_ir()
    source["features"].append({"id": "other", "type": "Sphere", "params": {"radius": 2}})
    source["dependencies"] = {"other": ["plate"]}
    assert compile_ir(source).features[1].depends_on == ["plate"]


@pytest.mark.parametrize("params", [{"count": 101, "spacing": [1, 0, 0]},
    {"count": 1.5, "spacing": [1, 0, 0]}, {"count": 3, "spacing": [0, 0, 0]}])
def test_pattern_resource_and_geometry_limits(params):
    source = box_ir()
    source["features"].append({"id": "array", "type": "LinearPattern", "inputs": ["plate"], "params": params})
    with pytest.raises(ParameterError):
        compile_ir(source)


def test_selector_rejects_topological_indices():
    source = box_ir()
    source["features"].append({"id": "round", "type": "Fillet", "inputs": ["plate"],
        "params": {"radius": 1, "selector": {"edge_index": 2}}})
    with pytest.raises(SchemaValidationError):
        compile_ir(source)


def test_geometry_input_types_and_zero_direction():
    source = box_ir()
    source["features"].append({"id": "ext", "type": "Extrude", "inputs": ["plate"], "params": {"amount": 3}})
    with pytest.raises(DependencyError):
        compile_ir(source)
    source["features"][-1] = {"id": "hole", "type": "Hole", "inputs": ["plate"],
        "params": {"diameter": 3, "positions": [[10, 10, 0]], "direction": [0, 0, 0]}}
    with pytest.raises(ParameterError):
        compile_ir(source)


def test_python_export_embeds_only_literal_data():
    source = box_ir()
    source["metadata"] = {"comment": "'); __import__('os').system('BAD') #"}
    script = compile_to_python(source)
    tree = ast.parse(script)
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and node.targets[0].id == "IR")
    assert isinstance(assignment.value.args[0], ast.Constant)
    assert json.loads(assignment.value.args[0].value)["metadata"] == source["metadata"]
    assert script == compile_to_python(source)


def test_atomic_parameter_edit_updates_linked_expectations():
    source = box_ir()
    original = copy.deepcopy(source)
    edited = apply_edits(source, parameters={"width": 90})
    result = compile_ir(edited)
    assert result.features[0].params["width"] == 90
    assert result.expected_geometry["bbox"] == [90, 50, 8]
    assert source == original
    with pytest.raises(ParameterError):
        apply_edits(source, parameters={"width": -1})
    assert source == original


def test_protected_fields_and_dangling_deletions():
    source = box_ir()
    source["parameters"]["width"]["editable"] = False
    with pytest.raises(ParameterError):
        apply_edits(source, parameters={"width": 90})
    with pytest.raises(ParameterError):
        apply_edits(source, feature_patches={"plate": {"type": "Sphere"}})
    source["features"].append({"id": "move", "type": "Transform", "inputs": ["plate"], "params": {}})
    with pytest.raises(DependencyError):
        apply_edits(source, delete_features=["plate"])


def test_feature_patch_and_addition_are_validated():
    source = box_ir()
    edited = apply_edits(source, feature_patches={"plate": {"params": {"height": 10}}},
        add_features=[{"id": "move", "type": "Transform", "inputs": ["plate"], "params": {"translation": [1, 2, 3]}}])
    result = compile_ir(edited)
    assert result.features[0].params["height"] == 10
    assert result.output_feature == "move"


def test_explicit_output_switch_supports_atomic_feature_addition():
    source = box_ir()
    source["output_feature"] = "plate"
    edited = apply_edits(source, add_features=[{
        "id": "move", "type": "Transform", "inputs": ["plate"],
        "params": {"translation": [1, 2, 3]},
    }], output_feature="move")
    assert compile_ir(edited).output_feature == "move"
    assert source["output_feature"] == "plate"
    with pytest.raises(DependencyError):
        apply_edits(source, output_feature="missing")


@pytest.mark.parametrize("units,scale", [("cm", 10), ("inch", 25.4)])
def test_hole_constraints_use_same_units_and_expressions_as_geometry(units, scale):
    source = box_ir()
    source["units"] = units
    source["parameters"]["diameter"] = {"kind": "length", "value": 0.4}
    source["constraints"] = [{"id": "bore", "type": "hole", "expected": {
        "diameter": "diameter", "positions": [[2, "height/2", 0]], "through": True,
    }}]
    expected = compile_ir(source).constraints[0]["expected"]
    assert expected["diameter"] == pytest.approx(0.4 * scale)
    assert expected["positions"][0] == pytest.approx([2 * scale, 4 * scale, 0])


@pytest.mark.parametrize("payload", [
    {"diameter": 1, "positions": [[0, 0, 0]], "through": False},
    {"diameter": 1, "positions": [[0, 0, 0]], "direction": [0, 0, 0]},
    {"diameter": 1, "positions": []},
    {"diameter": -1, "positions": [[0, 0, 0]]},
])
def test_invalid_hole_expectations_fail_before_kernel(payload):
    source = box_ir()
    source["constraints"] = [{"id": "bore", "type": "hole", "expected": payload}]
    with pytest.raises((ParameterError, SchemaValidationError)):
        compile_ir(source)
