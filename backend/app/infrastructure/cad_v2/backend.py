"""Deterministic build123d adapter; only trusted operations from validated IR run here."""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import time
from typing import Any

from build123d import (
    Align, Axis, Box, Circle, Color, Compound, Cylinder, GeomType, Helix, Plane,
    Polyline, Pos, Rectangle, Shape, Solid, Sphere, Spline, Vector, Vertex, Wire,
    chamfer, export_gltf, export_step, export_stl, extrude, fillet, import_step,
    loft, make_face, revolve, sweep,
)
from OCP.Adaptor3d import Adaptor3d_CurveOnSurface
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
from OCP.GProp import GProp_GProps
from OCP.GeomAdaptor import GeomAdaptor_Surface
from OCP.GeomLib import GeomLib_CheckCurveOnSurface
from OCP.ShapeFix import ShapeFix_Edge
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
    components: dict[str, Any] = field(default_factory=dict)


def _v(value: Any) -> tuple[float, float, float]:
    return tuple(float(x) for x in value)


def measure(shape: Shape) -> dict[str, Any]:
    box = shape.bounding_box()
    bounds = _precise_bounds(shape) if _has_freeform_geometry(shape) else {"min":list(box.min),"max":list(box.max),"size":list(box.size)}
    return {
        "bbox": bounds,
        # Compound.volume only sums immediate children in build123d. Traverse
        # solids so nested assembly mass includes every located occurrence.
        "volume": _volume(shape), "area": float(shape.area),
        "solids": len(shape.solids()), "faces": len(shape.faces()),
        "edges": len(shape.edges()), "valid": bool(shape.is_valid), "units": "mm",
    }


def _volume(shape: Shape | None) -> float:
    if shape is None:
        return 0.0
    if not _has_freeform_geometry(shape):
        return sum(float(solid.volume) for solid in shape.solids())
    volume = 0.0
    for solid in shape.solids():
        properties = GProp_GProps()
        error = BRepGProp.VolumePropertiesGK_s(solid.wrapped,properties,1e-11,True,True)
        if not math.isfinite(error) or error < 0 or not math.isfinite(properties.Mass()):
            raise GeometryKernelError("Adaptive B-Rep volume integration failed")
        volume += float(properties.Mass())
    return volume


def _has_freeform_geometry(shape: Shape) -> bool:
    return any(item.geom_type in {GeomType.BSPLINE,GeomType.BEZIER} for item in [*shape.faces(),*shape.edges()])


def _precise_bounds(shape: Shape) -> dict[str, list[float]]:
    # AddOptimal can miss interior spline extrema. Use a conservative box only
    # to place six planes, then let OCCT measure their closest B-Rep points.
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape.wrapped,bounds,False)
    coarse = bounds.Get()
    # An enclosing analytical rim (for example an internal thread's outer
    # cylinder) proves the same bound without searching every spline surface.
    analytic_edges = [edge for edge in shape.edges() if edge.geom_type in {GeomType.LINE,GeomType.CIRCLE,GeomType.ELLIPSE}]
    analytic = None
    if analytic_edges:
        analytic_box = Bnd_Box()
        BRepBndLib.AddOptimal_s(Compound(children=analytic_edges).wrapped,analytic_box,False,False)
        if not analytic_box.IsVoid():
            analytic = analytic_box.Get()
    span = max(coarse[i+3]-coarse[i] for i in range(3))
    margin = max(1.0,span*0.05)
    center = [(coarse[i]+coarse[i+3])/2 for i in range(3)]
    lower, upper = [], []
    for index in range(3):
        for side,output in ((0,lower),(1,upper)):
            if analytic is not None and abs(coarse[index+3*side]-analytic[index+3*side]) <= 1e-6:
                output.append(float(analytic[index+3*side]))
                continue
            origin = list(center)
            origin[index] = coarse[index+3*side] + (-margin if side==0 else margin)
            direction = [0,0,0]
            direction[index] = 1
            face = Plane(origin=origin,z_dir=direction)*Rectangle(2*span+2*margin,2*span+2*margin)
            extrema = BRepExtrema_DistShapeShape(shape.wrapped,face.wrapped,1e-9)
            if not extrema.IsDone() or not extrema.NbSolution():
                raise GeometryKernelError("Exact B-Rep axis bound measurement failed")
            point = extrema.PointOnShape1(1)
            output.append(float((point.X(),point.Y(),point.Z())[index]))
    return {"min":lower,"max":upper,"size":[b-a for a,b in zip(lower,upper)]}


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

    def __init__(self, *, healing_policy: dict[str, Any] | None = None):
        from .healing import validate_healing_options
        self.healing_policy = validate_healing_options(healing_policy)

    def build(self, compiled: Any) -> BuildResult:
        shapes: dict[str, Shape] = {}
        trace: list[dict[str, Any]] = []
        for feature in compiled.features:
            before = shapes.get(feature.inputs[0]) if feature.inputs else None
            start = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                shape, evidence = self._operation(feature, shapes)
                if feature.id in self.healing_policy:
                    from .healing import heal_shape
                    policy = self.healing_policy[feature.id]
                    require_solid = feature.type not in {"Rectangle", "Circle", "Polyline", "BSpline", "Path3D"} and (feature.type not in {"Transform", "LinearPattern", "CircularPattern"} or bool(before.solids()))
                    healed = heal_shape(shape, strategy=policy["strategy"], tolerance_mm=policy["tolerance_mm"], feature_id=feature.id, require_solid=require_solid)
                    shape = healed.shape
                    evidence["healing"] = healed.evidence
                if not shape.is_valid:
                    raise InvalidTopologyError("Kernel returned invalid topology", feature_id=feature.id, recoverable=True, details={"healing_candidate": True})
                if shape.solids() and _volume(shape) <= 1e-9:
                    raise InvalidTopologyError("Kernel returned non-positive solid volume", feature_id=feature.id, recoverable=True, details={"healing_candidate": True})
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
                error = BooleanOperationError if feature.type in {"Union", "Cut", "Intersection", "Hole", "Pocket", "HelicalThread"} else GeometryKernelError
                raise error(str(exc), feature_id=feature.id, details={"operation": feature.type, "trace": trace}) from exc
        if compiled.assembly is not None:
            from .assembly import build_assembly
            return build_assembly(compiled, shapes, trace)
        result = shapes[compiled.output_feature]
        if not result.solids() or _volume(result) <= 1e-9:
            raise InvalidTopologyError("Output contains no positive-volume solid", feature_id=compiled.output_feature, recoverable=True, details={"healing_candidate": True})
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
        elif kind in {"Rectangle", "Circle", "Polyline", "BSpline"}:
            if kind == "Rectangle":
                sketch = Rectangle(p["width"], p["height"], align=Align.MIN)
            elif kind == "Circle":
                sketch = Circle(p["radius"])
            elif kind == "Polyline":
                if not p.get("closed", True):
                    raise ParameterError("Only closed Polyline profiles can create a solid")
                sketch = make_face(Polyline(*(tuple(point) for point in p["points"]), close=True))
            else:
                sketch = make_face(Spline(*(tuple(point) for point in p["points"]), periodic=True))
            result = (getattr(Plane, p.get("plane", "XY")) * sketch).translate(p.get("origin", (0, 0, 0)))
        elif kind == "Path3D":
            points = [tuple(point) for point in p["points"]]
            result = Spline(*points,tangents=p.get("tangents")) if p["interpolation"] == "bspline" else Polyline(*points)
            info["path_length"] = float(result.length)
        elif kind == "Sweep":
            faces = list(inputs[0].faces())
            path = inputs[1]
            if len(faces) != 1 or faces[0].inner_wires():
                raise ParameterError("Sweep supports a single closed profile without inner wires", feature_id=feature.id)
            start = tuple(path.position_at(0))
            tangent = _direction(path.tangent_at(0))
            normal = _direction(faces[0].normal_at())
            if abs(sum(a*b for a, b in zip(tangent, normal))) < 1-1e-6 or faces[0].distance_to(Vertex(*start)) > 1e-6:
                raise ParameterError("Sweep profile must contain the path start and be normal to its tangent", feature_id=feature.id)
            result = sweep(inputs[0], path=path, is_frenet=p["is_frenet"])
            info.update(profile_area=float(faces[0].area), path_length=float(path.length), path_start=list(start))
        elif kind == "Loft":
            faces = []
            for section in inputs:
                section_faces = list(section.faces())
                if len(section_faces) != 1 or section_faces[0].inner_wires():
                    raise ParameterError("Loft supports single closed profiles without inner wires", feature_id=feature.id)
                faces.append(section_faces[0])
            normal = _direction(faces[0].normal_at())
            if any(abs(sum(a*b for a,b in zip(normal,_direction(face.normal_at())))) < 1-1e-6 for face in faces[1:]):
                raise ParameterError("Loft profile planes must be parallel", feature_id=feature.id)
            stations = [sum(a*b for a,b in zip(tuple(face.center()),normal)) for face in faces]
            differences = [b-a for a,b in zip(stations,stations[1:])]
            if not (all(delta > 1e-6 for delta in differences) or all(delta < -1e-6 for delta in differences)):
                raise ParameterError("Loft profiles must be ordered on distinct parallel planes", feature_id=feature.id)
            result = loft(inputs, ruled=p["ruled"])
            info.update(section_areas=[float(face.area) for face in faces], stations=stations, ruled=p["ruled"])
        elif kind == "HelicalThread":
            result, info = self._helical_thread(inputs[0], p, feature.id)
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
    def _helical_thread(body: Shape, params: dict[str, Any], feature_id: str) -> tuple[Shape, dict[str, Any]]:
        """Cut an explicit single-start V groove, rather than a cosmetic cylinder."""
        p = params
        major, minor = p["major_diameter"]/2, p["minor_diameter"]/2
        depth, pitch, length = major-minor, p["pitch"], p["length"]
        axis = _direction(p["axis_direction"])
        plane = Plane(origin=p["axis_origin"], z_dir=axis)
        origin_projection = sum(a*b for a,b in zip(p["axis_origin"],axis))
        wall_radius = minor if p["internal"] else major
        matching_walls = []
        for face in body.faces().filter_by(GeomType.CYLINDER):
            signature = surface_signature(face)
            adaptor = BRepAdaptor_Surface(face.wrapped)
            delta = [signature["origin"][i]-p["axis_origin"][i] for i in range(3)]
            along = sum(a*b for a,b in zip(delta,axis))
            distance = math.sqrt(sum((delta[i]-along*axis[i])**2 for i in range(3)))
            lower, upper = _axis_points(face,axis)
            if (math.isclose(signature["radius"],wall_radius,abs_tol=1e-6)
                    and abs(abs(sum(a*b for a,b in zip(signature["axis"],axis)))-1)<1e-6
                    and distance<=1e-6 and abs(adaptor.LastUParameter()-adaptor.FirstUParameter())>=2*math.pi-1e-6
                    and lower<=origin_projection+1e-6 and upper>=origin_projection+length-1e-6):
                matching_walls.append(signature)
        if len(body.solids()) != 1 or not matching_walls:
            raise ParameterError("Thread requires one solid with the requested complete cylindrical wall and axial extent", feature_id=feature_id)
        bore = plane * Cylinder(minor,length,align=(Align.CENTER,Align.CENTER,Align.MIN))
        outer = plane * Cylinder(major,length,align=(Align.CENTER,Align.CENTER,Align.MIN))
        stock = outer-bore if p["internal"] else outer
        supported_volume = _volume(body & stock)
        if not math.isclose(supported_volume,_volume(stock),rel_tol=1e-7,abs_tol=1e-6):
            raise ParameterError("Thread interval contains missing stock or intersecting features", feature_id=feature_id)
        if p["internal"] and _volume(body & bore)>1e-6:
            raise ParameterError("Internal thread requires an existing cylindrical bore at the minor diameter", feature_id=feature_id)
        # Extend beyond both ends, then clip to the declared interval. The tiny
        # radial overlap avoids coincident cutter faces without changing the root.
        overlap = max(1e-4,depth*1e-3)
        flank = math.tan(math.radians(p["profile_angle"]/2))
        if p["internal"]:
            radius = major
            points = [(major,-pitch),(minor-overlap,-pitch-(depth+overlap)*flank),(minor-overlap,-pitch+(depth+overlap)*flank)]
        else:
            radius = minor
            points = [(minor,-pitch),(major+overlap,-pitch-(depth+overlap)*flank),(major+overlap,-pitch+(depth+overlap)*flank)]
        profile = Plane.XZ * make_face(Polyline(*points,close=True))
        path = Helix(pitch,length+2*pitch,radius,center=(0,0,-pitch),lefthand=p["left_hand"])
        # build123d's pipe defaults allow 0.0001 mm boundary error. Tighten
        # the same OCCT sweep builder so STEP reparameterization is stable.
        pipe = BRepOffsetAPI_MakePipeShell(Wire(path.edges()).wrapped)
        pipe.SetMode(True)
        pipe.SetTolerance(1e-9,1e-9,1e-7)
        pipe.SetMaxSegments(2000)
        pipe.Add(profile.faces()[0].outer_wire().wrapped,False,False)
        pipe.Build()
        if not pipe.IsDone() or not pipe.MakeSolid():
            raise GeometryKernelError("Precise helical thread sweep failed",feature_id=feature_id)
        cutter = Solid(pipe.Shape())
        clip = Cylinder(major+2*overlap,length,align=(Align.CENTER,Align.CENTER,Align.MIN))
        cutter = plane * (cutter & clip)
        result = body-cutter
        corrected_pcurves = []
        if p["internal"]:
            result = deepcopy(result)
            builder, fixer = BRep_Builder(), ShapeFix_Edge()
            for face in result.faces().filter_by(GeomType.BSPLINE):
                for edge in face.edges():
                    if BRep_Tool.IsClosed_s(edge.wrapped,face.wrapped):
                        continue
                    curve = BRepAdaptor_Curve(edge.wrapped)
                    pcurve = BRepAdaptor_Curve(edge.wrapped,face.wrapped).CurveOnSurface()
                    located_surface = GeomAdaptor_Surface(BRep_Tool.Surface_s(face.wrapped))
                    checker = GeomLib_CheckCurveOnSurface(curve,1e-9)
                    checker.Perform(Adaptor3d_CurveOnSurface(pcurve.GetCurve(),located_surface))
                    if checker.ErrorStatus() != 0:
                        raise GeometryKernelError("Thread boundary consistency measurement failed",feature_id=feature_id,
                                                  details={"occt_error_status":checker.ErrorStatus()})
                    deviation = float(checker.MaxDistance())
                    if not math.isfinite(deviation):
                        raise GeometryKernelError("Thread boundary consistency measurement failed",feature_id=feature_id)
                    if deviation <= 1e-6:
                        continue
                    # Boolean end intersections can retain a poorly fitted UV
                    # curve. Reproject its existing 3D edge without replacing
                    # the surface or changing the requested thread dimensions.
                    builder.UpdateEdge(edge.wrapped,None,face.wrapped,1e-9)
                    if not fixer.FixAddPCurve(edge.wrapped,face.wrapped,False,1e-9):
                        raise GeometryKernelError("Thread parameter boundary projection failed",feature_id=feature_id)
                    corrected_pcurves.append({"max_deviation_before":deviation,"projection_precision":1e-9})
        removed = _volume(body)-_volume(result)
        if removed<=1e-8 or len(result.solids()) != 1:
            raise BooleanOperationError("Thread cut must remove material and retain one connected solid", feature_id=feature_id)
        probes = []
        radial = minor+depth*(0.75 if p["internal"] else 0.25)
        for turn in range(int(length/pitch)):
            for offset,expected_material in ((0,False),(0.5,True)):
                z = (turn+0.25+offset)*pitch
                if z>=length-1e-6:
                    continue
                angle = (-1 if p["left_hand"] else 1)*2*math.pi*(turn+0.25)
                local = (radial*math.cos(angle),radial*math.sin(angle),z)
                world = tuple(plane.from_local_coords(local))
                actual = any(solid.is_inside(world,tolerance=1e-6) for solid in result.solids())
                probes.append({"point":list(world),"material":expected_material})
                if actual != expected_material:
                    raise InvalidTopologyError("Helical valley/crest material probe failed",feature_id=feature_id)
        return result, {"thread":{"single_start":True,"profile":"V","major_diameter":2*major,"minor_diameter":2*minor,
                        "pitch":pitch,"length":length,"internal":p["internal"],"left_hand":p["left_hand"],
                        "removed_volume":removed,"supported_stock_volume":supported_volume,"matching_walls":matching_walls,"probes":probes,
                        "corrected_pcurves":corrected_pcurves}}

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
        is_assembly = result.compiled.assembly is not None
        if not is_assembly:
            result.shape.color = Color(0.35, 0.55, 0.8)
            result.shape.label = "Agent-CAD part"
        extra_artifacts = []
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
            if is_assembly:
                from .assembly import restore_components
                roundtrip_result = restore_components(result, restored)
            else:
                roundtrip_result = BuildResult(restored,result.trace,roundtrip,result.compiled)
            roundtrip_validation=self.validate(roundtrip_result)
            if roundtrip_validation["status"] != "passed":
                raise ExportError("STEP roundtrip geometry could not be fully verified",details={"validation":roundtrip_validation})
            result.geometry["step_roundtrip"] = roundtrip
            result.geometry["step_roundtrip_checks"] = {"status":roundtrip_validation["status"],"count":len(roundtrip_validation["checks"])}
            if not export_stl(result.shape, output_dir/"model.stl", tolerance=0.08, angular_tolerance=0.15):
                raise ExportError("STL writer returned failure")
            if not export_gltf(result.shape, output_dir/"model.glb", binary=True, linear_deflection=0.08, angular_deflection=0.15):
                raise ExportError("GLB writer returned failure")
            if is_assembly:
                from .assembly import annotate_glb, export_bom
                result.geometry["glb_hierarchy"] = annotate_glb(result, output_dir/"model.glb")
                bom_paths = export_bom(result, output_dir)
                extra_artifacts = [artifact(bom_paths[0], "bom_json"), artifact(bom_paths[1], "bom_csv")]
        except CADError:
            raise
        except Exception as exc:
            raise ExportError(str(exc)) from exc
        return [artifact(path, path.suffix.lstrip(".")) for path in (output_dir/"model.step",output_dir/"model.stl",output_dir/"model.glb")] + extra_artifacts

    def validate(self, result: BuildResult) -> dict[str, Any]:
        if result.compiled.assembly is not None:
            from .assembly import validate_assembly
            return validate_assembly(result)
        from .validation import validate
        return validate(result)

    def properties(self, result: BuildResult) -> dict[str, Any]:
        properties = measure(result.shape)
        if result.compiled.assembly is not None:
            from .assembly import assembly_properties
            properties["assembly"] = assembly_properties(result)
        return properties

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
