"""Durable question/answer workflow; FakeExecutor checks publication, not solids."""
import json

import httpx
import pytest

from app.application.services.cad_v2_service import CADV2Service
from app.domain.cad_v2.compiler import compile_ir
from app.domain.cad_v2.errors import InfrastructureError, RequirementError
from app.interfaces.cad_v2_app import create_app
from app.infrastructure.cad_v2.store import CADStore, StoreConflict, StoreNotFound
from .test_service import FakeExecutor, sample_ir


INCOMPLETE = "生成安装板，长80mm，宽50mm，在四角开四个通孔，孔径6mm，水平孔距60mm"
COMPLETE = INCOMPLETE + "，厚8mm，垂直孔距30mm"


async def settled(service, job, owner="alice"):
    await service.wait(job["id"])
    return service.store.get_job(job["id"], owner)


@pytest.mark.asyncio
async def test_partial_answers_keep_draft_and_original_requirements_across_restart(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    first = await settled(service, await service.create("alice", prompt=INCOMPLETE))
    assert first["status"] == "awaiting_input", first
    assert first["error"]["code"] == "RequirementError"
    assert executor.calls == 0
    assert service.store.list_revisions(first["document_id"], "alice") == []
    await service.shutdown()

    service = CADV2Service(tmp_path, executor=executor)
    try:
        await service.start()
        assert service.store.get_job(first["id"], "alice")["status"] == "awaiting_input"
        second = await settled(service, await service.create("alice", prompt="厚8mm", clarification_job_id=first["id"]))
        assert second["status"] == "awaiting_input", second
        assert second["document_id"] == first["document_id"]
        assert executor.calls == 0
        final = await settled(service, await service.create("alice", prompt="垂直孔距30mm", clarification_job_id=second["id"], request_id="final-answer"))
        assert final["status"] == "completed", final
        assert executor.calls == 1
        assert len(service.store.list_documents("alice")) == 1
        revisions = service.store.list_revisions(first["document_id"], "alice")
        assert len(revisions) == 1
        compiled = compile_ir(revisions[0]["ir"])
        assert compiled.expected_geometry["bbox"] == [80, 50, 8]
        assert compiled.features[1].params["diameter"] == 6
        assert sorted(compiled.features[1].params["positions"]) == [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]]
        _, _, payload = service.store.job_private(final["id"])
        assert payload["prompt"] == INCOMPLETE
        assert [turn["answer"] for turn in payload["clarification"]] == ["厚8mm", "垂直孔距30mm"]
        replay = await service.create("alice", prompt="垂直孔距30mm", clarification_job_id=second["id"], request_id="final-answer")
        assert replay["id"] == final["id"]
        with pytest.raises(StoreConflict):
            await service.create("alice", prompt="垂直孔距30mm", clarification_job_id=second["id"], request_id="another-answer")
        with pytest.raises(StoreConflict):
            await service.create("alice", prompt="厚9mm", clarification_job_id=first["id"])
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_complete_first_request_proceeds_without_question(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    try:
        result = await settled(service, await service.create("alice", prompt=COMPLETE))
        assert result["status"] == "completed", result
        assert executor.calls == 1
        assert not any(job["status"] == "awaiting_input" for _, job in service.store.events(result["id"], "alice"))
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_answer_scope_kind_and_invalid_failure_are_rejected(tmp_path):
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    try:
        first = await settled(service, await service.create("alice", prompt=INCOMPLETE))
        with pytest.raises(StoreNotFound):
            await service.create("bob", prompt="厚8mm", clarification_job_id=first["id"])
        with pytest.raises(ValueError):
            await service.create("alice", prompt=" ", clarification_job_id=first["id"])
        with pytest.raises(ValueError):
            await service.create("alice", ir=sample_ir(), clarification_job_id=first["id"])
        done = await settled(service, await service.create("alice", prompt=COMPLETE))
        with pytest.raises(ValueError):
            await service.edit("alice", done["document_id"], expected_revision=done["revision_id"],
                               prompt="厚8mm", clarification_job_id=first["id"])
        with pytest.raises(ValueError):
            await service.create("alice", prompt="厚8mm", clarification_job_id=done["id"])
        service._executor.fail = True
        failed = await settled(service, await service.create("alice", ir=sample_ir()))
        assert failed["status"] == "failed"
        with pytest.raises(ValueError):
            await service.create("alice", prompt="厚8mm", clarification_job_id=failed["id"])
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_edit_answers_resume_original_and_stale_revision_is_rejected(tmp_path):
    async def edit_planner(prompt, ir):
        if "用户补充：8mm" not in prompt:
            raise RequirementError("需要多大厚度？")
        assert "增加厚度" in prompt
        return {"height": 8}

    service = CADV2Service(tmp_path, executor=FakeExecutor(), edit_planner=edit_planner)
    try:
        done = await settled(service, await service.create("alice", ir=sample_ir()))
        question = await settled(service, await service.edit("alice", done["document_id"], expected_revision=done["revision_id"], prompt="增加厚度"))
        assert question["status"] == "awaiting_input"
        answer = await settled(service, await service.edit("alice", done["document_id"], expected_revision=done["revision_id"], prompt="8mm", clarification_job_id=question["id"]))
        assert answer["status"] == "completed", answer
        with pytest.raises(ValueError, match="same revision"):
            await service.edit("alice", done["document_id"], expected_revision=answer["revision_id"], prompt="8mm", clarification_job_id=question["id"])
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_infrastructure_error_does_not_ask_for_design_information(tmp_path):
    async def unavailable(prompt, document_id):
        raise InfrastructureError("Configure a planning model")

    service = CADV2Service(tmp_path, executor=FakeExecutor(), planner=unavailable)
    try:
        failed = await settled(service, await service.create("alice", prompt="生成支架"))
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "InfrastructureError"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_api_question_sse_closes_and_answer_continues(tmp_path):
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    token = "clarification-local-test-token"
    app = create_app(service, token=token)
    try:
        await service.start()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as client:
            response = await client.post("/api/v1/cad-v2/jobs", json={"prompt": INCOMPLETE})
            assert response.status_code == 202
            first = await settled(service, response.json()["data"], "cad-v2-local")
            events = await client.get(f"/api/v1/cad-v2/jobs/{first['id']}/events")
            snapshots = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
            assert snapshots[-1]["status"] == "awaiting_input"
            response = await client.post("/api/v1/cad-v2/jobs", json={"prompt": "厚8mm，垂直孔距30mm", "clarification_job_id": first["id"]})
            assert response.status_code == 202, response.text
            result = await settled(service, response.json()["data"], "cad-v2-local")
            assert result["status"] == "completed", result
            assert result["document_id"] == first["document_id"]
    finally:
        await service.shutdown()


def test_single_supervisor_lock_prevents_two_writers(tmp_path):
    first, second = CADStore(tmp_path), CADStore(tmp_path)
    first.acquire_supervisor_lock()
    try:
        with pytest.raises(RuntimeError, match="active supervisor"):
            second.acquire_supervisor_lock()
    finally:
        first.release_supervisor_lock()
    second.acquire_supervisor_lock()
    second.release_supervisor_lock()
