"""Atomic natural-language edit plans: offline geometry and mocked structured plans."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from app.application.services.cad_v2_planner import plan, plan_edit
from app.domain.cad_v2.compiler import apply_edits, compile_ir
from app.domain.cad_v2.editing import EditPlan, validate_edit_plan
from app.domain.cad_v2.errors import InfrastructureError, RequirementError, SchemaValidationError
from app.domain.cad_v2.models import CADDocumentIR

PROMPT = "生成一块长80mm宽50mm厚8mm的安装板，在四角开四个直径6mm的通孔，孔中心的水平距离为60mm，垂直距离为30mm。"


def plate():
    return asyncio.run(plan(PROMPT, "natural-edit"))


def model_response(monkeypatch, payload):
    calls = []
    monkeypatch.setenv("CAD_V2_API_KEY", "test-only")
    async def generate(_self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(payload=copy.deepcopy(payload), model="test-model", usage={"total_tokens": 42})
    monkeypatch.setattr("app.application.services.cad_v2_planner.StructuredModelGateway.generate", generate)
    return calls


def test_multiple_subset_edits_preserve_other_geometry_and_build_real_kernel(tmp_path):
    from app.infrastructure.cad_v2.backend import Build123dBackend
    from app.infrastructure.cad_v2.validation import validate
    original = plate()
    untouched = original.model_dump(mode="json")
    edit = asyncio.run(plan_edit("将左边两个孔的直径改成8mm，并将右边两个孔向左移动5mm，其他保持不变。", original))
    assert len(edit.intents) == 2
    changed = apply_edits(original, **edit.apply_kwargs())
    assert original.model_dump(mode="json") == untouched
    assert changed.features[0] == original.features[0]
    assert changed.parameters == original.parameters
    assert changed.constraints == original.constraints
    assert changed.expected_geometry.bbox == original.expected_geometry.bbox
    compiled = compile_ir(changed)
    groups = {feature.params["diameter"]: feature.params["positions"] for feature in compiled.features if feature.type == "Hole"}
    assert groups[6] == [[65, 10, 0], [65, 40, 0]]
    assert groups[8] == [[10, 10, 0], [10, 40, 0]]
    backend = Build123dBackend()
    result = backend.build(compiled)
    assert validate(result)["status"] == "passed"
    backend.export(result, tmp_path / "edited")
    assert result.geometry["step_roundtrip"]["solids"] == 1


def test_add_move_and_remove_holes_across_revisions_have_measured_expectations():
    from app.infrastructure.cad_v2.backend import Build123dBackend
    from app.infrastructure.cad_v2.validation import validate
    current = plate()
    backend = Build123dBackend()
    for prompt, expected_count in [
        ("在中心添加一个直径4mm的通孔", 5),
        ("删除左边两个孔", 3),
        ("删除所有孔", 0),
    ]:
        edit = asyncio.run(plan_edit(prompt, current))
        current = apply_edits(current, **edit.apply_kwargs())
        assert sum(len(feature.params.positions) for feature in current.features if feature.type == "Hole") == expected_count
        assert sum(len(hole.positions) for hole in current.expected_geometry.holes) == expected_count
        assert validate(backend.build(compile_ir(current)))["status"] == "passed"
    assert [feature.id for feature in current.features] == ["plate"]


def test_multi_edit_unit_conversion_and_complete_grammar(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("CAD_V2_API_KEY", raising=False)
    current = plate()
    edit = asyncio.run(plan_edit("set left two holes diameter to 0.8 cm and move right two holes left by 0.5 cm", current))
    compiled = compile_ir(apply_edits(current, **edit.apply_kwargs()))
    assert compiled.features[-1].params["diameter"] == 8
    with pytest.raises(InfrastructureError):
        asyncio.run(plan_edit("将左边两个孔的直径改成8mm，并增加一个加强筋", current))


def test_ambiguous_offline_selection_does_not_guess():
    current = plate()
    current.features[1].params.positions = [(10, 10, 0), (20, 20, 0), (30, 30, 0), (40, 40, 0)]
    with pytest.raises(RequirementError, match="ambiguous"):
        asyncio.run(plan_edit("将左边两个孔的直径改成8mm", current))


def test_structured_model_complex_add_and_move_preserves_ids_and_requirements(monkeypatch):
    from app.infrastructure.cad_v2.backend import Build123dBackend
    from app.infrastructure.cad_v2.validation import validate
    current = CADDocumentIR.model_validate({
        "document_id": "model-edit", "features": [
            {"id": "plate", "type": "Box", "params": {"width": 80, "depth": 50, "height": 8}},
            {"id": "bore", "type": "Hole", "inputs": ["plate"], "params": {"diameter": 6, "positions": [[10, 10, 0]], "through": True}},
        ], "output_feature": "bore",
        "expected_geometry": {"bbox": [80, 50, 8], "volume": "80*50*8-pi*3**2*8", "holes": [{"feature_id": "bore", "diameter": 6, "positions": [[10, 10, 0]]}]},
        "constraints": [{"id": "height", "type": "dimension", "axis": "z", "expected": 8}],
    })
    prompt = "Move bore to x=20, y=10 and add a 10 by 8 rectangular through slot at x=35, y=20."
    payload = {
        "feature_patches": [{"id": "bore", "params": {"positions": [[20, 10, 0]]}}],
        "add_features": [
            {"id": "slot_tool", "type": "Box", "params": {"width": 10, "depth": 8, "height": 8, "origin": [35, 20, 0]}},
            {"id": "slot", "type": "Cut", "inputs": ["bore", "slot_tool"], "params": {}},
        ], "output_feature": "slot",
        "expected_geometry": {"volume": "80*50*8-pi*3**2*8-10*8*8", "holes": [{"feature_id": "bore", "diameter": 6, "positions": [[20, 10, 0]], "through": True}]},
        "intents": [{"source": prompt, "action": "move", "targets": ["bore"]},
                    {"source": prompt, "action": "add", "targets": ["slot_tool", "slot"]}],
    }
    calls = model_response(monkeypatch, payload)
    edit = asyncio.run(plan_edit(prompt, current))
    assert edit.model == "test-model" and edit.usage["total_tokens"] == 42
    assert calls[0]["role"] == "editor" and '"current_ir"' in calls[0]["request"]
    assert all(branch["additionalProperties"] is False for branch in calls[0]["schema"]["oneOf"])
    edited = apply_edits(current, **edit.apply_kwargs())
    assert edited.features[0] == current.features[0]
    assert edited.constraints == current.constraints
    assert edited.expected_geometry.bbox == current.expected_geometry.bbox
    assert validate(Build123dBackend().build(compile_ir(edited)))["status"] == "passed"


def test_model_clarification_is_exposed_without_mutations(monkeypatch):
    current = plate()
    before = current.model_dump()
    model_response(monkeypatch, {"clarification": "Which two holes and which movement direction?"})
    with pytest.raises(RequirementError) as error:
        asyncio.run(plan_edit("Move those two holes a bit", current))
    assert error.value.details["clarification"] == "Which two holes and which movement direction?"
    assert current.model_dump() == before


@pytest.mark.parametrize("payload", [
    {"features": []},
    {"feature_patches": [{"id": "plate", "type": "Sphere", "params": {"radius": 3}}]},
    {"parameters": {"height": {"value": 6, "expression": "7"}}},
])
def test_model_cannot_return_full_replacement_or_protected_fields(monkeypatch, payload):
    model_response(monkeypatch, payload)
    with pytest.raises(SchemaValidationError):
        asyncio.run(plan_edit("Change the indicated part", plate()))


def test_model_cannot_weaken_unrelated_requirements(monkeypatch):
    current = plate()
    current.constraints = CADDocumentIR.model_validate({**current.model_dump(), "constraints": [
        {"id": "height", "type": "dimension", "axis": "z", "expected": 8}]}).constraints
    prompt = "Change the selected mounting bore size"
    base = {"parameters": {"hole_diameter": 8},
            "intents": [{"source": prompt, "action": "modify", "targets": ["mounting_holes"], "parameters": ["hole_diameter"]}]}
    for weakening in [{"constraints": []}, {"expected_geometry": {"holes": []}},
                      {"expected_geometry": {"volume": None}}, {"expected_geometry": {"tolerance": 1}},
                      {"expected_geometry": {"bbox": [100, 50, 8]}}]:
        model_response(monkeypatch, {**base, **weakening})
        with pytest.raises(RequirementError):
            asyncio.run(plan_edit(prompt, current))


def test_model_shared_parameter_cannot_change_unrelated_geometry():
    current = plate()
    raw = current.model_dump(mode="json")
    raw["parameters"]["derived"] = {"value": None, "expression": "hole_diameter*2"}
    raw["features"][0]["params"]["height"] = "derived"
    current = CADDocumentIR.model_validate(raw)
    prompt = "Change all hole diameters to 8"
    edit = EditPlan.model_validate({"parameters": {"hole_diameter": 8}, "intents": [
        {"source": prompt, "action": "modify", "targets": ["mounting_holes"], "parameters": ["hole_diameter"]}]})
    with pytest.raises(RequirementError, match="shared"):
        validate_edit_plan(edit, current, prompt)


def test_model_cannot_hide_existing_geometry_by_output_change():
    current = plate()
    prompt = "Change plate thickness to 10"
    edit = EditPlan.model_validate({"parameters": {"height": 10}, "output_feature": "plate", "intents": [
        {"source": prompt, "action": "modify", "targets": ["plate"], "parameters": ["height"], "changes_envelope": True}]})
    with pytest.raises(RequirementError, match="detach"):
        validate_edit_plan(edit, current, prompt)


def test_model_cannot_delete_and_recreate_existing_id():
    current = plate()
    prompt = "Remove plate"
    edit = EditPlan.model_validate({"delete_features": ["plate"], "add_features": [current.features[0].model_dump()],
                                   "intents": [{"source": prompt, "action": "remove", "targets": ["plate"]}]})
    with pytest.raises(RequirementError, match="reuse"):
        validate_edit_plan(edit, current, prompt)


def test_structured_assembly_move_and_add_instance_preserve_part_and_mates(monkeypatch):
    from app.infrastructure.cad_v2.backend import Build123dBackend
    current = CADDocumentIR.model_validate({
        "document_id": "assembly-edit", "features": [{"id": "pin", "type": "Cylinder", "params": {"radius": 2, "height": 4}}],
        "assembly": {"parts": [{"id": "pin_part", "name": "Pin", "feature_id": "pin"}],
                     "instances": [{"id": "base", "part_id": "pin_part"},
                                   {"id": "slider", "part_id": "pin_part", "transform": {"translation": [20, 0, 0]}}],
                     "mates": [{"id": "ground", "type": "fixed", "instance_id": "base"}]},
        "expected_geometry": {"bbox": [24, 4, 4]},
    })
    prompt = "Move slider to x=30 and add second slider at x=40."
    payload = {"instance_patches": [{"id": "slider", "transform": {"translation": [30, 0, 0]}}],
               "add_instances": [{"id": "slider2", "part_id": "pin_part", "transform": {"translation": [40, 0, 0]}}],
               "expected_geometry": {"bbox": [44, 4, 4], "solids": 3},
               "intents": [{"source": "Move slider to x=30", "action": "move", "instances": ["slider"], "changes_envelope": True},
                           {"source": "add second slider at x=40", "action": "add", "instances": ["slider2"], "changes_envelope": True}]}
    model_response(monkeypatch, payload)
    edit = asyncio.run(plan_edit(prompt, current))
    changed = apply_edits(current, **edit.apply_kwargs())
    assert changed.features == current.features
    assert changed.assembly.parts == current.assembly.parts
    assert changed.assembly.mates == current.assembly.mates
    assert changed.assembly.instances[0] == current.assembly.instances[0]
    backend = Build123dBackend()
    report = backend.validate(backend.build(compile_ir(changed)))
    assert report["status"] == "passed"


def test_move_intent_cannot_change_dimensions_or_unrequested_mate():
    current = plate()
    prompt = "Move mounting holes to the right"
    edit = EditPlan.model_validate({"feature_patches": [{"id": "mounting_holes", "params": {"diameter": 20}}],
        "intents": [{"source": prompt, "action": "move", "targets": ["mounting_holes"]}]})
    with pytest.raises(RequirementError, match="dimensions"):
        validate_edit_plan(edit, current, prompt)


def test_model_cannot_change_all_bores_for_a_subset_request():
    current = plate()
    prompt = "Increase the left two holes diameter to 8 mm."
    edit = EditPlan.model_validate({"parameters": {"hole_diameter": 8}, "intents": [
        {"source": prompt, "action": "modify", "targets": ["mounting_holes"], "parameters": ["hole_diameter"]}]})
    with pytest.raises(RequirementError, match="outside the requested subset"):
        validate_edit_plan(edit, current, prompt)


def test_single_move_preserves_diameter_and_unselected_bores():
    current = plate()
    edit = asyncio.run(plan_edit("将左边两个孔向右移动5mm", current))
    changed = compile_ir(apply_edits(current, **edit.apply_kwargs()))
    assert changed.features[1].params["diameter"] == 6
    assert changed.features[1].params["positions"] == [[15, 10, 0], [15, 40, 0], [70, 10, 0], [70, 40, 0]]
