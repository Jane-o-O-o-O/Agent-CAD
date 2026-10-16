"""A valid solid cannot substitute for preserving independently supplied intent."""
import asyncio
from types import SimpleNamespace

import jsonschema
import pytest
from pydantic import ValidationError

from app.application.services.cad_v2_requirements import (
    deterministic_requirements, extract_requirements, requirement_response_schema,
)
from app.domain.cad_v2.compiler import compile_ir
from app.domain.cad_v2.errors import RequirementCoverageError, RequirementError, SchemaValidationError
from app.domain.cad_v2.requirements import RequirementSet, require_coverage, verify_requirement_coverage


PROMPT = "长80mm宽50mm厚8mm安装板，四个直径6mm通孔，水平孔距60mm，垂直孔距30mm"


def plate(*, count=4, diameter=6, width=80):
    return compile_ir({"document_id": "coverage", "features": [
        {"id": "plate", "type": "Box", "params": {"width": width, "depth": 50, "height": 8}},
        {"id": "holes", "type": "Hole", "inputs": ["plate"], "params": {
            "diameter": diameter, "positions": [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]][:count], "through": True}},
    ], "output_feature": "holes", "expected_geometry": {"bbox": [80, 50, 8]}})


class MockGateway:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(payload=self.payload, model="requirements-mock")


def test_complete_explicit_request_checks_actual_params_and_active_graph_without_a_model():
    requirements = deterministic_requirements(PROMPT)
    assert require_coverage(requirements, plate())["status"] == "passed"
    assert requirements.raw_request == PROMPT
    assert {item.kind for item in requirements.requirements} >= {
        "body_dimension", "hole_count", "hole_diameter", "hole_through", "hole_pitch"}
    assert all(item.source_quote in PROMPT for item in requirements.requirements)


@pytest.mark.parametrize("mutation,kind", [({"count": 2}, "hole_count"), ({"diameter": 8}, "hole_diameter"), ({"width": 70}, "body_dimension")])
def test_valid_ir_dropping_explicit_count_diameter_or_size_fails(mutation, kind):
    requirements = deterministic_requirements(PROMPT)
    with pytest.raises(RequirementCoverageError) as failure:
        require_coverage(requirements, plate(**mutation))
    checks = failure.value.details["requirement_coverage"]["checks"]
    assert any(check["kind"] == kind and check["status"] == "failed" for check in checks)


def test_off_graph_and_cut_tool_holes_cannot_claim_required_holes():
    requirements = deterministic_requirements("四个通孔，孔径6mm")
    data = plate().source_ir
    data["output_feature"] = "plate"
    assert verify_requirement_coverage(requirements, compile_ir(data))["status"] == "failed"
    data["features"].append({"id": "cut", "type": "Cut", "inputs": ["plate", "holes"], "params": {}})
    data["output_feature"] = "cut"
    assert verify_requirement_coverage(requirements, compile_ir(data))["status"] == "failed"


def test_omitted_rib_cannot_be_hidden_in_model_metadata():
    requirements = deterministic_requirements("长80mm宽50mm厚8mm安装板，添加加强筋")
    data = plate().source_ir
    data["metadata"] = {"requirements_covered": ["加强筋"], "covered": True}
    report = verify_requirement_coverage(requirements, compile_ir(data))
    assert report["status"] == "failed"
    assert next(check for check in report["checks"] if check["source_quote"] == "加强筋")["status"] == "failed"


def test_unverifiable_semantics_fail_even_when_geometry_is_valid_and_preflight_allows_measurements():
    requirements = deterministic_requirements("添加加强筋")
    compiled = compile_ir({"features": [
        {"id": "plate", "type": "Box", "params": {"width": 80, "depth": 50, "height": 8}},
        {"id": "rib", "type": "Box", "label": "加强筋", "params": {"width": 80, "depth": 2, "height": 10}},
        {"id": "united", "type": "Union", "inputs": ["plate", "rib"], "params": {}},
    ], "output_feature": "united"})
    with pytest.raises(RequirementCoverageError) as failure:
        require_coverage(requirements, compiled, allow_geometry_pending=True)
    assert failure.value.details["requirement_coverage"]["status"] == "unverified"


def test_compound_body_size_is_deferred_then_checked_against_measured_final_geometry():
    requirements = deterministic_requirements("整体长80mm")
    compiled = compile_ir({"features": [
        {"id": "body", "type": "Box", "params": {"width": 40, "depth": 50, "height": 8}},
        {"id": "extension", "type": "Box", "params": {"width": 40, "depth": 50, "height": 8, "origin": [40, 0, 0]}},
        {"id": "output", "type": "Union", "inputs": ["body", "extension"], "params": {}},
    ], "output_feature": "output", "expected_geometry": {"bbox": [80, 50, 8]}})
    preflight = require_coverage(requirements, compiled, allow_geometry_pending=True)
    assert preflight["status"] == "unverified"
    assert preflight["checks"][0]["verification_stage"] == "geometry"
    assert require_coverage(requirements, compiled, geometry={"bbox": {"size": [80, 50, 8]}})["status"] == "passed"
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, compiled, geometry={"bbox": {"size": [70, 50, 8]}})
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, compiled, geometry={})


def test_measured_shape_can_still_fail_even_after_exact_box_preflight_passed():
    requirements = deterministic_requirements(PROMPT)
    assert require_coverage(requirements, plate())["status"] == "passed"
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, plate(), geometry={"bbox": {"size": [70, 50, 8]}})


def test_original_conflict_resolves_only_the_answered_obligation():
    original = "长80mm宽50mm厚8mm安装板，四个通孔，五个通孔，孔径6mm"
    with pytest.raises(RequirementError):
        deterministic_requirements(original)
    turns = [{"question": "孔数量有冲突，请明确需要几个孔", "answer": "四个孔"}]
    requirements = deterministic_requirements(original, clarification=turns)
    count = next(item for item in requirements.requirements if item.kind == "hole_count")
    assert count.count == 4 and count.source_index == 1
    assert require_coverage(requirements, plate())["status"] == "passed"


@pytest.mark.parametrize("answer", ["4", "四", "4个", "孔数量为4"])
def test_count_only_answer_resolves_count_conflict_without_guessing_dimensions(answer):
    original = "长80mm宽50mm厚8mm安装板，四个孔，五个孔"
    requirements = deterministic_requirements(original, clarification=[{"question": "孔数量有冲突，最终需要几个孔？", "answer": answer}])
    assert next(item.count for item in requirements.requirements if item.kind == "hole_count") == 4
    if answer != "孔数量为4":
        with pytest.raises(RequirementError):
            deterministic_requirements(original, clarification=[{"question": "需要补充孔数量和厚度", "answer": answer}])


def test_bare_answer_is_bound_to_single_asked_dimension_and_unit():
    requirements = deterministic_requirements("长8cm宽5cm安装板", clarification=[{"question": "请补充厚度", "answer": "8mm"}])
    thickness = next(item for item in requirements.requirements if item.axis == "z")
    assert thickness.value == 8 and thickness.source_quote == "8mm"
    assert require_coverage(requirements, plate())["status"] == "passed"


def test_removing_feature_in_a_later_answer_does_not_resurrect_it():
    requirements = deterministic_requirements("长80mm安装板，添加加强筋", clarification=[{"question": "加强筋尺寸？", "answer": "不要加强筋"}])
    assert [(item.kind, item.count) for item in requirements.requirements if item.feature_type == "Rib"] == [("feature_count", 0)]


@pytest.mark.parametrize("text,count", [
    ("均布六个通孔，第一个孔中心在(22,0)", 6),
    ("六个孔，第 1 个孔在(22,0)", 6),
    ("底板有两个直径4mm的通孔，立板另有两个直径4mm的通孔。总计四个孔", 4),
])
def test_ordinals_and_local_groups_do_not_conflict_with_global_count(text, count):
    requirements = deterministic_requirements(text)
    assert [item.count for item in requirements.requirements if item.kind == "hole_count"] == [count]


@pytest.mark.parametrize("text", ["不要附加倒角或圆角", "不增加圆角、倒角或其他特征", "without chamfers or fillets", "no fillets, chamfers"])
def test_negation_covers_coordinated_operations_and_preserves_absence(text):
    requirements = deterministic_requirements("长40mm。" + text)
    assert {(item.feature_type, item.kind, item.count) for item in requirements.requirements if item.feature_type} == {
        ("Fillet", "feature_count", 0), ("Chamfer", "feature_count", 0)}
    assert require_coverage(requirements, compile_ir({"features": [
        {"id": "body", "type": "Box", "params": {"width": 40, "depth": 30, "height": 10}}]}),
        allow_geometry_pending=True)["status"] in {"passed", "unverified"}


def test_positive_command_after_negation_keeps_its_feature():
    requirements = deterministic_requirements("不要圆角，但添加倒角2mm")
    assert any(item.feature_type == "Chamfer" and item.kind == "feature" for item in requirements.requirements)
    assert not any(item.feature_type == "Fillet" and item.kind == "feature" for item in requirements.requirements)


@pytest.mark.parametrize("check", [
    {"kind": "hole_count", "count": 6, "feature_type": "Hole"},
    {"kind": "feature_position", "feature_type": "Box", "position": [0, 0, 0], "parameter": "minimum_corner"},
    {"kind": "feature_dimension", "feature_type": "Box", "parameter": "width", "value": 40, "axis": "x"},
    {"kind": "feature_count", "feature_type": "Circle"},
])
def test_advertised_schema_rejects_the_same_invalid_field_combinations_as_local_validation(check):
    payload = {"requirements": [{"id": "test", "source_quote": "test", **check}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, requirement_response_schema())
    with pytest.raises(ValidationError):
        RequirementSet(raw_request="test", **payload)


def test_invalid_extraction_gets_one_grounded_correction_attempt():
    class CorrectingGateway(MockGateway):
        async def generate(self, **kwargs):
            self.calls.append(kwargs)
            check = {"id": "count", "source_quote": "六个孔", "kind": "hole_count", "count": 6}
            if len(self.calls) == 1:
                check["feature_type"] = "Hole"
            return SimpleNamespace(payload={"requirements": [check]}, model="test")
    gateway = CorrectingGateway(None)
    result = asyncio.run(extract_requirements("六个孔", gateway=gateway))
    assert len(gateway.calls) == 2
    assert "validation_errors" in gateway.calls[1]["request"]
    assert all(item.count == 6 for item in result.requirements if item.kind == "hole_count")


def test_composite_native_specs_bind_same_type_features_and_check_every_array_entry():
    compiled = compile_ir({"features": [
        {"id": "lower", "type": "Circle", "params": {"radius": 10, "origin": [0, 0, 0]}},
        {"id": "middle", "type": "Circle", "params": {"radius": 8, "origin": [0, 0, 15]}},
        {"id": "upper", "type": "Circle", "params": {"radius": 5, "origin": [0, 0, 30]}},
        {"id": "loft", "type": "Loft", "inputs": ["lower", "middle", "upper"], "params": {"ruled": True}},
    ]})
    requirements = RequirementSet(raw_request="three profiles ruled", requirements=[
        {"id": "middle", "source_quote": "three profiles", "kind": "feature_parameters", "feature_type": "Circle",
         "params": {"radius": 8, "origin": [0, 0, 15], "plane": "XY"}},
        {"id": "ruled", "source_quote": "ruled", "kind": "feature_parameters", "feature_type": "Loft", "params": {"ruled": True}},
        {"id": "ordered", "source_quote": "three profiles", "kind": "feature_inputs", "feature_type": "Loft", "inputs": [
            {"feature_type": "Circle", "params": {"radius": 10, "origin": [0, 0, 0]}},
            {"feature_type": "Circle", "params": {"radius": 8, "origin": [0, 0, 15]}},
            {"feature_type": "Circle", "params": {"radius": 5, "origin": [0, 0, 30]}},
        ]},
    ])
    assert require_coverage(requirements, compiled)["status"] == "passed"
    for key, wrong in [("radius", 7), ("origin", [0, 0, 14]), ("plane", "XZ")]:
        mutated = requirements.model_dump()
        mutated["requirements"][0]["params"][key] = wrong
        with pytest.raises(RequirementCoverageError):
            require_coverage(mutated, compiled)
    missing = compiled.source_ir
    missing["features"][-1]["inputs"] = ["lower", "upper"]
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, compile_ir(missing))


def test_native_parameter_schema_rejects_labels_and_invented_box_length():
    for params in ({"label": "compliant"}, {"length": 40}):
        with pytest.raises(ValidationError):
            RequirementSet(raw_request="box", requirements=[{"id": "test", "source_quote": "box", "kind": "feature_parameters",
                "feature_type": "Box", "params": params}])


def test_absence_and_allowlist_also_examine_boolean_tools():
    compiled = compile_ir({"features": [
        {"id": "body", "type": "Box", "params": {"width": 40, "depth": 30, "height": 10}},
        {"id": "tool", "type": "Cylinder", "params": {"radius": 3, "height": 10}},
        {"id": "cut", "type": "Cut", "inputs": ["body", "tool"], "params": {}},
    ]})
    requirements = RequirementSet(raw_request="box only", requirements=[{"id": "only", "source_quote": "box only",
        "kind": "allowed_features", "feature_types": ["Box", "Cut"]}])
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, compiled)


def test_solid_count_waits_for_independent_geometry_and_cannot_use_ir_expectation():
    requirements = RequirementSet(raw_request="one solid", requirements=[{"id": "one", "source_quote": "one solid", "kind": "solid_count", "count": 1}])
    assert require_coverage(requirements, plate(), allow_geometry_pending=True)["status"] == "unverified"
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, plate(), geometry={"solids": 2})
    assert require_coverage(requirements, plate(), geometry={"solids": 1})["status"] == "passed"


def test_spline_path_is_not_a_closed_bspline_profile_obligation():
    requirements = deterministic_requirements("路径采用穿过四点的三维插值样条，沿路径扫掠")
    assert any(item.feature_type == "Path3D" for item in requirements.requirements)
    assert not any(item.feature_type == "BSpline" for item in requirements.requirements)


def test_spatial_native_match_cannot_certify_a_displaced_body():
    compiled = compile_ir({"features": [
        {"id": "body", "type": "Cylinder", "params": {"radius": 10, "height": 12}},
        {"id": "moved", "type": "Transform", "inputs": ["body"], "params": {"translation": [10, 0, 0]}},
    ]})
    requirements = RequirementSet(raw_request="origin", requirements=[{"id": "origin", "source_quote": "origin", "kind": "feature_parameters",
        "feature_type": "Cylinder", "params": {"origin": [0, 0, 0]}}])
    with pytest.raises(RequirementCoverageError) as error:
        require_coverage(requirements, compiled, allow_geometry_pending=True)
    assert error.value.details["requirement_coverage"]["status"] == "unverified"


def test_assembly_count_requires_real_identity_mapping_and_measured_components():
    compiled = compile_ir({"features": [
        {"id": "a", "type": "Cylinder", "params": {"radius": 5, "height": 5}},
        {"id": "b", "type": "Cylinder", "params": {"radius": 3, "height": 5, "origin": [20, 0, 0]}},
    ], "assembly": {"parts": [{"id": "pa", "name": "a", "feature_id": "a"}, {"id": "pb", "name": "b", "feature_id": "b"}],
                     "instances": [{"id": "ia", "part_id": "pa"}, {"id": "ib", "part_id": "pb"}]}})
    requirements = RequirementSet(raw_request="two parts once each", requirements=[{"id": "parts", "source_quote": "two parts once each", "kind": "assembly_count", "count": 2}])
    assert require_coverage(requirements, compiled, allow_geometry_pending=True)["status"] == "unverified"
    assert require_coverage(requirements, compiled, geometry={"assembly": {"component_count": 2}})["status"] == "passed"
    compiled.assembly["instances"][1]["part_id"] = "pa"
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, compiled, geometry={"assembly": {"component_count": 2}})


def test_independent_extraction_has_its_own_role_schema_and_cannot_omit_mandatory_rib():
    gateway = MockGateway({"requirements": [{"id": "size", "source_quote": "长80mm", "kind": "body_dimension", "axis": "x", "value": 80}]})
    requirements = asyncio.run(extract_requirements("长80mm安装板，添加加强筋", gateway=gateway))
    assert gateway.calls[0]["role"] == "requirements"
    assert requirements.extraction_mode == "independent-model"
    assert any(item.feature_type == "Rib" for item in requirements.requirements)
    jsonschema.Draft202012Validator.check_schema(requirement_response_schema())
    jsonschema.validate(gateway.payload, requirement_response_schema())


def test_independent_extractor_cannot_claim_invented_source_or_question_as_source():
    payload = {"requirements": [{"id": "rib", "source_quote": "添加加强筋", "kind": "feature", "feature_type": "Rib"}]}
    with pytest.raises(SchemaValidationError):
        asyncio.run(extract_requirements("长80mm安装板", clarification=[{"question": "添加加强筋？", "answer": "厚8mm"}], gateway=MockGateway(payload)))
    with pytest.raises(ValidationError):
        RequirementSet(raw_request="长80mm安装板", requirements=payload["requirements"])


def test_model_clarification_does_not_become_a_generation_coverage_error():
    with pytest.raises(RequirementError, match="Which path"):
        asyncio.run(extract_requirements("Sweep a tube", gateway=MockGateway({"clarification": "Which path and tube radii?"})))


def test_separate_extractor_cannot_revive_old_count_after_explicit_resolution():
    original = "长80mm安装板，四个孔，五个孔"
    gateway = MockGateway({"requirements": [{"id": "old", "source_quote": "五个孔", "kind": "hole_count", "count": 5}]})
    result = asyncio.run(extract_requirements(original, clarification=[{"question": "最终孔数量？", "answer": "四个孔"}], gateway=gateway))
    assert [item.count for item in result.requirements if item.kind == "hole_count"] == [4]


def test_hole_diameter_does_not_capture_a_cylindrical_boss_in_another_clause():
    result = deterministic_requirements("长80mm安装板，四个直径6mm通孔，圆柱凸起直径20mm高5mm")
    assert [item.value for item in result.requirements if item.kind == "hole_diameter"] == [6]


def test_explicit_native_dimensions_are_mandatory_even_when_model_extractor_omits_them():
    gateway = MockGateway({"requirements": [{"id": "thread", "source_quote": "螺纹", "kind": "feature", "feature_type": "HelicalThread"}]})
    requirements = asyncio.run(extract_requirements("螺纹大径20mm，小径18mm，螺距2mm，螺纹长度16mm", gateway=gateway))
    values = {item.parameter: item.value for item in requirements.requirements if item.kind == "feature_dimension"}
    assert values == {"major_diameter": 20, "minor_diameter": 18, "pitch": 2, "length": 16}


def test_cylinder_diameter_and_height_bind_to_actual_parameters_without_self_coverage():
    requirements = deterministic_requirements("圆柱直径20mm，圆柱高度30mm")
    compiled = compile_ir({"features": [{"id": "cylinder", "type": "Cylinder", "params": {"radius": 10, "height": 30}}]})
    assert require_coverage(requirements, compiled)["status"] == "passed"
    wrong = compile_ir({"features": [{"id": "cylinder", "type": "Cylinder", "params": {"radius": 8, "height": 30}}]})
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, wrong)


def test_extractor_omission_cannot_drop_explicit_cylinder_height():
    gateway = MockGateway({"requirements": [{"id": "body", "source_quote": "圆柱", "kind": "feature", "feature_type": "Cylinder"}]})
    requirements = asyncio.run(extract_requirements("圆柱直径20mm，圆柱高度30mm", gateway=gateway))
    values = {item.parameter: item.value for item in requirements.requirements if item.kind == "feature_dimension"}
    assert values == {"radius": 10, "height": 30}
    wrong_height = compile_ir({"features": [{"id": "body", "type": "Cylinder", "params": {"radius": 10, "height": 20}}]})
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, wrong_height)


def test_native_thread_length_is_not_misclassified_as_body_x_size():
    requirements = deterministic_requirements("thread length 16mm and thread pitch 2mm")
    assert not any(item.kind == "body_dimension" for item in requirements.requirements)
    values = {item.parameter: item.value for item in requirements.requirements if item.kind == "feature_dimension"}
    assert values == {"length": 16, "pitch": 2}


def test_local_shaft_length_in_assembly_is_not_the_global_x_envelope():
    requirements = deterministic_requirements("三个零件组成装配。零件一是法兰，沿Z轴有直径10的通孔。零件二是实心轴，直径8、长度30，沿Z正方向。")
    assert not any(item.kind == "body_dimension" for item in requirements.requirements)
    assert any(item.kind == "hole_through" for item in requirements.requirements)


@pytest.mark.parametrize("constructed_hole", [False, True])
def test_explicit_zero_holes_resolves_conflict_and_retires_old_hole_obligations(constructed_hole):
    original = "长80mm宽50mm厚8mm安装板，四个通孔，五个通孔，孔径6mm，水平孔距60mm，垂直孔距30mm"
    gateway = MockGateway({"requirements": [
        {"id": "old_holes", "source_quote": "四个通孔", "kind": "feature", "feature_type": "Hole"},
        {"id": "old_diameter", "source_quote": "孔径6mm", "kind": "hole_diameter", "value": 6},
        {"id": "old_count", "source_quote": "五个通孔", "kind": "hole_count", "count": 5},
    ]})
    requirements = asyncio.run(extract_requirements(original, clarification=[{"question": "孔数量冲突，请明确几个孔？", "answer": "零个孔"}], gateway=gateway))
    hole_checks = [item for item in requirements.requirements if item.kind.startswith("hole_") or item.feature_type == "Hole"]
    assert len(hole_checks) == 1 and hole_checks[0].kind == "hole_count" and hole_checks[0].count == 0
    raw = {"features": [{"id": "plate", "type": "Box", "params": {"width": 80, "depth": 50, "height": 8}}],
           "expected_geometry": {"bbox": [80, 50, 8]}}
    if constructed_hole:
        raw["features"].extend([
            {"id": "tool", "type": "Cylinder", "params": {"radius": 3, "height": 8, "origin": [40, 25, 0]}},
            {"id": "cut", "type": "Cut", "inputs": ["plate", "tool"], "params": {}},
        ])
    compiled = compile_ir(raw)
    if constructed_hole:
        with pytest.raises(RequirementCoverageError) as failure:
            require_coverage(requirements, compiled)
        assert failure.value.details["requirement_coverage"]["status"] == "unverified"
    else:
        assert require_coverage(requirements, compiled)["status"] == "passed"


def test_single_hole_diameter_question_binds_bare_length_with_explicit_units():
    requirements = deterministic_requirements("长80mm宽50mm厚8mm安装板，四个通孔", clarification=[{"question": "请补充孔径及单位", "answer": "0.6cm"}])
    diameter = next(item for item in requirements.requirements if item.kind == "hole_diameter")
    assert diameter.value == 6 and diameter.source_index == 1 and diameter.source_quote == "0.6cm"
    assert require_coverage(requirements, plate())["status"] == "passed"


def test_multi_round_bare_longitudinal_pitch_is_preserved_as_independent_obligation():
    original = "长80mm宽50mm安装板，四个通孔，五个通孔，孔径6mm，水平孔距60mm"
    turns = [{"question": "孔数量冲突，最终几个孔？", "answer": "四个孔"},
             {"question": "请补充厚度", "answer": "8mm"},
             {"question": "请补充纵向孔距", "answer": "3cm"}]
    requirements = deterministic_requirements(original, clarification=turns)
    pitch = next(item for item in requirements.requirements if item.kind == "hole_pitch" and item.axis == "y")
    assert pitch.value == 30 and pitch.source_index == 3 and pitch.source_quote == "3cm"
    assert require_coverage(requirements, plate())["status"] == "passed"
    wrong = plate().source_ir
    wrong["features"][1]["params"]["positions"] = [[10, 10, 0], [10, 30, 0], [70, 10, 0], [70, 30, 0]]
    with pytest.raises(RequirementCoverageError):
        require_coverage(requirements, compile_ir(wrong))


def test_mixed_hole_dimension_questions_do_not_bind_one_bare_answer():
    for question in ("请补充孔径和纵向孔距", "请补充水平孔距和垂直孔距", "请补充孔径和厚度", "请补充孔径和孔距"):
        requirements = deterministic_requirements("长80mm安装板，四个通孔", clarification=[{"question": question, "answer": "30mm"}])
        assert not any(item.source_index == 1 for item in requirements.requirements)


@pytest.mark.parametrize("question", ["孔数和孔径是多少？", "孔径及孔的位置？"])
def test_count_or_position_with_diameter_cannot_bind_a_bare_length(question):
    requirements = deterministic_requirements("长80mm安装板，四个通孔", clarification=[{"question": question, "answer": "30mm"}])
    assert not any(item.source_index == 1 for item in requirements.requirements)
