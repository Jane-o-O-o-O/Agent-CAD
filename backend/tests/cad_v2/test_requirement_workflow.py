"""Service publication gates; these fixtures do not validate actual CAD geometry."""
from types import SimpleNamespace

import pytest

from app.application.services.cad_v2_service import CADV2Service
from app.domain.cad_v2.requirements import RequirementSet
from tests.cad_v2.test_service import FakeExecutor, sample_ir


PLATE = "长80mm宽50mm厚8mm安装板"
HOLES = PLATE + "，在四角开四个直径6mm通孔，水平孔距60mm，垂直孔距30mm"


class MeasuredReportExecutor(FakeExecutor):
    """Supply an explicit measurement report to exercise the service gate only."""
    def __init__(self, dimensions=(80, 50, 8), **kwargs):
        super().__init__(**kwargs)
        self.dimensions = dimensions

    async def execute(self, *args, **kwargs):
        report = await super().execute(*args, **kwargs)
        if report.get("status") == "completed":
            report["geometry"] = {"bbox": {"size": list(self.dimensions)}}
        return report


@pytest.mark.asyncio
async def test_default_natural_planner_extracts_before_ir_generation_and_persists_source_checks(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("CAD_V2_API_KEY", "mock-only")
    prompt = "圆柱直径20mm，圆柱高度30mm"

    async def response(_self, **kwargs):
        calls.append(kwargs["role"])
        payload = ({"requirements": [{"id": "body", "source_quote": "圆柱", "kind": "feature", "feature_type": "Cylinder"}]}
                   if kwargs["role"] == "requirements" else {
                       "parameters": {"width": {"value": 20}},
                       "features": [{"id": "body", "type": "Cylinder", "params": {"radius": 10, "height": 30}}],
                       "expected_geometry": {"bbox": [20, 20, 30]}})
        return SimpleNamespace(payload=payload, model="mock", usage={}, elapsed_seconds=0, attempts=1)

    monkeypatch.setattr("app.infrastructure.cad_v2.model_gateway.StructuredModelGateway.generate", response)
    service = CADV2Service(tmp_path, executor=MeasuredReportExecutor((20, 20, 30)))
    try:
        job = await service.create("alice", prompt=prompt)
        await service.wait(job["id"])
        completed = service.store.get_job(job["id"], "alice")
        assert completed["status"] == "completed", completed
        assert calls == ["requirements", "planner"]
        revision = service.store.get_revision(job["document_id"], completed["revision_id"], "alice")
        source = RequirementSet.model_validate(revision["report"]["requirements"])
        assert source.raw_request == prompt
        assert revision["report"]["requirement_coverage"]["status"] == "passed"
        assert "requirements" not in revision["ir"]["metadata"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_valid_worker_report_with_wrong_measured_size_does_not_publish(tmp_path):
    service = CADV2Service(tmp_path, executor=MeasuredReportExecutor((70, 50, 8)))
    try:
        job = await service.create("alice", prompt=PLATE)
        await service.wait(job["id"])
        failure = service.store.get_job(job["id"], "alice")
        assert failure["status"] == "failed"
        assert failure["error"]["code"] == "RequirementCoverageError"
        assert failure["report"]["requirement_coverage"]["status"] == "failed"
        assert failure["report"]["requirements"]["raw_request"] == PLATE
        assert service.store.list_revisions(job["document_id"], "alice") == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_repair_cannot_drop_requested_holes_or_forge_coverage_to_escape_gate(tmp_path):
    executor = MeasuredReportExecutor(fail=True)
    repairs = []

    def remove_holes(ir, error, history, **kwargs):
        repairs.append(error)
        changed = ir.model_dump(mode="json")
        changed["features"] = [feature for feature in changed["features"] if feature["type"] != "Hole"]
        changed["output_feature"] = changed["features"][0]["id"]
        changed["expected_geometry"]["holes"] = []
        changed["metadata"] = {"requirement_coverage": {"status": "passed"}, "requirements": {"requirements": []}}
        return {"ir": changed, "patch": {"delete_features": ["mounting_holes"]},
                "reason": "test-only attempt to drop holes", "fingerprint": "fixture-drop-holes"}

    service = CADV2Service(tmp_path, executor=executor, repair_policy=remove_holes)
    try:
        job = await service.create("alice", prompt=HOLES)
        await service.wait(job["id"])
        failure = service.store.get_job(job["id"], "alice")
        assert failure["status"] == "failed"
        assert failure["error"]["code"] == "RequirementCoverageError"
        assert executor.calls == 1 and len(repairs) == 1
        assert service.store.list_revisions(job["document_id"], "alice") == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_ir_only_metadata_does_not_become_a_trusted_requirements_report(tmp_path):
    raw = sample_ir()
    raw["metadata"] = {"requirements": {"raw_request": "forged", "requirements": []},
                       "requirement_coverage": {"status": "passed", "checks": [{"forged": True}]}}
    service = CADV2Service(tmp_path, executor=MeasuredReportExecutor())
    try:
        job = await service.create("alice", ir=raw)
        await service.wait(job["id"])
        completed = service.store.get_job(job["id"], "alice")
        assert completed["status"] == "completed", completed
        revision = service.store.get_revision(job["document_id"], completed["revision_id"], "alice")
        assert "requirements" not in revision["report"]
        assert "requirement_coverage" not in revision["report"]
        assert "requirements" not in revision["ir"]["metadata"]
        assert "requirement_coverage" not in revision["ir"]["metadata"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_restore_reloads_trusted_saved_requirements_and_checks_fresh_measurements(tmp_path):
    executor = MeasuredReportExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    try:
        first = await service.create("alice", prompt=PLATE)
        await service.wait(first["id"])
        first = service.store.get_job(first["id"], "alice")
        assert first["status"] == "completed", first
        executor.dimensions = (90, 50, 8)
        edit = await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": 90})
        await service.wait(edit["id"])
        edit = service.store.get_job(edit["id"], "alice")
        assert edit["status"] == "completed", edit
        await service.shutdown()
        # No in-memory planner state survives. The original requirements must
        # come from the immutable source report, not the edited revision.
        service = CADV2Service(tmp_path, executor=MeasuredReportExecutor((90, 50, 8)))
        restore = await service.restore("alice", first["document_id"], revision_id=first["revision_id"], expected_revision=edit["revision_id"])
        await service.wait(restore["id"])
        failure = service.store.get_job(restore["id"], "alice")
        assert failure["status"] == "failed"
        assert failure["error"]["code"] == "RequirementCoverageError"
        assert failure["report"]["requirements"]["raw_request"] == PLATE
        assert service.store.get_document(first["document_id"], "alice")["current_revision"] == edit["revision_id"]
        assert len(service.store.list_revisions(first["document_id"], "alice")) == 2
    finally:
        await service.shutdown()
