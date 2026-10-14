"""Deterministic build123d adapter; only trusted operations from validated IR run here."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import time
from typing import Any

from build123d import (
    Align, Axis, Box, Circle, Color, Compound, Cylinder, GeomType, Plane,
    Polyline, Pos, Rectangle, Shape, Sphere, Vector, Vertex,
    chamfer, export_gltf, export_step, export_stl, extrude, fillet, import_step,
    make_face, revolve,
)
from OCP.BRepAdaptor import BRepAdaptor_Surface
from app.domain.cad_v2.errors import (
    CADError, BooleanOperationError, ExportError, GeometryKernelError,
    InvalidTopologyError, ParameterError,
)


@dataclass
class BuildResult:
    shape: Any
    trace: list[dict[str, Any]] = field(default_factory=list)
    geometry: dict[str, Any] = field(default_factory=dict)
    compiled: Any = None
    validation: dict[str, Any] = field(default_factory=dict)


def _v(value: Any) -> tuple[float, float, float]:
    return tuple(float(x) for x in value)


def measure(shape: Shape) -> dict[str, Any]:
    box = shape.bounding_box()
    return {
        "bbox": {"min": list(box.min), "max": list(box.max), "size": list(box.size)},
        "volume": float(shape.volume), "area": float(shape.area),
        "solids": len(shape.solids()), "faces": len(shape.faces()),
        "edges": len(shape.edges()), "valid": bool(shape.is_valid), "units": "mm",
    }


def _volume(shape: Shape | None) -> float:
    return float(shape.volume) if shape is not None and shape.solids() else 0.0


def _compound(shapes: list[Shape], fuse: bool = False) -> Shape:
    if not shapes:
        raise GeometryKernelError("Operation produced no shapes")
    if not fuse:
        return Compound(children=shapes)
    result = shapes[0]
    for shape in shapes[1:]:
        result = result + shape
    return result


def _axis_points(shape: Shape, direction: tuple[float, float, float]) -> tuple[float, float]:
    b = shape.bounding_box()
    points = [(x, y, z) for x in (b.min.X, b.max.X)
              for y in (b.min.Y, b.max.Y) for z in (b.min.Z, b.max.Z)]
    projections = [sum(a * b for a, b in zip(p, direction)) for p in points]
    return min(projections), max(projections)


def _direction(value: Any) -> tuple[float, float, float]:
    vector = _v(value)
    length = math.sqrt(sum(x*x for x in vector))
    if length <= 1e-12:
        raise ParameterError("Direction must be nonzero")
    return tuple(x / length for x in vector)


def surface_signature(face: Any) -> dict[str, Any]:
    """Geometric surface identity independent of unstable face indices."""
    kind = face.geom_type.name
    adaptor = BRepAdaptor_Surface(face.wrapped)
    bbox = face.bounding_box()
    signature: dict[str, Any] = {"type": kind, "bbox_min": list(bbox.min), "bbox_max": list(bbox.max)}
    if kind == "CYLINDER":
        surface = adaptor.Cylinder()
        point, direction = surface.Location(), surface.Axis().Direction()
        origin, axis = [point.X(),point.Y(),point.Z()], [direction.X(),direction.Y(),direction.Z()]
        if next((x for x in axis if abs(x)>1e-8),1)<0:
            axis=[-x for x in axis]
        projection=sum(a*b for a,b in zip(origin,axis))
        signature.update(radius=surface.Radius(),axis=axis,origin=[origin[i]-projection*axis[i] for i in range(3)])
    elif kind == "TORUS":
        surface=adaptor.Torus()
        center=surface.Location()
        signature.update(radius=surface.MinorRadius(),major_radius=surface.MajorRadius(),origin=[center.X(),center.Y(),center.Z()])
    elif kind == "SPHERE":
        surface=adaptor.Sphere()
        center=surface.Location()
        signature.update(radius=surface.Radius(),origin=[center.X(),center.Y(),center.Z()])
    elif kind == "PLANE":
        normal=list(face.normal_at())
        if next((x for x in normal if abs(x)>1e-8),1)<0:
            normal=[-x for x in normal]
        signature.update(normal=normal,offset=sum(a*b for a,b in zip(normal,face.center())))
    return signature


class Build123dBackend:
    """Precise B-Rep construction; model-generated Python is never accepted."""

    def build(self, compiled: Any) -> BuildResult:
        shapes: dict[str, Shape] = {}
        trace: list[dict[str, Any]] = []
        for feature in compiled.features:
            before = shapes.get(feature.inputs[0]) if feature.inputs else None
            start = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                shape, evidence = self._operation(feature, shapes)
                if not shape.is_valid:
                    raise InvalidTopologyError("Kernel returned invalid topology", feature_id=feature.id)
                shapes[feature.id] = shape
                trace.append({
                    "feature_id": feature.id, "type": feature.type,
                    "started_at": started_at,
                    "duration_seconds": round(time.perf_counter()-start, 6),
                    "before_volume": _volume(before), "after_volume": _volume(shape),
                    "before_faces": len(before.faces()) if before is not None else 0,
                    "after_faces": len(shape.faces()), "solids": len(shape.solids()),
                    "evidence": evidence,
                })
            except CADError as exc:
                if exc.feature_id is None:
                    exc.feature_id = feature.id
                exc.details.setdefault("trace", trace)
                raise
            except Exception as exc:
                error = BooleanOperationError if feature.type in {"Union", "Cut", "Intersection", "Hole", "Pocket"} else GeometryKernelError
                raise error(str(exc), feature_id=feature.id, details={"operation": feature.type, "trace": trace}) from exc
        result = shapes[compiled.output_feature]
        if not result.solids() or _volume(result) <= 1e-9:
            raise InvalidTopologyError("Output contains no positive-volume solid", feature_id=compiled.output_feature)
        return BuildResult(result, trace, measure(result), compiled)

    def _operation(self, feature: Any, shapes: dict[str, Shape]) -> tuple[Shape, dict[str, Any]]:
        p = feature.params
        inputs = [shapes[key] for key in feature.inputs]
        kind = feature.type
        info: dict[str, Any] = {}
        if kind == "Box":
            result = Pos(*p.get("origin", (0, 0, 0))) * Box(p["width"], p["depth"], p["height"], align=Align.MIN)
        elif kind == "Cylinder":
            result = Pos(*p.get("origin", (0, 0, 0))) * Cylinder(p["radius"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        elif kind == "Sphere":
            result = Pos(*p.get("center", (0, 0, 0))) * Sphere(p["radius"])
        elif kind in {"Rectangle", "Circle", "Polyline"}:
            if kind == "Rectangle":
                sketch = Rectangle(p["width"], p["height"], align=Align.MIN)
            elif kind == "Circle":
                sketch = Circle(p["radius"])
            else:
                if not p.get("closed", True):
                    raise ParameterError("Only closed Polyline profiles can create a solid")
                sketch = make_face(Polyline(*(tuple(point) for point in p["points"]), close=True))
            result = (getattr(Plane, p.get("plane", "XY")) * sketch).translate(p.get("origin", (0, 0, 0)))
        elif kind == "Extrude":
            result = extrude(inputs[0], amount=p["amount"])
        elif kind == "Revolve":
            result = revolve(inputs[0], axis=Axis(p.get("axis_origin", (0, 0, 0)), p.get("axis_direction", (0, 0, 1))), revolution_arc=p.get("angle", 360))
        elif kind == "Hole":
            result = inputs[0]
            direction = _direction(p.get("direction", (0, 0, 1)))
            lo, hi = _axis_points(result, direction)
            positions = p["positions"]
            holes = []
            for pos in positions:
                center = _v(pos)
                if p.get("through", True):
                    projection = sum(a*b for a,b in zip(center,direction))
                    margin = max(1.0, (hi-lo)*0.01)
                    origin = tuple(center[i]+direction[i]*(lo-projection-margin) for i in range(3))
                    depth = hi-lo+2*margin
                else:
                    origin, depth = center, p["depth"]
                # Plane defines local Z along the requested bore axis, including side holes.
                tool = Plane(origin=origin, z_dir=direction) * Cylinder(p["diameter"]/2, depth, align=(Align.CENTER, Align.CENTER, Align.MIN))
                before_volume = _volume(result)
                result = result - tool
                removed = before_volume-_volume(result)
                if removed <= 1e-8:
                    raise BooleanOperationError("Hole does not remove material", feature_id=feature.id)
                holes.append({"position": list(center), "direction": list(direction), "diameter": p["diameter"], "through": p.get("through", True), "depth": p.get("depth"), "removed_volume": removed})
            info["holes"] = holes
        elif kind == "Pocket":
            tool = extrude(inputs[1], amount=p["depth"], dir=p.get("direction", (0, 0, 1)))
            result = inputs[0]-tool
            if _volume(inputs[0])-_volume(result) <= 1e-8:
                raise BooleanOperationError("Pocket does not remove material", feature_id=feature.id)
        elif kind in {"Fillet", "Chamfer"}:
            selected = self.select(inputs[0], p["selector"], feature.id)
            if p["selector"].get("kind", "edge") != "edge":
                raise ParameterError("Fillet and Chamfer require an edge selector", feature_id=feature.id)
            result = fillet(selected, radius=p["radius"]) if kind == "Fillet" else chamfer(selected, length=p["length"])
            added_faces = [face for face in result.faces() if not any(face.is_same(old) for old in inputs[0].faces())]
            if kind == "Fillet":
                signatures = [surface_signature(face) for face in added_faces]
                info["finish_surfaces"] = [s for s in signatures if "radius" in s and math.isclose(s["radius"],p["radius"],abs_tol=1e-5)]
                info["measured_radii"] = [item["radius"] for item in info["finish_surfaces"]]
            else:
                finish = []
                measured = []
                for edge in selected:
                    candidates = []
                    for face in added_faces:
                        if face.geom_type != GeomType.PLANE:
                            continue
                        distances = [vertex.distance_to(edge) for vertex in face.vertices()]
                        if distances and all(math.isclose(d,p["length"],abs_tol=1e-5) for d in distances):
                            candidates.append((face,distances))
                    if len(candidates) == 1:
                        face, distances = candidates[0]
                        finish.append(surface_signature(face))
                        measured.extend(distances)
                info["finish_surfaces"] = finish
                info["measured_lengths"] = measured
                info["length_verified"] = len(finish)==len(selected)
            info["selected_edges"] = len(selected)
            info["selected_centers"] = [list(edge.center()) for edge in selected]
            info["topology_changed"] = len(result.faces()) != len(inputs[0].faces())
            info["volume_changed"] = abs(_volume(result)-_volume(inputs[0])) > 1e-8
            if not info["volume_changed"] or not info["topology_changed"]:
                raise InvalidTopologyError(f"{kind} did not produce a measurable geometric change", feature_id=feature.id)
        elif kind in {"Union", "Cut", "Intersection"}:
            result = inputs[0]
            for other in inputs[1:]:
                result = result + other if kind == "Union" else result - other if kind == "Cut" else result & other
            if not result.solids() or _volume(result) <= 1e-9:
                raise BooleanOperationError("Boolean result contains no solid", feature_id=feature.id)
            if kind == "Cut" and _volume(inputs[0])-_volume(result) <= 1e-8:
                raise BooleanOperationError("Cut does not remove material", feature_id=feature.id)
        elif kind == "LinearPattern":
            copies = [inputs[0].translate(tuple(i*x for x in p["spacing"])) for i in range(p["count"])]
            result = _compound(copies, p.get("fuse", False))
        elif kind == "CircularPattern":
            axis = Axis(p.get("axis_origin", (0,0,0)), p.get("axis_direction", (0,0,1)))
            copies = [inputs[0].rotate(axis, i*p.get("angle",360)/p["count"]) for i in range(p["count"])]
            result = _compound(copies, p.get("fuse", False))
        elif kind == "Transform":
            result = inputs[0]
            for axis, angle in zip((Axis.X, Axis.Y, Axis.Z), p.get("rotation", (0,0,0))):
                if angle:
                    result = result.rotate(axis, angle)
            result = result.translate(p.get("translation", (0,0,0)))
        else:
            raise ParameterError(f"Unsupported operation {kind}", feature_id=feature.id)
        return result, info

    @staticmethod
    def select(shape: Shape, selector: dict[str, Any], feature_id: str) -> list[Any]:
        tolerance = selector.get("tolerance", 1e-4)
        expected = selector.get("expected_count", 1)
        def matches(item: Any, tol: float) -> bool:
            if selector.get("geometry_type") and item.geom_type.name != selector["geometry_type"].upper():
                return False
            center = tuple(item.center())
            if selector.get("parallel") is not None:
                direction = _direction(selector["parallel"])
                actual = tuple(item.tangent_at(0.5)) if selector.get("kind", "edge") == "edge" else tuple(item.normal_at())
                if abs(abs(sum(a*b for a,b in zip(actual,direction)))-1)>min(tol, 1e-3):
                    return False
            if selector.get("center") is not None and math.dist(center, selector["center"]) > tol:
                return False
            if selector.get("near") is not None and item.distance_to(Vertex(*selector["near"])) > tol:
                return False
            for i in range(3):
                if selector.get("min") is not None and center[i] < selector["min"][i]-tol:
                    return False
                if selector.get("max") is not None and center[i] > selector["max"][i]+tol:
                    return False
            return True
        items = list(shape.edges() if selector.get("kind", "edge") == "edge" else shape.faces())
        selected = [item for item in items if matches(item, tolerance)]
        if len(selected) != expected:
            # Only report a bounded precision repair when it produces exactly the requested set.
            recommended = min(1e-4, min(shape.bounding_box().size)*1e-5)
            relaxed = [item for item in items if matches(item, recommended)] if tolerance < recommended else []
            recoverable = not selected and len(relaxed) == expected
            raise InvalidTopologyError(
                f"Semantic selector matched {len(selected)} entities; expected {expected}",
                feature_id=feature_id, recoverable=recoverable,
                details={"selector_tolerance": tolerance, "recommended_tolerance": recommended if recoverable else None, "matched": len(selected), "expected_count": expected},
            )
        return selected

    def export(self, result: BuildResult, output_dir: Path) -> list[dict[str, Any]]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result.shape.color = Color(0.35, 0.55, 0.8)
        result.shape.label = "Agent-CAD part"
        try:
            if not export_step(result.shape, output_dir/"model.step"):
                raise ExportError("STEP writer returned failure")
            restored = import_step(output_dir/"model.step")
            roundtrip = measure(restored)
            before = result.geometry
            same = (roundtrip["valid"] and roundtrip["solids"]==before["solids"]
                    and math.isclose(roundtrip["volume"], before["volume"], rel_tol=1e-7, abs_tol=1e-5)
                    and all(math.isclose(a,b,abs_tol=1e-5) for key in ("min","max","size") for a,b in zip(roundtrip["bbox"][key],before["bbox"][key])))
            if not same:
                raise ExportError("STEP roundtrip changed geometry", details={"before": before, "after": roundtrip})
            from .validation import validate
            roundtrip_validation=validate(BuildResult(restored,result.trace,roundtrip,result.compiled))
            if roundtrip_validation["status"] != "passed":
                raise ExportError("STEP roundtrip geometry could not be fully verified",details={"validation":roundtrip_validation})
            result.geometry["step_roundtrip"] = roundtrip
            result.geometry["step_roundtrip_checks"] = {"status":roundtrip_validation["status"],"count":len(roundtrip_validation["checks"])}
            if not export_stl(result.shape, output_dir/"model.stl", tolerance=0.08, angular_tolerance=0.15):
                raise ExportError("STL writer returned failure")
            if not export_gltf(result.shape, output_dir/"model.glb", binary=True, linear_deflection=0.08, angular_deflection=0.15):
                raise ExportError("GLB writer returned failure")
        except CADError:
            raise
        except Exception as exc:
            raise ExportError(str(exc)) from exc
        return [artifact(path, path.suffix.lstrip(".")) for path in (output_dir/"model.step",output_dir/"model.stl",output_dir/"model.glb")]

    def validate(self, result: BuildResult) -> dict[str, Any]:
        from .validation import validate
        return validate(result)

    def properties(self, result: BuildResult) -> dict[str, Any]:
        return measure(result.shape)

    def query_topology(self, result: BuildResult, selector: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"type": item.geom_type.name, "center": list(item.center())}
                for item in self.select(result.shape, selector, result.compiled.output_feature)]

    def tessellate(self, result: BuildResult, tolerance: float = 0.08) -> dict[str, Any]:
        if not 0.001 <= tolerance <= 10:
            raise ParameterError("Preview tolerance must be between 0.001 and 10 mm")
        vertices, triangles = result.shape.tessellate(tolerance, 0.15)
        return {"vertices": [list(vertex) for vertex in vertices], "triangles": list(triangles), "units": "mm", "up_axis": "Z"}

    def cleanup(self) -> None:
        """Shapes are process-local and released when the isolated worker exits."""


def artifact(path: Path, kind: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {"format": kind, "filename": path.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
