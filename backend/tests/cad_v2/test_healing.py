"""Real OCCT defects and bounded recovery; no mocked geometry or model calls."""
from copy import deepcopy
import json
import math

import pytest
from build123d import Box, Compound, import_step
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.gp import gp_Pnt

from app.application.services.cad_v2_repair import next_repair
from app.domain.cad_v2.compiler import compile_ir
from app.domain.cad_v2.errors import InvalidTopologyError, ParameterError
from app.infrastructure.cad_v2.backend import Build123dBackend, measure
from app.infrastructure.cad_v2.healing import heal_shape, validate_healing_options
from app.infrastructure.cad_v2.validation import cylindrical_surfaces, validate


def plate_ir():
    return {"features": [
        {"id": "base", "type": "Box", "params": {"width": 20, "depth": 15, "height": 5}},
        {"id": "bores", "type": "Hole", "inputs": ["base"], "params": {
            "diameter": 2, "positions": [[5, 5, 0], [15, 5, 0]], "through": True}},
    ], "expected_geometry": {
        "bbox": [20, 15, 5], "volume": 1500 - 10 * math.pi, "solids": 1,
        "holes": [{"diameter": 2, "positions": [[5, 5, 0], [15, 5, 0]], "through": True}],
    }, "constraints": [{"id": "thickness", "type": "dimension", "axis": "z", "expected": 5}]}


def vertex_mismatch(shape, displacement=5e-6):
    """A 3D vertex no longer agrees with its exact edge curves at its tolerance."""
    damaged = Compound.cast(BRepBuilderAPI_Copy(shape.wrapped, True, False).Shape())
    vertex = min(damaged.vertices(), key=lambda item: tuple(item.center()))
    point = BRep_Tool.Pnt_s(vertex.wrapped)
    BRep_Builder().UpdateVertex(vertex.wrapped, gp_Pnt(point.X() + displacement, point.Y(), point.Z()), 1e-7)
    assert not damaged.is_valid
    return damaged


def separate_faces(shape):
    """Independent, geometrically coincident face boundaries with no solid."""
    return Compound(children=[Compound.cast(BRepBuilderAPI_Copy(face.wrapped, True, False).Shape())
                              for face in shape.faces()])


@pytest.mark.parametrize("strategy,damage", [
    ("shape_fix", vertex_mismatch),
    ("orient_solids", lambda shape: Compound.cast(shape.wrapped.Reversed())),
    ("sew_closed_shells", separate_faces),
])
def test_real_defect_recovers_with_original_holes_constraints_and_step(tmp_path, strategy, damage):
    engine = Build123dBackend()
    original_ir = plate_ir()
    built = engine.build(compile_ir(original_ir))
    original_source = deepcopy(built.compiled.source_ir)
    damaged = damage(built.shape)
    if strategy == "shape_fix":
        assert not damaged.is_valid
    elif strategy == "orient_solids":
        assert damaged.volume < 0
    else:
        assert not damaged.solids()
    repaired = heal_shape(damaged, strategy=strategy, tolerance_mm=1e-5,
                          feature_id="bores", expected_solids=1)
    assert repaired.evidence["status"] == "accepted"
    assert all(repaired.evidence["checks"].values())
    assert repaired.evidence["before"]["volume"] == pytest.approx(
        -built.shape.volume if strategy == "orient_solids" else built.shape.volume)
    assert repaired.evidence["after"]["volume"] == pytest.approx(built.shape.volume)
    json.dumps(repaired.evidence, allow_nan=False)
    # The original damaged input remains damaged; OCCT modifications were isolated.
    if strategy == "shape_fix":
        assert not damaged.is_valid
    elif strategy == "orient_solids":
        assert damaged.volume < 0
    else:
        assert not damaged.solids()
    built.shape = repaired.shape
    built.geometry = measure(built.shape)
    assert built.compiled.source_ir == original_source
    assert validate(built)["status"] == "passed"
    engine.export(built, tmp_path)
    restored = import_step(tmp_path / "model.step")
    assert restored.is_valid and len(restored.solids()) == 1
    assert restored.volume == pytest.approx(1500 - 10 * math.pi, abs=1e-7)
    assert list(restored.bounding_box().size) == pytest.approx([20, 15, 5], abs=1e-7)
    bores = [surface for surface in cylindrical_surfaces(restored) if surface["inward"]]
    assert len(bores) == 2
    assert [surface["radius"] for surface in bores] == pytest.approx([1, 1])
    assert built.geometry["step_roundtrip_checks"]["status"] == "passed"


def test_sewing_cannot_invent_missing_faces():
    faces = separate_faces(Box(10, 20, 5)).faces()
    open_box = Compound(children=list(faces)[:-1])
    with pytest.raises(InvalidTopologyError) as caught:
        heal_shape(open_box, strategy="sew_closed_shells", tolerance_mm=1e-5)
    assert "open/non-manifold" in caught.value.details["healing"]["reason"]
    assert len(open_box.faces()) == 5 and not open_box.solids()


def test_large_defects_do_not_expand_tolerances_until_they_pass():
    damaged = vertex_mismatch(Box(10, 20, 5), displacement=0.01)
    with pytest.raises(InvalidTopologyError) as caught:
        heal_shape(damaged, strategy="shape_fix", tolerance_mm=1e-5)
    report = caught.value.details["healing"]
    assert report["status"] == "rejected"
    assert report.get("checks", {}).get("valid_topology") is False or "tolerance" in report["reason"]
    assert not damaged.is_valid


def test_repaired_solid_count_must_match_original_requirement():
    with pytest.raises(InvalidTopologyError) as caught:
        heal_shape(separate_faces(Box(10, 20, 5)), strategy="sew_closed_shells",
                   tolerance_mm=1e-5, expected_solids=2)
    assert not caught.value.details["healing"]["checks"]["expected_solid_count"]


def test_sewing_cannot_turn_an_enclosed_cavity_into_added_material():
    hollow = Box(10, 20, 5) - Box(4, 4, 2)
    assert len(hollow.solids()) == 1 and len(hollow.shells()) == 2
    with pytest.raises(InvalidTopologyError) as caught:
        heal_shape(separate_faces(hollow), strategy="sew_closed_shells", tolerance_mm=1e-5)
    assert not caught.value.details["healing"]["checks"]["absolute_volume_preserved"]


def test_healing_does_not_override_unsatisfied_design_constraints():
    engine = Build123dBackend()
    ir = plate_ir()
    ir["expected_geometry"]["holes"][0]["diameter"] = 3
    built = engine.build(compile_ir(ir))
    built.shape = heal_shape(vertex_mismatch(built.shape), strategy="shape_fix", tolerance_mm=1e-5).shape
    built.geometry = measure(built.shape)
    assert built.shape.is_valid
    assert validate(built)["status"] == "failed"
    assert built.compiled.expected_geometry["holes"][0]["diameter"] == 3


@pytest.mark.parametrize("policy", [
    {"strategy": "fill_holes", "tolerance_mm": 1e-5},
    {"strategy": [], "tolerance_mm": 1e-5},
    {"strategy": "shape_fix", "tolerance_mm": 0.001},
    {"strategy": "shape_fix", "tolerance_mm": True},
    {"strategy": "shape_fix", "tolerance_mm": float("nan")},
    {"strategy": "shape_fix", "tolerance_mm": 1e-5, "expected_solids": 0},
])
def test_untrusted_or_excessive_healing_policies_are_rejected(policy):
    with pytest.raises(ParameterError):
        validate_healing_options({"base": policy})


def test_policy_uses_bounded_distinct_execution_candidates_and_keeps_ir_intact():
    from app.domain.cad_v2.models import CADDocumentIR
    original = CADDocumentIR.model_validate(plate_ir())
    error = {"code": "InvalidTopologyError", "feature_id": "bores", "recoverable": True,
             "details": {"healing_candidate": True}}
    history = []
    strategies = []
    for _ in range(3):
        repair = next_repair(original, error, history)
        assert repair["ir"] == original
        candidate = repair["execution_options"]["healing"]["bores"]
        strategies.append(candidate["strategy"])
        assert candidate["tolerance_mm"] <= 1e-5
        assert repair["patch"]["path"] == "execution_options.healing"
        history.append(repair)
    assert len(set(strategies)) == 3
    assert next_repair(original, error, history, max_attempts=5) is None
    assert next_repair(original, error, [], max_attempts=0) is None
    for code in ("GeometryKernelError", "BooleanOperationError", "ConstraintViolationError", "ExportError", "InfrastructureError"):
        assert next_repair(original, {**error, "code": code}, []) is None
    assert next_repair(original, {**error, "recoverable": False}, []) is None
    assert next_repair(original, {**error, "details": {}}, []) is None


def test_backend_and_policy_recover_actual_face_soup_after_bounded_failed_candidates(tmp_path):
    class BrokenSolidContainerBackend(Build123dBackend):
        """Inject a reproducible real OCP topology defect after the exact cut."""
        def _operation(self, feature, shapes):
            shape, evidence = super()._operation(feature, shapes)
            return (separate_faces(shape) if feature.id == "bores" else shape), evidence

    ir = plate_ir()
    compiled = compile_ir(ir)
    ir = compiled.source_ir
    history = []
    policy = None
    for _ in range(4):
        engine = BrokenSolidContainerBackend(healing_policy=policy)
        try:
            built = engine.build(compiled)
            break
        except InvalidTopologyError as error:
            repair = next_repair(ir, error.to_dict(), history)
            assert repair is not None, error.to_dict()
            assert repair["ir"].model_dump(mode="json") == compiled.source_ir
            history.append(repair)
            policy = repair["execution_options"]["healing"]
    else:
        pytest.fail("Closed face soup did not recover within the three candidate budget")
    assert len(history) == 3
    assert history[0]["execution_options"]["healing"]["bores"]["strategy"] == "shape_fix"
    assert built.trace[-1]["evidence"]["healing"]["strategy"] == "sew_closed_shells"
    assert validate(built)["status"] == "passed"
    engine.export(built, tmp_path)
    assert built.geometry["step_roundtrip_checks"]["status"] == "passed"
