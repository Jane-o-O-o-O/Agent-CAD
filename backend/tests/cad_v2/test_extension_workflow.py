"""Service-level extension workflows with real isolated CAD worker processes.

Run with CAD_V2_ISOLATION=process-dev in development containers lacking namespace
support. Strict isolation and fail-closed behavior have their own tests.
Only structured-model responses are mocked; every published revision uses OCP.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from build123d import import_step

from app.application.services.cad_v2_planner import plan_edit
from app.application.services.cad_v2_service import CADV2Service
from app.infrastructure.cad_v2.store import StoreNotFound
from app.infrastructure.cad_v2.validation import cylindrical_surfaces

from .test_assembly import flange_shaft_spacer_ir

OWNER = "extension-owner"
PROMPT = "生成一块长80mm宽50mm厚8mm的安装板，在四角开四个直径6mm的通孔，孔中心的水平距离为60mm，垂直距离为30mm。"


async def completed(service, job):
    await service.wait(job["id"])
    stored = service.store.get_job(job["id"], OWNER)
    assert stored["status"] == "completed", stored
    revision = service.store.get_revision(stored["document_id"], stored["revision_id"], OWNER)
    assert revision["report"]["validation"]["status"] == "passed"
    assert revision["report"]["geometry"]["step_roundtrip"]["valid"]
    return stored, revision


def bore_profiles(service, revision):
    path, manifest = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, "step")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
    shape = import_step(path)
    assert shape.is_valid and len(shape.solids()) == 1
    assert list(shape.bounding_box().size) == pytest.approx([80, 50, 8])
    return {(round(surface["origin"][0], 6), round(surface["origin"][1], 6)): round(surface["radius"] * 2, 6)
            for surface in cylindrical_surfaces(shape) if surface["inward"]}


@pytest.mark.asyncio
async def test_service_natural_subset_multiple_operations_preserve_independent_properties(tmp_path):
    service = CADV2Service(tmp_path, max_concurrency=1)
    try:
        _, first = await completed(service, await service.create(OWNER, prompt=PROMPT))
        original = bore_profiles(service, first)
        assert original == {(10, 10): 6, (10, 40): 6, (70, 10): 6, (70, 40): 6}
        _, edited = await completed(service, await service.edit(
            OWNER, first["document_id"], expected_revision=first["id"],
            prompt="将左边两个孔的直径改成8mm，并将右边两个孔向左移动5mm，其他保持不变。"))
        actual = bore_profiles(service, edited)
        # Left positions stay put; right diameters stay at six. The exported B-Rep
        # independently verifies the intent instead of trusting the planned IR.
        assert actual == {(10, 10): 8, (10, 40): 8, (65, 10): 6, (65, 40): 6}
        assert edited["ir"]["features"][0] == first["ir"]["features"][0]
        assert edited["ir"]["parameters"] == first["ir"]["parameters"]
        assert edited["ir"]["constraints"] == first["ir"]["constraints"]
        assert edited["ir"]["expected_geometry"]["bbox"] == first["ir"]["expected_geometry"]["bbox"]
        assert len(edited["ir"]["metadata"]["last_edit"]["intents"]) == 2
        assert edited["parent_revision"] == first["id"]
        assert bore_profiles(service, first) == original
        assert len(service.store.list_revisions(first["document_id"], OWNER)) == 2
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_service_clarification_resumes_original_multi_edit_and_rejects_wrong_owner_or_base(tmp_path, monkeypatch):
    original_prompt = "将左边两个孔的直径改成新尺寸，并将右边两个孔向左移动5mm"
    question = "左边两个孔需要多大直径？"
    answer = "将左边两个孔的直径改成8mm"
    requests = []
    resolved_payload = None
    monkeypatch.setenv("CAD_V2_API_KEY", "mock-only")

    async def generate(_self, **kwargs):
        request = json.loads(kwargs["request"])
        requests.append(request)
        payload = {"clarification": question} if len(requests) == 1 else resolved_payload
        assert payload is not None
        return SimpleNamespace(payload=payload, model="mock-editor", usage={"total_tokens": 10})

    monkeypatch.setattr("app.application.services.cad_v2_planner.StructuredModelGateway.generate", generate)
    service = CADV2Service(tmp_path, max_concurrency=1)
    try:
        _, first = await completed(service, await service.create(OWNER, prompt=PROMPT))
        failure = await service.edit(OWNER, first["document_id"], expected_revision=first["id"], prompt=original_prompt)
        await service.wait(failure["id"])
        failure = service.store.get_job(failure["id"], OWNER)
        assert failure["status"] == "failed"
        assert failure["error"]["code"] == "RequirementError"
        assert failure["error"]["details"]["clarification"] == question
        assert service.store.get_document(first["document_id"], OWNER)["current_revision"] == first["id"]
        assert len(service.store.list_revisions(first["document_id"], OWNER)) == 1
        with pytest.raises(StoreNotFound):
            await service.edit("another-owner", first["document_id"], expected_revision=first["id"],
                               prompt=answer, clarification_job_id=failure["id"])
        resolved = await plan_edit(answer + "，并将右边两个孔向左移动5mm", first["ir"])
        resolved_payload = resolved.model_dump(mode="json")
        _, edited = await completed(service, await service.edit(
            OWNER, first["document_id"], expected_revision=first["id"],
            prompt=answer, clarification_job_id=failure["id"]))
        assert len(requests) == 2
        composed = requests[1]["edit_request"]
        assert original_prompt in composed and question in composed and answer in composed
        assert requests[1]["current_ir"]["document_id"] == first["document_id"]
        assert bore_profiles(service, edited) == {(10, 10): 8, (10, 40): 8, (65, 10): 6, (65, 40): 6}
        assert edited["ir"]["metadata"]["last_edit"]["model"] == "mock-editor"
        with pytest.raises(ValueError, match="same revision"):
            await service.edit(OWNER, first["document_id"], expected_revision=edited["id"],
                               prompt=answer, clarification_job_id=failure["id"])
        assert len(service.store.list_revisions(first["document_id"], OWNER)) == 2
    finally:
        await service.shutdown()


def read_bom(service, revision):
    path, manifest = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, "bom_json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
    csv_path, csv_manifest = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, "bom_csv")
    assert csv_path.stat().st_size == csv_manifest["size"]
    return json.loads(path.read_text())


@pytest.mark.asyncio
async def test_service_assembly_instance_edits_bom_and_restore_survive_restart(tmp_path):
    service = CADV2Service(tmp_path, max_concurrency=1)
    first = edited = None
    try:
        _, first = await completed(service, await service.create(OWNER, ir=flange_shaft_spacer_ir()))
        initial_bom = read_bom(service, first)
        assert initial_bom["component_count"] == 3
        old_step, old_manifest = service.store.artifact_location(first["document_id"], first["id"], OWNER, "step")
        initial_hash = hashlib.sha256(old_step.read_bytes()).hexdigest()
        _, edited = await completed(service, await service.edit(
            OWNER, first["document_id"], expected_revision=first["id"],
            instance_patches={"shaft_1": {"transform": {"translation": [0, 0, -7]}},
                              "spacer_1": {"transform": {"rotation": [0, 0, 45]}}},
            add_instances=[{"id": "spacer_2", "part_id": "spacer_part", "transform": {"translation": [0, 0, 10]}}]))
        assert edited["ir"]["assembly"]["parts"] == first["ir"]["assembly"]["parts"]
        assert edited["ir"]["assembly"]["mates"] == first["ir"]["assembly"]["mates"]
        assert edited["ir"]["assembly"]["instances"][0] == first["ir"]["assembly"]["instances"][0]
        bom = read_bom(service, edited)
        assert bom["component_count"] == 4
        spacer = next(part for part in bom["bom"] if part["part_id"] == "spacer_part")
        assert spacer["quantity"] == 2 and spacer["instance_ids"] == ["spacer_1", "spacer_2"]
        step, _ = service.store.artifact_location(edited["document_id"], edited["id"], OWNER, "step")
        reimported = import_step(step)
        assert len(reimported.solids()) == 4
        shaft = next(child for child in reimported.children if child.label == "shaft_1")
        assert list(shaft.bounding_box().min) == pytest.approx([-4.8, -4.8, -7])
        assert hashlib.sha256(old_step.read_bytes()).hexdigest() == initial_hash == old_manifest["sha256"]
    finally:
        await service.shutdown()

    reopened = CADV2Service(tmp_path, max_concurrency=1)
    try:
        await reopened.start()
        assert reopened.store.get_document(first["document_id"], OWNER)["current_revision"] == edited["id"]
        assert read_bom(reopened, edited)["component_count"] == 4
        _, restored = await completed(reopened, await reopened.restore(
            OWNER, first["document_id"], revision_id=first["id"], expected_revision=edited["id"]))
        assert restored["id"] not in {first["id"], edited["id"]}
        assert restored["parent_revision"] == edited["id"]
        assert restored["ir"]["assembly"] == first["ir"]["assembly"]
        assert read_bom(reopened, restored)["component_count"] == 3
        assert len(reopened.store.list_revisions(first["document_id"], OWNER)) == 3
        assert read_bom(reopened, edited)["component_count"] == 4
    finally:
        await reopened.shutdown()

    final_open = CADV2Service(tmp_path)
    try:
        await final_open.start()
        assert final_open.store.get_document(first["document_id"], OWNER)["current_revision"] == restored["id"]
        assert read_bom(final_open, restored)["component_count"] == 3
    finally:
        await final_open.shutdown()
