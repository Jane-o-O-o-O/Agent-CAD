"""Explicit feature add/delete carries the user's revised design intent to real solids."""
import copy
import math

from app.domain.cad_v2.compiler import apply_edits, compile_ir
from app.infrastructure.cad_v2.backend import Build123dBackend
from app.infrastructure.cad_v2.validation import validate


def plate():
    return {
        "document_id": "intent-edit", "features": [{"id": "plate", "type": "Box", "params": {
            "width": 80, "depth": 50, "height": 8,
        }}], "output_feature": "plate",
        "expected_geometry": {"bbox": [80, 50, 8], "solids": 1, "volume": 32000, "holes": []},
        "constraints": [{"id": "height", "type": "dimension", "axis": "z", "expected": 8}],
    }


def test_add_then_delete_hole_with_explicit_intent_changes_preserves_dimensions(tmp_path):
    original = plate()
    untouched = copy.deepcopy(original)
    backend = Build123dBackend()
    initial = backend.build(compile_ir(original))
    assert validate(initial)["status"] == "passed"
    hole = {"diameter": 6, "positions": [[40, 25, 0]], "through": True}
    added = apply_edits(original, add_features=[{
        "id": "bore", "type": "Hole", "inputs": ["plate"], "params": hole,
    }], output_feature="bore", expected_geometry={
        "volume": "80*50*8-3.141592653589793*3**2*8", "holes": [{**hole, "feature_id": "bore"}],
    })
    assert original == untouched
    assert list(added.expected_geometry.bbox) == [80, 50, 8]
    built = backend.build(compile_ir(added))
    assert validate(built)["status"] == "passed"
    assert math.isclose(initial.geometry["volume"] - built.geometry["volume"], math.pi * 3**2 * 8)
    backend.export(built, tmp_path / "added")
    assert built.geometry["step_roundtrip"]["solids"] == 1
    restored = apply_edits(added, delete_features=["bore"], output_feature="plate",
                          expected_geometry={"holes": [], "volume": 32000})
    rebuilt = backend.build(compile_ir(restored))
    assert validate(rebuilt)["status"] == "passed"
    assert rebuilt.geometry["volume"] == initial.geometry["volume"]
    assert rebuilt.geometry["bbox"] == initial.geometry["bbox"]
    assert restored.constraints == added.constraints


def test_adding_hole_without_explicit_new_expectation_does_not_weaken_validation():
    backend = Build123dBackend()
    edited = apply_edits(plate(), add_features=[{
        "id": "bore", "type": "Hole", "inputs": ["plate"],
        "params": {"diameter": 6, "positions": [[40, 25, 0]], "through": True},
    }], output_feature="bore")
    assert edited.expected_geometry.volume == 32000
    assert validate(backend.build(compile_ir(edited)))["status"] == "failed"


def test_explicit_constraint_replacement_still_requires_supported_verification():
    edited = apply_edits(plate(), constraints=[{
        "id": "manufacturing", "type": "custom", "description": "Not automatically verified",
    }])
    assert validate(Build123dBackend().build(compile_ir(edited)))["status"] == "unverified"
