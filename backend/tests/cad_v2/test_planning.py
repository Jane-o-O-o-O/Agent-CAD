import asyncio
import json

import httpx
import pytest

from app.application.services.cad_v2_planner import edit_prompt, plan
from app.domain.cad_v2.compiler import apply_edits, compile_ir
from app.domain.cad_v2.errors import InfrastructureError, RequirementError, SchemaValidationError
from app.infrastructure.cad_v2.model_gateway import ModelConfig, StructuredModelGateway

PROMPT = "生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。"


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
    with pytest.raises(RequirementError):
        asyncio.run(plan("做一个L型支架", "complex"))


def test_parser_rejects_mixed_units_and_unconsumed_design_intent(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("CAD_V2_API_KEY", raising=False)
    for prompt in ["长80 mm、宽5 cm、厚8 mm安装板", "长80宽50厚8安装板，中央添加直径10mm高5mm的圆柱凸起"]:
        with pytest.raises(RequirementError):
            asyncio.run(plan(prompt, "strict"))
    ir = asyncio.run(plan("80x50x8 m plate", "meters"))
    assert compile_ir(ir).expected_geometry["bbox"] == [80000, 50000, 8000]


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
