"""Persist trusted healing policies through revisions and real Python replay.

Fault fixtures damage actual OCCT vertices after a real boolean operation;
validity, repair, design checks and exported files all use the real kernel.
"""
from copy import deepcopy
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
from build123d import Compound, import_step
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.gp import gp_Pnt

from app.application.services.cad_v2_service import CADV2Service
from app.domain.cad_v2.compiler import compile_ir, compile_to_python
from app.domain.cad_v2.errors import CADError, ConstraintViolationError
from app.infrastructure.cad_v2.backend import Build123dBackend, artifact
from app.infrastructure.cad_v2.executor import CADExecutor


OWNER = "healing-replay"
POLICY = {"bores": {"strategy": "shape_fix", "tolerance_mm": 1e-5}}


def plate_ir():
    return {"parameters": {"width": {"value": 20}}, "features": [
        {"id": "base", "type": "Box", "params": {"width": "width", "depth": 15, "height": 5}},
        {"id": "bores", "type": "Hole", "inputs": ["base"], "params": {
            "diameter": 2, "positions": [[5, 5, 0], [15, 5, 0]], "through": True}},
        {"id": "unused", "type": "Box", "params": {"width": 2, "depth": 2, "height": 2}},
    ], "output_feature": "bores", "expected_geometry": {
        "bbox": ["width", 15, 5], "solids": 1,
        "holes": [{"diameter": 2, "positions": [[5, 5, 0], [15, 5, 0]], "through": True}],
    }}


class VertexFaultBackend(Build123dBackend):
    def _operation(self, feature, shapes):
        shape, evidence = super()._operation(feature, shapes)
        if feature.id == "bores":
            shape = Compound.cast(BRepBuilderAPI_Copy(shape.wrapped, True, False).Shape())
            vertex = min(shape.vertices(), key=lambda item: tuple(item.center()))
            point = BRep_Tool.Pnt_s(vertex.wrapped)
            BRep_Builder().UpdateVertex(vertex.wrapped,
                gp_Pnt(point.X() + 5e-6, point.Y(), point.Z()), 1e-7)
            assert not shape.is_valid
        return shape, evidence


class RealFaultExecutor:
    """Use real geometry in-process to control a deterministic kernel defect."""
    def __init__(self):
        self.calls = []
        self.inject_fault = True

    async def execute(self, ir, output_dir, cancel_event=None, on_stage=None, execution_options=None):
        options = deepcopy(execution_options or {})
        self.calls.append(options)
        backend_type = VertexFaultBackend if self.inject_fault else Build123dBackend
        engine = backend_type(healing_policy=options.get("healing"))
        try:
            built = engine.build(compile_ir(ir))
            validation = engine.validate(built)
            if validation["status"] != "passed":
                raise ConstraintViolationError("Fault fixture failed original geometry constraints", details=validation)
            artifacts = engine.export(built, output_dir)
            script = Path(output_dir) / "model.py"
            script.write_text(compile_to_python(ir, healing_policy=options.get("healing")))
            artifacts.append(artifact(script, "python"))
            # The service must persist its trusted policy even if an executor
            # omits it from its report; this is not model or IR metadata.
            return {"status": "completed", "geometry": built.geometry, "validation": validation,
                    "trace": built.trace, "artifacts": artifacts}
        except CADError as error:
            return {"status": "failed", "error": error.to_dict()}


async def finish(service, job, *, status="completed"):
    await service.wait(job["id"])
    saved = service.store.get_job(job["id"], OWNER)
    assert saved["status"] == status, saved
    revision = service.store.get_revision(saved["document_id"], saved["revision_id"], OWNER) if status == "completed" else None
    return saved, revision


@pytest.mark.asyncio
async def test_healing_survives_restart_source_restore_and_only_unchanged_feature_subgraphs(tmp_path, monkeypatch):
    executor = RealFaultExecutor()
    service = CADV2Service(tmp_path, executor=executor)
    try:
        first_job, original = await finish(service, await service.create(OWNER, ir=plate_ir()))
        assert len(first_job["repair_history"]) == 1
        assert original["report"]["execution_options"]["healing"] == POLICY
        assert "healing" not in original["ir"]["metadata"]
    finally:
        await service.shutdown()

    # No retries available after reopening: recovery must come from the saved
    # revision recipe, not accidental rediscovery of the same repair.
    executor = RealFaultExecutor()
    service = CADV2Service(tmp_path, executor=executor, max_repairs=0)
    try:
        unrelated_job, unrelated = await finish(service, await service.edit(OWNER, original["document_id"],
            expected_revision=original["id"], feature_patches=[{"id": "unused", "params": {"height": 3}}]))
        assert not unrelated_job["repair_history"]
        assert executor.calls[-1]["healing"] == POLICY
        assert unrelated["report"]["execution_options"]["healing"] == POLICY

        # A resolved upstream dimension changes even though the target Hole
        # feature's literal params and inputs stay identical. Do not inherit.
        executor.inject_fault = False
        _, changed = await finish(service, await service.edit(OWNER, original["document_id"],
            expected_revision=unrelated["id"], parameters={"width": 24}))
        assert not executor.calls[-1].get("healing")
        assert not changed["report"]["execution_options"].get("healing")

        # Restore the specified older source, whose policy differs from the
        # current revision. Loading the current/base revision policy would fail.
        executor.inject_fault = True
        restored_job, restored = await finish(service, await service.restore(OWNER, original["document_id"],
            revision_id=original["id"], expected_revision=changed["id"]))
        assert not restored_job["repair_history"]
        assert executor.calls[-1]["healing"] == POLICY
        assert restored["ir"] == original["ir"]
        assert restored["report"]["execution_options"]["healing"] == POLICY

        # The exported trusted script also reproduces the actual repair. Only
        # the reproducible OCP fault source is injected; no validity is mocked.
        script, _ = service.store.artifact_location(original["document_id"], restored["id"], OWNER, "python")
        from app.infrastructure.cad_v2 import backend as backend_module
        monkeypatch.setattr(backend_module, "Build123dBackend", VertexFaultBackend)
        replay_dir = tmp_path / "replayed"
        monkeypatch.setattr(sys, "argv", [str(script), str(replay_dir)])
        namespace = runpy.run_path(str(script), run_name="__main__")
        assert namespace["backend"].healing_policy == POLICY
        replayed = import_step(replay_dir / "model.step")
        assert replayed.is_valid and len(replayed.solids()) == 1
        assert replayed.volume == pytest.approx(original["report"]["geometry"]["volume"], abs=1e-7)
        assert namespace["report"]["status"] == "passed"

        failed, _ = await finish(service, await service.edit(OWNER, original["document_id"],
            expected_revision=restored["id"], parameters={"width": 22}), status="failed")
        assert not executor.calls[-1].get("healing")
        assert failed["error"]["code"] == "InvalidTopologyError"
        assert service.store.get_document(original["document_id"], OWNER)["current_revision"] == restored["id"]
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_actual_worker_report_and_python_export_keep_trusted_policy(tmp_path, monkeypatch):
    # Serialization/replay test in explicit development isolation; dedicated
    # sandbox tests assert strict mode fails closed on unsupported hosts.
    report = await CADExecutor(timeout_seconds=45, isolation_mode="process-dev").execute(plate_ir(), tmp_path / "worker",
        execution_options={"healing": POLICY})
    assert report["status"] == "completed", report
    assert report["execution_options"]["healing"] == POLICY
    script = tmp_path / "worker" / "model.py"
    # Execute, rather than matching a particular generated source formatting.
    replay_dir = tmp_path / "worker-replay"
    from app.infrastructure.cad_v2 import backend as backend_module
    monkeypatch.setattr(backend_module, "Build123dBackend", VertexFaultBackend)
    monkeypatch.setattr(sys, "argv", [str(script), str(replay_dir)])
    namespace = runpy.run_path(str(script), run_name="__main__")
    assert namespace["backend"].healing_policy == POLICY
    assert import_step(replay_dir / "model.step").is_valid


@pytest.mark.asyncio
async def test_model_metadata_cannot_authorize_healing_without_service_policy(tmp_path):
    executor = RealFaultExecutor()
    service = CADV2Service(tmp_path, executor=executor, max_repairs=0)
    ir = plate_ir()
    ir["metadata"] = {"execution_options": {"healing": POLICY}, "healing": POLICY}
    try:
        failed, _ = await finish(service, await service.create(OWNER, ir=ir), status="failed")
        assert failed["error"]["code"] == "InvalidTopologyError"
        assert executor.calls == [{}]
        assert service.store.get_document(failed["document_id"], OWNER)["current_revision"] is None
    finally:
        await service.shutdown()


def test_policy_validation_and_replay_compilation_do_not_load_the_kernel():
    code = """
import sys
from app.infrastructure.cad_v2.healing import validate_healing_options
from app.domain.cad_v2.compiler import compile_to_python
policy = {'base': {'strategy': 'shape_fix', 'tolerance_mm': 1e-5}}
validate_healing_options(policy)
compile_to_python({'features': [{'id': 'base', 'type': 'Box', 'params': {'width': 1, 'depth': 2, 'height': 3}}]}, healing_policy=policy)
assert 'build123d' not in sys.modules
assert not any(name == 'OCP' or name.startswith('OCP.') for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
