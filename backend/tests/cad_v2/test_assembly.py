"""Real component B-Reps, independent STEP occurrences, GLB hierarchy and mates."""
from copy import deepcopy
import csv
import json
import math
import struct

import pytest
from build123d import import_step

from app.domain.cad_v2.compiler import apply_edits, compile_ir, compile_to_python, validate_ir
from app.domain.cad_v2.errors import DependencyError, ExportError, InvalidTopologyError, ParameterError
from app.infrastructure.cad_v2.backend import Build123dBackend


def flange_shaft_spacer_ir():
    return {
        "document_id": "flange-shaft-spacer",
        "parameters": {"shaft_z": {"value": -5}, "spacer_z": {"value": 5}},
        "features": [
            {"id": "flange_stock", "type": "Cylinder", "params": {"radius": 20, "height": 5}},
            {"id": "flange", "type": "Hole", "inputs": ["flange_stock"], "params": {"diameter": 10, "positions": [[0, 0, 0]]}},
            {"id": "shaft", "type": "Cylinder", "params": {"radius": 4.8, "height": 30}},
            {"id": "spacer_stock", "type": "Cylinder", "params": {"radius": 8, "height": 3}},
            {"id": "spacer", "type": "Hole", "inputs": ["spacer_stock"], "params": {"diameter": 10, "positions": [[0, 0, 0]]}},
        ],
        "assembly": {
            "id": "drive", "name": "Flange, shaft and spacer",
            "parts": [
                {"id": "flange_part", "name": "Flange", "feature_id": "flange", "color": [0.2, 0.4, 0.8, 1], "material": "Steel", "expected_geometry": {"bbox": [40, 40, 5], "volume": math.pi * (400-25)*5}},
                {"id": "shaft_part", "name": "Shaft", "feature_id": "shaft", "color": [0.8, 0.5, 0.1, 1], "material": "Steel", "expected_geometry": {"bbox": [9.6, 9.6, 30]}},
                {"id": "spacer_part", "name": "Spacer", "feature_id": "spacer", "color": [0.2, 0.7, 0.3, 1], "material": "Brass", "expected_geometry": {"bbox": [16, 16, 3]}},
            ],
            "instances": [
                {"id": "flange_1", "part_id": "flange_part"},
                {"id": "shaft_1", "part_id": "shaft_part", "transform": {"translation": [0, 0, "shaft_z"]}},
                {"id": "spacer_1", "part_id": "spacer_part", "transform": {"translation": [0, 0, "spacer_z"]}},
            ],
            "mates": [
                {"id": "ground", "type": "fixed", "instance_id": "flange_1"},
                {"id": "shaft_axis", "type": "concentric", "instance_id": "shaft_1", "reference_instance_id": "flange_1"},
                {"id": "spacer_face", "type": "aligned", "instance_id": "spacer_1", "reference_instance_id": "flange_1", "reference_origin": [0, 0, 5]},
            ],
        },
        "expected_geometry": {"bbox": [40, 40, 30]},
    }


def test_real_flange_shaft_spacer_assembly_export(tmp_path):
    backend = Build123dBackend()
    compiled = compile_ir(flange_shaft_spacer_ir())
    assert compiled.expected_geometry["solids"] == 3
    assert compile_ir(validate_ir(flange_shaft_spacer_ir()).model_dump(mode="json")).expected_geometry["solids"] == 3
    built = backend.build(compiled)
    report = backend.validate(built)
    assert report["status"] == "passed", [c for c in report["checks"] if c["status"] != "passed"]
    assert built.geometry["solids"] == 3
    assert len(built.shape.children) == 3
    assert built.geometry["bbox"]["min"] == pytest.approx([-20, -20, -5])
    assert all(pair["intersection_volume_mm3"] < 1e-7 for pair in report["assembly"]["pairs"])
    artifacts = backend.export(built, tmp_path)
    assert {a["format"] for a in artifacts} == {"step", "stl", "glb", "bom_json", "bom_csv"}
    restored = import_step(tmp_path / "model.step")
    # Read actual independent solids, names and placements from the STEP file.
    assert len(restored.solids()) == 3
    assert {child.label for child in restored.children} == {"flange_1", "shaft_1", "spacer_1"}
    instances = {child.label: child for child in restored.children}
    assert list(instances["shaft_1"].bounding_box().min) == pytest.approx([-4.8, -4.8, -5])
    assert list(instances["shaft_1"].bounding_box().max) == pytest.approx([4.8, 4.8, 25])
    assert list(instances["spacer_1"].bounding_box().min) == pytest.approx([-8, -8, 5])
    assert instances["flange_1"].volume == pytest.approx(math.pi * 375 * 5, rel=1e-8)
    assert instances["flange_1"].children[0].color != instances["shaft_1"].children[0].color
    raw = (tmp_path / "model.glb").read_bytes()
    json_length = struct.unpack_from("<I", raw, 12)[0]
    glb = json.loads(raw[20:20+json_length])
    glb_instances = {node["extras"]["instance_id"]: node for node in glb["nodes"] if "instance_id" in node.get("extras", {})}
    assert set(glb_instances) == set(instances)
    assert glb_instances["shaft_1"]["translation"] == pytest.approx([0, 0, -0.005])
    assert all(node["children"] for node in glb_instances.values())
    assert len(glb["materials"]) >= 3
    assert len({tuple(m["pbrMetallicRoughness"]["baseColorFactor"]) for m in glb["materials"]}) >= 3
    info = json.loads((tmp_path / "assembly.json").read_text())
    assert info["component_count"] == 3
    assert {part["part_id"]: part["quantity"] for part in info["bom"]} == {"flange_part": 1, "shaft_part": 1, "spacer_part": 1}
    with (tmp_path / "bom.csv").open() as file:
        rows = list(csv.DictReader(file))
    assert {row["name"] for row in rows} == {"Flange", "Shaft", "Spacer"}
    assert built.geometry["step_roundtrip_checks"]["status"] == "passed"


def test_collision_and_mate_violation_are_measured_failures():
    backend = Build123dBackend()
    ir = flange_shaft_spacer_ir()
    # Moving the shaft off axis creates a physically intersecting solid.
    ir["assembly"]["instances"][1]["transform"]["translation"] = [2, 0, -5]
    report = backend.validate(backend.build(compile_ir(ir)))
    assert report["status"] == "failed"
    collision = next(c for c in report["checks"] if c["name"] == "collision.flange_1.shaft_1")
    assert collision["status"] == "failed"
    assert collision["actual"]["intersection_volume_mm3"] > 1
    axis = next(c for c in report["checks"] if c["name"] == "mate.shaft_axis.concentric")
    assert axis["actual"]["axis_distance_mm"] == pytest.approx(2)
    assert axis["status"] == "failed"


@pytest.mark.parametrize("wrong_diameter", [False, True])
def test_feature_bound_hole_expectations_are_measured_on_the_part_with_inserted_shaft(wrong_diameter):
    backend = Build123dBackend()
    ir = flange_shaft_spacer_ir()
    ir["expected_geometry"]["holes"] = [
        {"feature_id": "flange", "diameter": 8 if wrong_diameter else 10, "positions": [[0, 0, 0]], "through": True},
        {"feature_id": "spacer", "diameter": 10, "positions": [[0, 0, 0]], "through": True},
    ]
    report = backend.validate(backend.build(compile_ir(ir)))
    assert report["status"] == ("failed" if wrong_diameter else "passed")
    assert any(check["name"].startswith("instance.flange_1.expected_hole") for check in report["checks"])


def test_unscoped_assembly_hole_still_checks_the_whole_assembly():
    backend = Build123dBackend()
    ir = flange_shaft_spacer_ir()
    ir["expected_geometry"]["holes"] = [{"diameter": 10, "positions": [[0, 0, 0]], "through": True}]
    report = backend.validate(backend.build(compile_ir(ir)))
    assert report["status"] == "failed"
    assert any(check["name"] == "expected_hole.0.0.through" and check["status"] == "failed" for check in report["checks"])


def test_duplicate_global_and_part_assertions_still_check_both_diameters():
    backend = Build123dBackend()
    ir = flange_shaft_spacer_ir()
    ir["assembly"]["parts"][0]["expected_geometry"]["holes"] = [{"feature_id": "flange", "diameter": 10, "positions": [[0, 0, 0]]}]
    ir["expected_geometry"]["holes"] = [{"feature_id": "flange", "diameter": 8, "positions": [[0, 0, 0]]}]
    report = backend.validate(backend.build(compile_ir(ir)))
    assert report["status"] == "failed"
    assert any(check["name"].startswith("instance.flange_1.source_hole") and check["status"] == "failed" for check in report["checks"])


def test_reusable_part_instances_and_atomic_parametric_instance_edits(tmp_path):
    backend = Build123dBackend()
    original = flange_shaft_spacer_ir()
    edited = apply_edits(original, parameters={"shaft_z": -7}, instance_patches={"spacer_1": {"transform": {"rotation": [0, 0, 45]}, "name": "Adjusted spacer"}}, add_instances=[{"id": "spacer_2", "part_id": "spacer_part", "transform": {"translation": [0, 0, 10]}}])
    built = backend.build(compile_ir(edited))
    assert backend.validate(built)["status"] == "passed"
    assert len(built.shape.solids()) == 4
    assert list(built.components["shaft_1"].shape.bounding_box().min) == pytest.approx([-4.8, -4.8, -7])
    spacer_bom = next(part for part in built.geometry["assembly"]["bom"] if part["part_id"] == "spacer_part")
    assert spacer_bom["quantity"] == 2
    assert spacer_bom["instance_ids"] == ["spacer_1", "spacer_2"]
    backend.export(built, tmp_path)
    restored = import_step(tmp_path / "model.step")
    assert len(restored.children) == 4
    assert len(restored.solids()) == 4
    assert original["parameters"]["shaft_z"]["value"] == -5
    assert "backend.validate(result)" in compile_to_python(edited)


@pytest.mark.parametrize("mutation", [
    lambda ir: ir["assembly"]["instances"][0].update(part_id="absent"),
    lambda ir: ir["assembly"]["parts"][0].update(feature_id="absent"),
    lambda ir: ir["assembly"]["mates"][0].update(instance_id="absent"),
    lambda ir: ir["assembly"]["instances"].append(deepcopy(ir["assembly"]["instances"][0])),
])
def test_assembly_references_and_stable_ids(mutation):
    ir = flange_shaft_spacer_ir()
    mutation(ir)
    with pytest.raises(DependencyError):
        compile_ir(ir)


def test_protected_instances_and_dangling_mates():
    ir = flange_shaft_spacer_ir()
    ir["assembly"]["instances"][0]["editable"] = False
    with pytest.raises(ParameterError):
        apply_edits(ir, instance_patches={"flange_1": {"name": "changed"}})
    with pytest.raises(DependencyError):
        apply_edits(ir, delete_instances=["shaft_1"])
    with pytest.raises(ParameterError):
        apply_edits(ir, instance_patches={"shaft_1": {"id": "renamed"}})


def test_multi_solid_pattern_cannot_masquerade_as_a_part():
    ir = flange_shaft_spacer_ir()
    ir["features"].append({"id": "two_shafts", "type": "LinearPattern", "inputs": ["shaft"], "params": {"count": 2, "spacing": [50, 0, 0]}})
    ir["assembly"]["parts"][1]["feature_id"] = "two_shafts"
    with pytest.raises(InvalidTopologyError):
        Build123dBackend().build(compile_ir(ir))


def test_assembly_length_units_and_rotation_pose():
    ir = {"units": "cm", "features": [{"id": "body", "type": "Box", "params": {"width": 1, "depth": 2, "height": 3}}], "assembly": {"parts": [{"id": "part", "name": "Block", "feature_id": "body"}], "instances": [{"id": "placed", "part_id": "part", "transform": {"translation": [4, 5, 6], "rotation": [0, 0, 90]}}], "mates": [{"id": "position", "type": "fixed", "instance_id": "placed", "transform": {"translation": [4, 5, 6], "rotation": [0, 0, 90]}}]}}
    backend = Build123dBackend()
    result = backend.build(compile_ir(ir))
    assert backend.validate(result)["status"] == "passed"
    assert result.geometry["bbox"]["min"] == pytest.approx([20, 50, 60])
    assert result.geometry["bbox"]["max"] == pytest.approx([40, 60, 90])


def test_glb_export_rejects_a_corrupted_component_pose(tmp_path, monkeypatch):
    import app.infrastructure.cad_v2.backend as module
    original_export = module.export_gltf
    def corrupt(shape, path, **kwargs):
        success = original_export(shape, path, **kwargs)
        raw = path.read_bytes()
        length = struct.unpack_from("<I", raw, 12)[0]
        content = json.loads(raw[20:20+length])
        shaft = next(node for node in content["nodes"] if node.get("name") == "shaft_1")
        shaft["translation"][0] = 0.001
        encoded = json.dumps(content, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        remainder = raw[20+length:]
        path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20+len(encoded)+len(remainder)) + struct.pack("<I4s", len(encoded), b"JSON") + encoded + remainder)
        return success
    monkeypatch.setattr(module, "export_gltf", corrupt)
    backend = Build123dBackend()
    built = backend.build(compile_ir(flange_shaft_spacer_ir()))
    with pytest.raises(ExportError, match="component pose"):
        backend.export(built, tmp_path)


def test_step_component_integrity_is_not_inferred_from_aggregate_bounds(tmp_path):
    from build123d import Location
    from app.infrastructure.cad_v2.assembly import restore_components
    backend = Build123dBackend()
    built = backend.build(compile_ir(flange_shaft_spacer_ir()))
    backend.export(built, tmp_path)
    restored = import_step(tmp_path / "model.step")
    shaft = next(child for child in restored.children if child.label == "shaft_1")
    shaft.location = Location((1, 0, -5))
    with pytest.raises(ExportError, match="individual component"):
        restore_components(built, restored)
