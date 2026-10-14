"""Explicit part definitions and located component instances; no implicit fusion."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import csv
import json
import math
from pathlib import Path
import struct
from typing import Any

from build123d import Color, Compound, Location

from app.domain.cad_v2.compiler import CompiledFeature
from app.domain.cad_v2.errors import ExportError, InvalidTopologyError
from .backend import BuildResult, _volume, measure


@dataclass
class Component:
    instance_id: str
    part_id: str
    name: str
    export_name: str
    shape: Any
    local_shape: Any
    definition: dict[str, Any]
    instance: dict[str, Any]


def pose_location(transform: dict[str, Any]) -> Location:
    """Millimetres, then extrinsic X/Y/Z rotations in degrees, like Transform."""
    result = Location()
    for axis, angle in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), transform["rotation"]):
        result = Location((0, 0, 0), axis, angle) * result
    return Location(transform["translation"]) * result


def location_matrix(location: Location) -> list[list[float]]:
    matrix = location.wrapped.Transformation()
    return [[matrix.Value(row, column) for column in range(1, 5)] for row in range(1, 4)] + [[0., 0., 0., 1.]]


def frame_vector(location: Location, vector: list[float], *, point: bool) -> list[float]:
    matrix = location_matrix(location)
    return [sum(matrix[i][j] * vector[j] for j in range(3)) + (matrix[i][3] if point else 0) for i in range(3)]


def _pose_error(actual: Location, expected: Location) -> tuple[float, float]:
    delta = expected.inverse() * actual
    matrix = location_matrix(delta)
    distance = math.sqrt(sum(matrix[i][3] ** 2 for i in range(3)))
    cosine = max(-1., min(1., (sum(matrix[i][i] for i in range(3)) - 1) / 2))
    return distance, math.degrees(math.acos(cosine))


def build_assembly(compiled: Any, shapes: dict[str, Any], trace: list[dict[str, Any]]) -> BuildResult:
    assembly = compiled.assembly
    parts = {part["id"]: part for part in assembly["parts"]}
    for part in parts.values():
        shape = shapes[part["feature_id"]]
        if len(shape.solids()) != 1 or _volume(shape) <= 1e-9:
            raise InvalidTopologyError("Assembly part definitions require exactly one positive-volume solid", feature_id=part["feature_id"], details={"part_id": part["id"]})
    components: dict[str, Component] = {}
    for instance in assembly["instances"]:
        part = parts[instance["part_id"]]
        source = shapes[part["feature_id"]]
        local = deepcopy(source)
        local.label = f"{part['id']}: {part['name']}"
        local.color = Color(*part["color"])
        name = instance.get("name") or part["name"]
        export_name = instance["id"]
        located = Compound(children=[local], label=export_name, color=Color(*part["color"]), material=part.get("material") or "")
        located.location = pose_location(instance["transform"])
        components[instance["id"]] = Component(instance["id"], part["id"], name, export_name, located, source, part, instance)
    shape = Compound(children=[component.shape for component in components.values()], label=f"{assembly['id']}: {assembly['name']}")
    result = BuildResult(shape=shape, trace=trace, geometry=measure(shape), compiled=compiled, components=components)
    result.geometry["assembly"] = assembly_properties(result)
    return result


def assembly_properties(result: BuildResult) -> dict[str, Any]:
    assembly = result.compiled.assembly
    bom = []
    for part in assembly["parts"]:
        instances = [c for c in result.components.values() if c.part_id == part["id"]]
        bom.append({"part_id": part["id"], "name": part["name"], "feature_id": part["feature_id"], "material": part.get("material"), "color": part["color"], "quantity": len(instances), "instance_ids": [c.instance_id for c in instances], "unit_volume_mm3": _volume(instances[0].local_shape) if instances else None})
    return {"id": assembly["id"], "name": assembly["name"], "component_count": len(result.components), "bom": bom,
            "components": [{"instance_id": c.instance_id, "part_id": c.part_id, "name": c.name, "export_name": c.export_name, "transform_matrix_mm": location_matrix(c.shape.location), "color": c.definition["color"], "geometry": measure(c.shape)} for c in result.components.values()]}


def validate_assembly(result: BuildResult) -> dict[str, Any]:
    from .validation import _check, validate
    compiled, assembly = result.compiled, result.compiled.assembly
    by_id = {feature.id: feature for feature in compiled.features}

    def ancestors(feature_id: str) -> set[str]:
        found: set[str] = set()
        def visit(key: str) -> None:
            if key not in found:
                found.add(key)
                for parent in by_id[key].inputs:
                    visit(parent)
        visit(feature_id)
        return found

    part_features = set().union(*(ancestors(part["feature_id"]) for part in assembly["parts"]))
    global_compiled = compiled.model_copy(update={
        "features": [CompiledFeature(id=assembly["id"], type="Assembly", inputs=[], depends_on=[], params={})],
        "output_feature": assembly["id"], "assembly": None,
        "constraints": [c for c in compiled.constraints if c.get("feature_id") not in part_features],
    })
    # Refresh from the B-Rep. A stale geometry dictionary must not make a damaged
    # exported or edited component pass validation.
    result.geometry.update(measure(result.shape))
    # Kernel Boolean operations on a hierarchical Compound may use child-local
    # coordinates. This measurement-only view uses world-located leaf solids.
    world_shape = Compound(result.shape.solids())
    checks = validate(BuildResult(world_shape, [], result.geometry, global_compiled))["checks"]
    expected_ids = {instance["id"] for instance in assembly["instances"]}
    checks.append(_check("assembly.instance_ids", sorted(expected_ids), sorted(result.components), expected_ids == set(result.components), evidence="Named B-Rep assembly components"))
    part_reports: dict[str, Any] = {}
    for component in result.components.values():
        part = component.definition
        local = component.local_shape
        part_compiled = compiled.model_copy(update={"output_feature": part["feature_id"], "assembly": None, "expected_geometry": part["expected_geometry"], "constraints": [c for c in compiled.constraints if c.get("feature_id") in ancestors(part["feature_id"])]})
        # Verify each instance independently; a STEP importer may mutate just one
        # occurrence of a reusable definition.
        report = validate(BuildResult(local, result.trace, measure(local), part_compiled))
        part_reports[component.instance_id] = report["status"]
        checks.extend([{**check, "name": f"instance.{component.instance_id}.{check['name']}"} for check in report["checks"]])
        actual = component.shape.location
        expected = pose_location(component.instance["transform"])
        distance, angle = _pose_error(actual, expected)
        checks.append(_check(f"instance.{component.instance_id}.placement", component.instance["transform"], {"translation_error_mm": distance, "rotation_error_deg": angle, "matrix_mm": location_matrix(actual)}, distance <= 1e-5 and angle <= 1e-5, 1e-5, "OpenCascade component TopLoc_Location compared with explicit pose"))
        checks.append(_check(f"instance.{component.instance_id}.one_solid", 1, len(component.shape.solids()), len(component.shape.solids()) == 1, evidence="Located component B-Rep traversal"))
    for mate in assembly["mates"]:
        if mate["instance_id"] not in result.components or (mate["reference_instance_id"] is not None and mate["reference_instance_id"] not in result.components):
            checks.append(_check(f"mate.{mate['id']}", "named components exist", None, False))
            continue
        actual = result.components[mate["instance_id"]].shape.location
        reference = result.components[mate["reference_instance_id"]].shape.location if mate["reference_instance_id"] else Location()
        tolerance, angular_tolerance = mate["tolerance"], mate["angular_tolerance"]
        if mate["type"] == "fixed":
            distance, angle = _pose_error(reference.inverse() * actual, pose_location(mate["transform"]))
            measured = {"translation_error_mm": distance, "rotation_error_deg": angle}
            passed = distance <= tolerance and angle <= angular_tolerance
            expected = mate["transform"]
        else:
            origin = frame_vector(actual, mate["origin"], point=True)
            reference_origin = frame_vector(reference, mate["reference_origin"], point=True)
            axis = frame_vector(actual, mate["axis"], point=False)
            reference_axis = frame_vector(reference, mate["reference_axis"], point=False)
            dot = max(-1., min(1., sum(a*b for a, b in zip(axis, reference_axis))))
            angle = math.degrees(math.acos(abs(dot) if mate["type"] == "concentric" else dot))
            delta = [a-b for a, b in zip(origin, reference_origin)]
            axial = sum(a*b for a, b in zip(delta, reference_axis))
            radial = math.sqrt(sum((delta[i] - axial*reference_axis[i])**2 for i in range(3)))
            measured = {"axis_angle_deg": angle, "signed_offset_mm": axial, "axis_distance_mm": radial}
            passed = angle <= angular_tolerance and (radial <= tolerance if mate["type"] == "concentric" else abs(axial-mate["offset"]) <= tolerance)
            expected = {"axis_distance_mm": 0} if mate["type"] == "concentric" else {"signed_offset_mm": mate["offset"]}
        checks.append(_check(f"mate.{mate['id']}.{mate['type']}", expected, measured, passed, tolerance, "Independent component B-Rep placement frames; explicit local datum origins and axes"))
    collisions = []
    if assembly["collision_check"]:
        components = list(result.components.values())
        for index, first in enumerate(components):
            for second in components[index+1:]:
                intersection = first.shape.solids()[0] & second.shape.solids()[0]
                volume = abs(_volume(intersection))
                collision = {"first": first.instance_id, "second": second.instance_id, "intersection_volume_mm3": volume}
                collisions.append(collision)
                checks.append(_check(f"collision.{first.instance_id}.{second.instance_id}", {"maximum_intersection_volume_mm3": assembly["collision_tolerance"]}, collision, volume <= assembly["collision_tolerance"], assembly["collision_tolerance"], "Exact OpenCascade Boolean common of independent located solids; touching faces are allowed"))
    status = "failed" if any(c["status"] == "failed" for c in checks) else "unverified" if any(c["status"] == "unverified" for c in checks) else "passed"
    report = {"status": status, "checks": checks, "tolerance_mm": compiled.expected_geometry["tolerance"], "scope": "Independent part B-Reps, explicit component placements, supported datum mates and volumetric collisions", "assembly": {"component_count": len(result.components), "parts": part_reports, "collision_check_enabled": assembly["collision_check"], "pairs": collisions}}
    result.geometry["assembly"] = assembly_properties(result)
    result.validation = report
    return report


def restore_components(original: BuildResult, restored_shape: Any) -> BuildResult:
    """Find actual exported occurrences by stable name, preserving separate solids."""
    descendants: dict[str, list[Any]] = {}
    def walk(shape: Any) -> None:
        descendants.setdefault(shape.label, []).append(shape)
        for child in shape.children:
            walk(child)
    walk(restored_shape)
    components = {}
    for original_component in original.components.values():
        matches = descendants.get(original_component.export_name, [])
        if len(matches) != 1:
            raise ExportError("STEP assembly hierarchy lost a component identity", details={"instance_id": original_component.instance_id, "matches": len(matches)})
        shape = matches[0]
        before, after = measure(original_component.shape), measure(shape)
        same_geometry = (after["valid"] and before["solids"] == after["solids"]
                         and math.isclose(before["volume"], after["volume"], rel_tol=1e-7, abs_tol=1e-5)
                         and all(math.isclose(a, b, abs_tol=1e-5, rel_tol=0) for key in ("min", "max", "size") for a, b in zip(before["bbox"][key], after["bbox"][key])))
        if not same_geometry:
            raise ExportError("STEP roundtrip changed an individual component", details={"instance_id": original_component.instance_id, "before": before, "after": after})
        local = shape.moved(shape.location.inverse())
        components[original_component.instance_id] = Component(original_component.instance_id, original_component.part_id, original_component.name, original_component.export_name, shape, local, original_component.definition, original_component.instance)
    return BuildResult(restored_shape, original.trace, measure(restored_shape), original.compiled, components=components)


def export_bom(result: BuildResult, output_dir: Path) -> list[Path]:
    info = assembly_properties(result)
    json_path, csv_path = output_dir / "assembly.json", output_dir / "bom.csv"
    json_path.write_text(json.dumps(info, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["part_id", "name", "material", "quantity", "instance_ids", "unit_volume_mm3"])
        writer.writeheader()
        for part in info["bom"]:
            row = {key: part[key] for key in writer.fieldnames}
            row["instance_ids"] = ";".join(row["instance_ids"])
            writer.writerow(row)
    return [json_path, csv_path]


def annotate_glb(result: BuildResult, path: Path) -> dict[str, Any]:
    """Retain XCAF's actual hierarchy/poses and add stable application identities."""
    data = path.read_bytes()
    if data[:4] != b"glTF" or struct.unpack_from("<I", data, 4)[0] != 2:
        raise ExportError("GLB writer did not produce a version 2 container")
    length, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON":
        raise ExportError("GLB has no leading JSON chunk")
    content = json.loads(data[20:20+length])
    nodes = content.get("nodes", [])
    indices: dict[str, int] = {}
    for component in result.components.values():
        matches = [(index, node) for index, node in enumerate(nodes) if node.get("name") == component.export_name]
        if len(matches) != 1 or not matches[0][1].get("children"):
            raise ExportError("GLB lost the component hierarchy", details={"instance_id": component.instance_id})
        index, node = matches[0]
        if "matrix" in node:
            actual_matrix = [[node["matrix"][4*j+i] for j in range(4)] for i in range(4)]
        else:
            x, y, z, w = node.get("rotation", [0, 0, 0, 1])
            translation = node.get("translation", [0, 0, 0])
            actual_matrix = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), translation[0]],
                             [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), translation[1]],
                             [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), translation[2]],
                             [0, 0, 0, 1]]
        for row in actual_matrix[:3]:
            row[3] *= 1000
        expected_matrix = location_matrix(component.shape.location)
        if (node.get("scale", [1, 1, 1]) != [1, 1, 1]
                or any(abs(actual_matrix[i][j]-expected_matrix[i][j]) > 1e-5 for i in range(4) for j in range(4))):
            raise ExportError("GLB changed an individual component pose", details={"instance_id": component.instance_id})
        node["extras"] = {"instance_id": component.instance_id, "part_id": component.part_id, "display_name": component.name}
        indices[component.instance_id] = index
    content.setdefault("asset", {}).setdefault("extras", {}).update({"cad_document_id": result.compiled.document_id, "assembly_id": result.compiled.assembly["id"], "source_units": "mm", "source_up_axis": "Z"})
    encoded = json.dumps(content, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    encoded += b" " * (-len(encoded) % 4)
    remainder = data[20+length:]
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20+len(encoded)+len(remainder)) + struct.pack("<I4s", len(encoded), b"JSON") + encoded + remainder)
    return {"status": "passed", "component_nodes": indices, "units": "metres", "up_axis": "Y", "source_up_axis": "Z"}
