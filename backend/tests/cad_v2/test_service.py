"""Supervisor/transaction contracts. Geometry truth is tested separately with OCP."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from app.application.services.cad_v2_service import CADV2Service
from app.infrastructure.cad_v2.store import CADStore, StoreConflict, StoreNotFound


def sample_ir():
    return {"schema_version": "2.0", "parameters": {"width": {"value": 80}, "depth": {"value": 50}, "height": {"value": 8}},
            "features": [{"id": "base", "type": "Box", "params": {"width": "width", "depth": "depth", "height": "height"}}],
            "expected_geometry": {"bbox": ["width", "depth", "height"], "volume": "width*depth*height"}}


class FakeExecutor:
    """Tests orchestration only, deliberately not a CAD geometry simulation."""
    def __init__(self, *, fail=False, gate=None):
        self.fail = fail
        self.gate = gate
        self.started = asyncio.Event()
        self.calls = 0
        self.cancelled = False
        self.active = 0
        self.peak = 0

    async def execute(self, ir, output_dir, cancel_event=None, on_stage=None):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            if self.gate:
                await self.gate.wait()
            if self.fail:
                return {"status": "failed", "error": {"code": "GeometryKernelError", "message": "fixture failure"}}
            if on_stage:
                await on_stage("validating")
                await on_stage("exporting")
            artifacts = []
            for format, suffix in (("step", "step"), ("stl", "stl"), ("glb", "glb"), ("python", "py")):
                path = Path(output_dir) / f"model.{suffix}"
                data = f"orchestration fixture only:{format}:{ir.parameters['width'].value}".encode()
                path.write_bytes(data)
                artifacts.append({"format": format, "filename": path.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
            return {"status": "completed", "geometry": {}, "validation": {"status": "passed", "checks": []}, "trace": [], "artifacts": artifacts}
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.active -= 1


async def create_completed(service, owner="alice", request_id=None):
    job = await service.create(owner, ir=sample_ir(), request_id=request_id)
    await service.wait(job["id"])
    result = service.store.get_job(job["id"], owner)
    assert result["status"] == "completed", result
    return result


@pytest.mark.asyncio
async def test_revision_persistence_edit_restore_and_immutable_artifacts(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    first = await create_completed(service)
    doc = first["document_id"]
    old_path, old_manifest = service.store.artifact_location(doc, first["revision_id"], "alice", "step")
    old_bytes = old_path.read_bytes()
    edited = await service.edit("alice", doc, expected_revision=first["revision_id"], parameters={"width": 90})
    await service.wait(edited["id"])
    edited = service.store.get_job(edited["id"], "alice")
    assert edited["status"] == "completed", edited
    revision = service.store.get_revision(doc, edited["revision_id"], "alice")
    assert revision["ir"]["parameters"]["width"]["value"] == 90
    assert revision["ir"]["parameters"]["height"]["value"] == 8
    assert set(revision["diff"]["parameters"]) == {"width"}
    assert not revision["diff"]["features"]
    assert old_path.read_bytes() == old_bytes
    restored = await service.restore("alice", doc, revision_id=first["revision_id"], expected_revision=edited["revision_id"])
    await service.wait(restored["id"])
    restored = service.store.get_job(restored["id"], "alice")
    assert restored["status"] == "completed"
    assert restored["revision_id"] not in {first["revision_id"], edited["revision_id"]}
    await service.shutdown()
    reopened = CADV2Service(tmp_path, executor=FakeExecutor())
    await reopened.start()
    assert reopened.store.get_document(doc, "alice")["current_revision"] == restored["revision_id"]
    assert len(reopened.store.list_revisions(doc, "alice")) == 3
    assert reopened.store.get_revision(doc, first["revision_id"], "alice")["artifacts"]["step"] == old_manifest
    await reopened.shutdown()


@pytest.mark.asyncio
async def test_failed_edit_and_stale_revision_preserve_last_valid(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor, repair_policy=lambda *a, **kw: None)
    first = await create_completed(service)
    executor.fail = True
    edited = await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": 90})
    await service.wait(edited["id"])
    assert service.store.get_job(edited["id"], "alice")["status"] == "failed"
    assert service.store.get_document(first["document_id"], "alice")["current_revision"] == first["revision_id"]
    assert len(service.store.list_revisions(first["document_id"], "alice")) == 1
    with pytest.raises(StoreNotFound):
        await service.edit("alice", first["document_id"], expected_revision="missing", parameters={"width": 90})
    await service.shutdown()


@pytest.mark.asyncio
async def test_owner_checks_cover_documents_revisions_jobs_artifacts(tmp_path):
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    first = await create_completed(service)
    assert service.store.list_documents("bob") == []
    calls = [lambda: service.store.get_document(first["document_id"], "bob"),
             lambda: service.store.get_revision(first["document_id"], first["revision_id"], "bob"),
             lambda: service.store.artifact_location(first["document_id"], first["revision_id"], "bob", "step"),
             lambda: service.store.get_job(first["id"], "bob"),
             lambda: service.store.events(first["id"], "bob")]
    for call in calls:
        with pytest.raises(StoreNotFound):
            call()
    with pytest.raises(StoreNotFound):
        await service.cancel("bob", first["id"])
    await service.shutdown()


@pytest.mark.asyncio
async def test_idempotent_create_and_edit_deduplicate_and_reject_changed_payload(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    first = await create_completed(service, request_id="create-once")
    duplicate = await service.create("alice", ir=sample_ir(), request_id="create-once")
    assert duplicate["id"] == first["id"]
    assert executor.calls == 1
    changed = sample_ir()
    changed["parameters"]["width"]["value"] = 81
    with pytest.raises(StoreConflict):
        await service.create("alice", ir=changed, request_id="create-once")
    edit = await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": 90}, request_id="edit-once")
    await service.wait(edit["id"])
    again = await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": 90}, request_id="edit-once")
    assert again["id"] == edit["id"]
    assert executor.calls == 2
    with pytest.raises(StoreConflict):
        await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": 91}, request_id="new-edit")
    await service.shutdown()


@pytest.mark.asyncio
async def test_concurrent_edits_publish_one_revision_with_cas(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor, max_concurrency=2)
    first = await create_completed(service)
    gate = asyncio.Event()
    executor.gate = gate
    edits = [await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": value}) for value in (90, 100)]
    gate.set()
    await asyncio.gather(*(service.wait(job["id"]) for job in edits))
    results = [service.store.get_job(job["id"], "alice") for job in edits]
    assert sorted(j["status"] for j in results) == ["completed", "failed"]
    failed = next(j for j in results if j["status"] == "failed")
    assert failed["error"]["http_status"] == 409
    assert len(service.store.list_revisions(first["document_id"], "alice")) == 2
    assert len(list((tmp_path / "artifacts" / first["document_id"]).iterdir())) == 2
    await service.shutdown()


@pytest.mark.asyncio
async def test_running_and_queued_cancellation_kill_execution_without_publication(tmp_path):
    gate = asyncio.Event()
    executor = FakeExecutor(gate=gate)
    service = CADV2Service(tmp_path, executor=executor, max_concurrency=1)
    running = await service.create("alice", ir=sample_ir())
    await executor.started.wait()
    queued = await service.create("alice", ir=sample_ir())
    result = await service.cancel("alice", queued["id"])
    assert result["status"] == "cancelled"
    assert executor.calls == 1
    result = await service.cancel("alice", running["id"])
    assert result["status"] == "cancelled"
    assert executor.cancelled
    assert executor.peak == 1
    assert service.store.list_revisions(running["document_id"], "alice") == []
    assert not (tmp_path / "jobs" / running["id"]).exists()
    await service.shutdown()


@pytest.mark.asyncio
async def test_restart_recovers_queued_but_marks_interrupted_running_failed(tmp_path):
    store = CADStore(tmp_path)
    queued, _ = store.create_job("alice", "queued-document", {"kind": "create", "ir": sample_ir(), "prompt": None})
    interrupted, _ = store.create_job("alice", "running-document", {"kind": "create", "ir": sample_ir(), "prompt": None})
    store.update_job(interrupted["id"], status="running", stage="building")
    orphan = tmp_path / "jobs" / interrupted["id"]
    orphan.mkdir(parents=True)
    (orphan / "temporary.step").write_text("not committed")
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    await service.start()
    await service.wait(queued["id"])
    assert service.store.get_job(queued["id"], "alice")["status"] == "completed"
    failure = service.store.get_job(interrupted["id"], "alice")
    assert failure["status"] == "failed"
    assert failure["error"]["type"] == "InfrastructureError"
    assert not orphan.exists()
    await service.shutdown()


@pytest.mark.asyncio
async def test_supervisor_lock_and_unverified_geometry_refusal(tmp_path):
    one = CADV2Service(tmp_path, executor=FakeExecutor())
    two = CADV2Service(tmp_path, executor=FakeExecutor())
    await one.start()
    with pytest.raises(RuntimeError, match="one API worker"):
        await two.start()
    class UnverifiedExecutor(FakeExecutor):
        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            result["validation"]["status"] = "unverified"
            return result
    one._executor = UnverifiedExecutor()
    one._repair_policy = lambda *a, **kw: None
    job = await one.create("alice", ir=sample_ir())
    await one.wait(job["id"])
    assert one.store.get_job(job["id"], "alice")["status"] == "failed"
    assert not one.store.list_revisions(job["document_id"], "alice")
    await one.shutdown()
    await two.start()
    await two.shutdown()


@pytest.mark.asyncio
async def test_queue_budget_replays_existing_request_and_shutdown_cancels_all(tmp_path):
    executor = FakeExecutor(gate=asyncio.Event())
    service = CADV2Service(tmp_path, executor=executor, max_concurrency=1, max_queue=2)
    first = await service.create("alice", ir=sample_ir(), request_id="running")
    await executor.started.wait()
    second = await service.create("alice", ir=sample_ir(), request_id="queued")
    replay = await service.create("alice", ir=sample_ir(), request_id="running")
    assert replay["id"] == first["id"]
    with pytest.raises(StoreConflict, match="queue is full"):
        await service.create("alice", ir=sample_ir(), request_id="overflow")
    assert len(service.store.list_documents("alice")) == 2
    await service.shutdown()
    for job in (first, second):
        assert service.store.get_job(job["id"], "alice")["status"] == "cancelled"


@pytest.mark.asyncio
async def test_artifact_escape_rejected_and_orphan_cleanup_preserves_committed(tmp_path):
    executor = FakeExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    first = await create_completed(service)
    committed, _ = service.store.artifact_location(first["document_id"], first["revision_id"], "alice", "step")
    outside = tmp_path / "outside.step"
    outside.write_text("must not be served")
    class EscapeExecutor(FakeExecutor):
        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            result["artifacts"][0]["filename"] = str(outside)
            return result
    service._executor = EscapeExecutor()
    edit = await service.edit("alice", first["document_id"], expected_revision=first["revision_id"], parameters={"width": 90})
    await service.wait(edit["id"])
    failed = service.store.get_job(edit["id"], "alice")
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "ExportError"
    assert committed.is_file()
    orphan = tmp_path / "artifacts" / first["document_id"] / "unpublished"
    orphan.mkdir()
    (orphan / "unpublished.step").write_text("crash before sqlite publication")
    await service.shutdown()
    restarted = CADV2Service(tmp_path, executor=FakeExecutor())
    await restarted.start()
    assert not orphan.exists()
    assert committed.is_file()
    assert outside.read_text() == "must not be served"
    await restarted.shutdown()


@pytest.mark.asyncio
async def test_real_kernel_prompt_edit_and_step_roundtrip(tmp_path):
    """Acceptance A/B/E: actual separate OCP worker, no model API or fake geometry."""
    from build123d import import_step
    service = CADV2Service(tmp_path, max_concurrency=1)
    try:
        job = await service.create("alice", prompt="生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。")
        await service.wait(job["id"])
        original = service.store.get_job(job["id"], "alice")
        assert original["status"] == "completed", original
        assert original["report"]["geometry"]["bbox"]["size"] == pytest.approx([80, 50, 8])
        edit = await service.edit("alice", original["document_id"], expected_revision=original["revision_id"], prompt="将四个孔的直径改成 8 mm，其他保持不变。")
        await service.wait(edit["id"])
        updated = service.store.get_job(edit["id"], "alice")
        assert updated["status"] == "completed", updated
        assert updated["report"]["geometry"]["bbox"]["size"] == pytest.approx([80, 50, 8])
        assert updated["report"]["geometry"]["volume"] < original["report"]["geometry"]["volume"]
        revision = service.store.get_revision(updated["document_id"], updated["revision_id"], "alice")
        assert set(revision["diff"]["parameters"]) == {"hole_diameter"}
        checks = revision["report"]["validation"]["checks"]
        assert checks and all(item["status"] == "passed" for item in checks)
        assert sum(".through" in item["name"] for item in checks) >= 4
        path, _ = service.store.artifact_location(updated["document_id"], updated["revision_id"], "alice", "step")
        reopened = import_step(path)
        assert reopened.is_valid
        assert reopened.volume == pytest.approx(updated["report"]["geometry"]["volume"], abs=1e-5)
        assert tuple(reopened.bounding_box().size) == pytest.approx((80, 50, 8))
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_real_repair_preserves_design_dimensions_and_persists_history(tmp_path):
    """Acceptance C: actual semantic selection miss, then bounded precision repair."""
    ir = {"features": [
        {"id": "base", "type": "Box", "params": {"width": 20, "depth": 10, "height": 8}},
        {"id": "finish", "type": "Fillet", "inputs": ["base"], "params": {"radius": 1,
         "selector": {"center": [0.00005, 0, 4], "tolerance": 1e-6, "expected_count": 1}}}],
        "expected_geometry": {"bbox": [20, 10, 8], "solids": 1}}
    service = CADV2Service(tmp_path, max_concurrency=1)
    try:
        job = await service.create("alice", ir=ir)
        await service.wait(job["id"])
        result = service.store.get_job(job["id"], "alice")
        assert result["status"] == "completed", result
        assert len(result["repair_history"]) == 1
        repair = result["repair_history"][0]
        assert repair["error"]["code"] == "InvalidTopologyError"
        assert repair["patch"]["path"] == "params.selector.tolerance"
        assert repair["patch"]["after"] <= 0.001
        revision = service.store.get_revision(result["document_id"], result["revision_id"], "alice")
        assert revision["ir"]["features"][1]["params"]["radius"] == 1
        assert revision["report"]["geometry"]["bbox"]["size"] == pytest.approx([20, 10, 8])
        assert any(snapshot["status"] == "repairing" for _, snapshot in service.store.events(result["id"], "alice"))
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_real_add_delete_feature_with_explicit_intent_and_output_diff(tmp_path):
    service = CADV2Service(tmp_path, max_concurrency=1)
    try:
        initial = await service.create("alice", ir=sample_ir())
        await service.wait(initial["id"])
        first = service.store.get_job(initial["id"], "alice")
        assert first["status"] == "completed", first
        added = await service.edit("alice", first["document_id"], expected_revision=first["revision_id"],
            add_features=[{"id": "center_hole", "type": "Hole", "inputs": ["base"],
                           "params": {"diameter": 6, "positions": [[40, 25, 0]], "through": True}}],
            output_feature="center_hole", expected_geometry={
                "volume": "width*depth*height-3.141592653589793*3**2*height",
                "holes": [{"feature_id": "center_hole", "diameter": 6, "positions": [[40, 25, 0]], "through": True}]})
        await service.wait(added["id"])
        added = service.store.get_job(added["id"], "alice")
        assert added["status"] == "completed", added
        revision = service.store.get_revision(added["document_id"], added["revision_id"], "alice")
        assert revision["diff"]["output_feature"]["after"] == "center_hole"
        assert "expected_geometry" in revision["diff"]
        assert revision["ir"]["expected_geometry"]["bbox"] == ["width", "depth", "height"]
        removed = await service.edit("alice", added["document_id"], expected_revision=added["revision_id"],
            delete_features=["center_hole"], output_feature="base", expected_geometry={"volume": "width*depth*height", "holes": []})
        await service.wait(removed["id"])
        removed = service.store.get_job(removed["id"], "alice")
        assert removed["status"] == "completed", removed
        assert removed["report"]["geometry"]["volume"] == pytest.approx(first["report"]["geometry"]["volume"])
        assert len(service.store.list_revisions(first["document_id"], "alice")) == 3
    finally:
        await service.shutdown()
