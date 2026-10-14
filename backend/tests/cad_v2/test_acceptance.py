"""Real-kernel acceptance A–E, separate from paid-provider and browser checks."""
from __future__ import annotations

import hashlib
import json
import math

import pytest
from build123d import import_step

from app.application.services.cad_v2_service import CADV2Service
from app.domain.cad_v2.compiler import compile_ir
from app.infrastructure.cad_v2.validation import cylindrical_surfaces
from benchmarks.cad_v2.cases import corpus, repair_ir

OWNER = "acceptance-user"
PROMPT_A = "生成一块长 80 mm、宽 50 mm、厚 8 mm 的安装板，在四角开四个直径 6 mm 的通孔，孔中心的水平距离为 60 mm，垂直距离为 30 mm。"


async def completed(service: CADV2Service, job: dict) -> tuple[dict, dict]:
    await service.wait(job["id"])
    stored = service.store.get_job(job["id"], OWNER)
    assert stored["status"] == "completed", stored
    document = service.store.get_document(stored["document_id"], OWNER)
    revision = service.store.get_revision(document["id"], document["current_revision"], OWNER)
    assert revision["report"]["validation"]["status"] == "passed"
    return stored, revision


def inspect_revision(service: CADV2Service, revision: dict, diameter: float) -> None:
    assert set(revision["artifacts"]) == {"step", "stl", "glb", "ir", "python"}
    for kind, metadata in revision["artifacts"].items():
        path, actual = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, kind)
        assert actual == metadata
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
        assert revision["id"] in path.parts
        if kind == "glb":
            assert path.read_bytes()[:4] == b"glTF"
        if kind == "ir":
            assert json.loads(path.read_text()) == revision["ir"]
    step, _ = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, "step")
    shape = import_step(step)
    assert shape.is_valid
    assert len(shape.solids()) == 1
    assert list(shape.bounding_box().size) == pytest.approx([80, 50, 8], abs=1e-4)
    assert shape.volume == pytest.approx(32000 - 4 * math.pi * (diameter / 2)**2 * 8, abs=1e-4)
    # Independent inspection of the exported and reimported B-Rep.
    cylinders = [s for s in cylindrical_surfaces(shape) if s["inward"]]
    assert len(cylinders) == 4
    assert sorted(round(s["radius"] * 2, 5) for s in cylinders) == [diameter] * 4
    centers = {(round(s["origin"][0], 5), round(s["origin"][1], 5)) for s in cylinders}
    assert centers == {(10, 10), (70, 10), (10, 40), (70, 40)}
    assert all(s["axial_length"] == pytest.approx(8, abs=1e-4) for s in cylinders)


@pytest.mark.asyncio
async def test_acceptance_a_b_e_prompt_edit_and_step_roundtrip(tmp_path):
    service = CADV2Service(tmp_path)
    try:
        first_job, first = await completed(service, await service.create(OWNER, prompt=PROMPT_A))
        assert first["ir"]["metadata"]["planning_mode"] == "deterministic-explicit-plate"
        inspect_revision(service, first, 6)
        second_job, second = await completed(service, await service.edit(
            OWNER, first["document_id"], expected_revision=first["id"],
            prompt="将四个孔的直径改成 8 mm，其他保持不变。"))
        inspect_revision(service, second, 8)
        assert second["id"] != first["id"]
        assert second["parent_revision"] == first["id"]
        assert second["ir"]["features"] == first["ir"]["features"]
        assert second["ir"]["constraints"] == first["ir"]["constraints"]
        assert second["ir"]["expected_geometry"] == first["ir"]["expected_geometry"]
        assert set(second["diff"]["parameters"]) == {"hole_diameter"}
        assert second["diff"]["features"] == {}
        assert not first_job["repair_history"] and not second_job["repair_history"]
        # Historical preview/export are still tied to the old revision.
        inspect_revision(service, first, 6)
        _, restored = await completed(service, await service.restore(
            OWNER, first["document_id"], revision_id=first["id"], expected_revision=second["id"]))
        inspect_revision(service, restored, 6)
        assert restored["parent_revision"] == second["id"]
        assert len(service.store.list_revisions(first["document_id"], OWNER)) == 3
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_failed_edit_does_not_replace_last_valid_revision(tmp_path):
    service = CADV2Service(tmp_path)
    try:
        _, first = await completed(service, await service.create(OWNER, prompt=PROMPT_A))
        failed = await service.edit(OWNER, first["document_id"], expected_revision=first["id"],
                                    parameters={"hole_diameter": -8})
        await service.wait(failed["id"])
        result = service.store.get_job(failed["id"], OWNER)
        assert result["status"] == "failed"
        assert (result["error"].get("code") or result["error"].get("type")) == "ParameterError"
        assert service.store.get_document(first["document_id"], OWNER)["current_revision"] == first["id"]
        assert len(service.store.list_revisions(first["document_id"], OWNER)) == 1
        inspect_revision(service, first, 6)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_acceptance_c_real_selection_failure_and_minimal_repair(tmp_path):
    service = CADV2Service(tmp_path)
    original = repair_ir()
    try:
        job, revision = await completed(service, await service.create(OWNER, ir=original))
        history = job["repair_history"]
        assert len(history) == 1
        assert history[0]["error"]["code"] == "InvalidTopologyError"
        assert history[0]["error"]["feature_id"] == "round"
        assert history[0]["error"]["details"]["matched"] == 0
        assert history[0]["patch"]["path"] == "params.selector.tolerance"
        assert history[0]["patch"]["before"] == 0.000001
        assert history[0]["patch"]["after"] <= 0.001
        compiled = compile_ir(revision["ir"])
        assert compiled.features[-1].params["radius"] == 1
        assert revision["report"]["geometry"]["volume"] == pytest.approx(9592 + 2*math.pi, abs=1e-4)
        assert revision["report"]["geometry"]["step_roundtrip"]["valid"]
        stages = [event[1]["stage"] for event in service.store.events(job["id"], OWNER)]
        assert "repairing" in stages
        assert stages[-1] == "completed"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_acceptance_d_l_bracket_two_faces_holes_fillet(tmp_path):
    service = CADV2Service(tmp_path)
    bracket = next(c for c in corpus() if c.name == "22_l_bracket")
    try:
        _, revision = await completed(service, await service.create(OWNER, ir=bracket.ir))
        report = revision["report"]
        assert report["geometry"]["bbox"]["size"] == pytest.approx([60, 40, 46], abs=1e-4)
        assert report["geometry"]["volume"] == pytest.approx(28560 - 156*math.pi, abs=1e-4)
        assert report["geometry"]["step_roundtrip"]["valid"]
        checks = report["validation"]["checks"]
        assert len([c for c in checks if c["name"].startswith("expected_hole.") and c["name"].endswith(".through")]) == 4
        assert any(c["name"] == "feature.body.final_finish" and c["status"] == "passed" for c in checks)
        step, _ = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, "step")
        rounded_edges = [surface for surface in cylindrical_surfaces(import_step(step))
                         if not surface["inward"] and abs(surface["radius"] - 2) < 1e-4
                         and abs(abs(surface["direction"][0]) - 1) < 1e-4]
        assert len(rounded_edges) == 1
        assert rounded_edges[0]["axial_length"] == pytest.approx(60, abs=1e-4)
    finally:
        await service.shutdown()
