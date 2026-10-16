import asyncio
import json
import math
from types import SimpleNamespace

import httpx
import jsonschema
import pytest

from app.application.services.cad_v2_planner import _conversation, edit_prompt, plan, plan_edit
from app.domain.cad_v2.compiler import apply_edits, compile_ir
from app.domain.cad_v2.expressions import evaluate_expression, expression_dependencies
from app.domain.cad_v2.errors import InfrastructureError, RequirementCoverageError, RequirementError, SchemaValidationError
from app.infrastructure.cad_v2.model_gateway import ModelConfig, StructuredModelGateway

PROMPT = "生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。"


def test_builtin_pi_is_numeric_and_not_an_unresolved_parameter():
    assert expression_dependencies("pi*r**2*h") == {"r", "h"}
    assert evaluate_expression("pi*r**2*h", {"r": 3, "h": 8}) == pytest.approx(math.pi * 72)


def test_acceptance_requirement_and_edit_preserve_envelope():
    ir = asyncio.run(plan(PROMPT, "acceptance-plate"))
    compiled = compile_ir(ir)
    assert compiled.expected_geometry["bbox"] == [80, 50, 8]
    assert sorted(compiled.features[1].params["positions"]) == [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]]
    patch = asyncio.run(edit_prompt("将四个孔的直径改成 8 mm，其他保持不变。", ir))
    changed = compile_ir(apply_edits(ir, parameters=patch))
    assert changed.features[1].params["diameter"] == 8
    assert changed.features[0] == compiled.features[0]
    assert changed.features[1].params["positions"] == compiled.features[1].params["positions"]
    assert changed.expected_geometry["holes"][0]["diameter"] == 8
    assert ir.parameters["hole_diameter"].value == 6


@pytest.mark.parametrize("prompt", ["生成安装板", "长80宽50厚8的安装板，四个直径6的通孔", "长80宽50厚8安装板，四个直径6的孔，水平距离60，垂直距离30"])
def test_missing_requirements_are_not_invented(prompt):
    with pytest.raises(RequirementError):
        asyncio.run(plan(prompt, "missing"))


def test_unconfigured_complex_planner_is_explicit(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("CAD_V2_API_KEY", raising=False)
    with pytest.raises(InfrastructureError):
        asyncio.run(plan("做一个L型支架", "complex"))


def test_parser_converts_explicit_units_and_preserves_unconsumed_design_intent(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("CAD_V2_API_KEY", raising=False)
    ir = asyncio.run(plan("长80 mm、宽5 cm、厚8 mm安装板", "mixed"))
    assert compile_ir(ir).expected_geometry["bbox"] == [80, 50, 8]
    with pytest.raises(InfrastructureError):
        asyncio.run(plan("长80宽50厚8安装板，中央添加直径10mm高5mm的圆柱凸起", "strict"))
    ir = asyncio.run(plan("80x50x8 m plate", "meters"))
    assert compile_ir(ir).expected_geometry["bbox"] == [80000, 50000, 8000]


def test_incomplete_plate_clarification_merges_multiple_answers_without_losing_holes():
    prompt = "生成安装板，长80mm，宽50mm，在四角开四个通孔，孔径6mm，水平孔距60mm"
    with pytest.raises(RequirementError) as first:
        asyncio.run(plan(prompt, "clarified-plate"))
    assert first.value.details["missing"] == ["thickness", "pitch_y"]
    turns = [{"question": first.value.message, "answer": "厚8mm"}]
    with pytest.raises(RequirementError) as second:
        asyncio.run(plan(prompt, "clarified-plate", clarification=turns))
    assert second.value.details["missing"] == ["pitch_y"]
    turns.append({"question": second.value.message, "answer": "垂直孔距30mm"})
    compiled = compile_ir(asyncio.run(plan(prompt, "clarified-plate", clarification=turns)))
    assert compiled.expected_geometry["bbox"] == [80, 50, 8]
    assert compiled.features[1].params["diameter"] == 6
    assert compiled.features[1].params["positions"] == [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]]


def test_four_hole_pitch_does_not_invent_pattern_placement():
    prompt = "长80mm宽50mm厚8mm安装板，四个直径6mm通孔，水平孔距60mm，垂直孔距30mm"
    with pytest.raises(RequirementError) as question:
        asyncio.run(plan(prompt, "placement"))
    assert question.value.details["missing"] == ["hole_placement"]
    assert "坐标/边距" in question.value.message
    ir = asyncio.run(plan(prompt, "placement", clarification=[{"question": question.value.message, "answer": "居中对称"}]))
    assert compile_ir(ir).features[1].params["positions"] == [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]]


def test_single_missing_dimension_accepts_bare_answer_and_converts_units():
    prompt = "长8cm宽5cm安装板"
    with pytest.raises(RequirementError) as question:
        asyncio.run(plan(prompt, "units-answer"))
    ir = asyncio.run(plan(prompt, "units-answer", clarification=[{"question": question.value.message, "answer": "8mm"}]))
    assert compile_ir(ir).expected_geometry["bbox"] == [80, 50, 8]


def test_conflicting_dimension_requires_resolution_and_answers_cannot_drop_extra_features(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("CAD_V2_API_KEY", raising=False)
    prompt = "长80mm长90mm宽50mm厚8mm安装板"
    with pytest.raises(RequirementError) as question:
        asyncio.run(plan(prompt, "conflict"))
    ir = asyncio.run(plan(prompt, "conflict", clarification=[{"question": question.value.message, "answer": "长度80mm"}]))
    assert compile_ir(ir).expected_geometry["bbox"] == [80, 50, 8]
    with pytest.raises(InfrastructureError):
        asyncio.run(plan("长80mm宽50mm安装板", "intent", clarification=[{
            "question": "请补充厚度", "answer": "厚8mm，再添加加强筋"}]))


def test_offline_diameter_edit_asks_target_then_amount_and_preserves_plate():
    original = asyncio.run(plan(PROMPT, "edit-answer"))
    prompt = "把孔的直径改大"
    with pytest.raises(RequirementError) as first:
        asyncio.run(plan_edit(prompt, original))
    turns = [{"question": first.value.message, "answer": "左边两个孔"}]
    with pytest.raises(RequirementError) as second:
        asyncio.run(plan_edit(prompt, original, clarification=turns))
    turns.append({"question": second.value.message, "answer": "8mm"})
    edit = asyncio.run(plan_edit(prompt, original, clarification=turns))
    changed = compile_ir(apply_edits(original, **edit.apply_kwargs()))
    assert changed.features[0] == compile_ir(original).features[0]
    assert changed.features[-1].params["diameter"] == 8
    assert changed.features[-1].params["positions"] == [[10, 10, 0], [10, 40, 0]]


def test_structured_planner_receives_original_requirement_and_all_answers(monkeypatch):
    monkeypatch.setenv("CAD_V2_API_KEY", "test-only")
    calls = []
    payload = asyncio.run(plan(PROMPT, "context")).model_dump(mode="json")
    async def generate(_self, **kwargs):
        calls.append(kwargs)
        if kwargs["role"] == "requirements":
            return SimpleNamespace(payload={"requirements": [{"id": "holes", "kind": "feature", "feature_type": "Hole",
                                                              "source_quote": "安装孔"}]}, model="test")
        return SimpleNamespace(payload=payload, model="test", usage={}, elapsed_seconds=0, attempts=1)
    monkeypatch.setattr("app.application.services.cad_v2_planner.StructuredModelGateway.generate", generate)
    turns = [{"question": "支架厚度？", "answer": "8mm"}, {"question": "安装孔位置？", "answer": "距两边10mm"}]
    ir = asyncio.run(plan("做一个带安装孔的支架", "context", clarification=turns))
    assert "做一个带安装孔的支架" in calls[0]["request"]
    assert all(turn[key] in calls[0]["request"] for turn in turns for key in ("question", "answer"))
    assert ir.metadata["clarification"] == turns
    assert [call["role"] for call in calls] == ["requirements", "planner"]


def test_empty_clarification_context_preserves_complete_original_request():
    prompt = "x" * 16000
    assert _conversation(prompt, None) == prompt
    assert _conversation(prompt, []) == prompt


def test_model_response_schemas_accept_questions_and_resolve_success_references(monkeypatch):
    monkeypatch.setenv("CAD_V2_API_KEY", "test-only")
    original = asyncio.run(plan(PROMPT, "schema-response"))
    calls = []
    question = {"clarification": "请补充厚度"}
    async def generate(_self, **kwargs):
        calls.append(kwargs)
        schema = kwargs["schema"]
        validator = jsonschema.Draft202012Validator(schema)
        validator.check_schema(schema)
        validator.validate(question)
        if kwargs["role"] == "requirements":
            validator.validate({"requirements": [{"id": "body", "kind": "body_dimension", "axis": "z",
                                                   "value": 8, "source_quote": "支架"}]})
        elif kwargs["role"] == "planner":
            validator.validate(original.model_dump(mode="json"))
            assert not validator.is_valid({**question, "features": []})
        else:
            edit = {
                "clarification": None, "parameters": {"hole_diameter": 8},
                "intents": [{"source": "Change selected hole diameter", "action": "modify", "targets": ["mounting_holes"], "parameters": ["hole_diameter"]}],
            }
            validator.validate(edit)
            assert not validator.is_valid({**edit, **question})
        return SimpleNamespace(payload=question)
    monkeypatch.setattr("app.application.services.cad_v2_planner.StructuredModelGateway.generate", generate)
    with pytest.raises(RequirementError, match="厚度"):
        asyncio.run(plan("做一个支架", "schema-response"))
    with pytest.raises(RequirementError, match="厚度"):
        asyncio.run(plan_edit("Change selected hole diameter", original))
    assert [call["role"] for call in calls] == ["requirements", "editor"]


@pytest.mark.parametrize("corrected", [True, False])
def test_omitted_holes_are_replanned_once_and_never_silently_published(monkeypatch, corrected):
    monkeypatch.setenv("CAD_V2_API_KEY", "test-only")
    good = asyncio.run(plan(PROMPT, "coverage")).model_dump(mode="json")
    bad = {**good, "features": good["features"][:1], "output_feature": "plate",
           "expected_geometry": {"bbox": [80, 50, 8], "volume": 32000, "solids": 1}}
    calls = []
    async def generate(_self, **kwargs):
        calls.append(kwargs)
        if kwargs["role"] == "requirements":
            payload = {"requirements": [{"id": "holes", "kind": "feature", "feature_type": "Hole", "source_quote": "通孔"}]}
        else:
            payload = good if corrected and len(calls) == 3 else bad
        return SimpleNamespace(payload=payload, model="test", usage={}, elapsed_seconds=0, attempts=1)
    monkeypatch.setattr("app.application.services.cad_v2_planner.StructuredModelGateway.generate", generate)
    prompt = "创建带四个通孔的支架，孔径6mm"
    if corrected:
        ir = asyncio.run(plan(prompt, "coverage"))
        assert ir.metadata["coverage_replans"] == 1
        assert len(compile_ir(ir).features[1].params["positions"]) == 4
    else:
        with pytest.raises(RequirementCoverageError):
            asyncio.run(plan(prompt, "coverage"))
    assert [call["role"] for call in calls] == ["requirements", "planner", "planner"]
    assert "coverage_failures" in calls[-1]["request"]


@pytest.mark.parametrize("payload", [{"clarification": 42}, {"clarification": ""}, {"clarification": "厚度？", "features": []}])
def test_malformed_model_clarification_is_schema_error(monkeypatch, payload):
    monkeypatch.setenv("CAD_V2_API_KEY", "test-only")
    async def generate(_self, **_kwargs):
        return SimpleNamespace(payload=payload)
    monkeypatch.setattr("app.application.services.cad_v2_planner.StructuredModelGateway.generate", generate)
    with pytest.raises(SchemaValidationError):
        asyncio.run(plan("做一个支架", "bad-model"))
    original = asyncio.run(plan(PROMPT, "bad-edit"))
    with pytest.raises(SchemaValidationError):
        asyncio.run(plan_edit("增加一个加强筋", original))


def test_edit_does_not_change_a_subset_or_ignore_units():
    ir = asyncio.run(plan(PROMPT, "edit"))
    with pytest.raises(RequirementError):
        asyncio.run(edit_prompt("将左边两个孔的直径改成8mm", ir))
    patch = asyncio.run(edit_prompt("将四个孔的直径改成0.8 cm，其他保持不变", ir))
    assert patch == {"hole_diameter": 8}


def test_gateway_retries_only_recoverable_provider_errors():
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"features":[]}'}}], "usage": {"total_tokens": 9}})
    gateway = StructuredModelGateway(ModelConfig("https://example.invalid/v1", "test-only", "test", retries=1), transport=httpx.MockTransport(respond))
    result = asyncio.run(gateway.generate(role="planner", instruction="JSON", request="part", schema={}))
    assert result.attempts == 2 and result.usage == {"total_tokens": 9}
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 400])
def test_gateway_does_not_leak_provider_errors_or_retry_auth(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="secret provider payload")
    gateway = StructuredModelGateway(ModelConfig("https://example.invalid/v1", "test-only", "test"), transport=httpx.MockTransport(respond))
    with pytest.raises(InfrastructureError) as error:
        asyncio.run(gateway.generate(role="planner", instruction="JSON", request="part", schema={}))
    assert len(calls) == 1 and "secret" not in str(error.value.to_dict())


def test_gateway_rejects_non_json_output():
    gateway = StructuredModelGateway(ModelConfig("https://example.invalid/v1", "test-only", "test"), transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "import os"}}]})))
    with pytest.raises(SchemaValidationError):
        asyncio.run(gateway.generate(role="planner", instruction="JSON", request="part", schema={}))


@pytest.mark.parametrize("recover", [True, False])
def test_gateway_bounds_remote_protocol_disconnect_retries(recover):
    calls = []
    def respond(request):
        calls.append(request)
        if not recover or len(calls) == 1:
            raise httpx.RemoteProtocolError("upstream disconnected", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{}'}}]})
    gateway = StructuredModelGateway(ModelConfig("https://example.invalid/v1", "test-only", "test", retries=1),
                                     transport=httpx.MockTransport(respond))
    if recover:
        assert asyncio.run(gateway.generate(role="requirements", instruction="JSON", request="part", schema={})).attempts == 2
    else:
        with pytest.raises(InfrastructureError) as error:
            asyncio.run(gateway.generate(role="requirements", instruction="JSON", request="part", schema={}))
        assert error.value.details == {"attempts": 2}
    assert len(calls) == 2


def test_planner_corrects_compilation_budget_once_without_raising_the_budget(monkeypatch):
    from app.domain.cad_v2.errors import ParameterError
    prompt = "制作实心垫块，长40mm宽30mm厚10mm"
    plans = []
    async def generate(self, **kwargs):
        if kwargs["role"] == "requirements":
            return SimpleNamespace(payload={"requirements": [{"id": "body", "source_quote": "实心垫块", "kind": "feature", "feature_type": "Box"}]}, model="test")
        plans.append(kwargs)
        payload = {"features": [{"id": "body", "type": "Box", "params": {"width": 40, "depth": 30, "height": 10}}],
                   "expected_geometry": {"bbox": [40, 30, 10], "volume": "+".join(["1"] * 25) if len(plans) == 1 else 12000}}
        return SimpleNamespace(payload=payload, model="test", usage={}, elapsed_seconds=0, attempts=1)
    monkeypatch.setenv("CAD_V2_API_KEY", "test-only")
    monkeypatch.setattr(StructuredModelGateway, "generate", generate)
    compiled = compile_ir(asyncio.run(plan(prompt, "budget-correction")))
    assert len(plans) == 2 and "compilation_error" in plans[1]["request"]
    assert compiled.expected_geometry["volume"] == 12000
    with pytest.raises(ParameterError, match="complexity budget"):
        evaluate_expression("+".join(["1"] * 25), {})
