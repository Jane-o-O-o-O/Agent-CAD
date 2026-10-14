"""Independent measurements of the executed B-Rep, never IR-only success claims."""
from __future__ import annotations

import math
from typing import Any
from build123d import Align, Cylinder, GeomType, Plane
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from .backend import BuildResult, _axis_points, _direction, _volume, surface_signature


def _check(name: str, expected: Any, actual: Any, passed: bool | None,
           tolerance: float | None = None, evidence: Any = None) -> dict[str, Any]:
    return {"name": name, "status": "unverified" if passed is None else "passed" if passed else "failed",
            "expected": expected, "actual": actual, "tolerance": tolerance, "evidence": evidence}


def cylindrical_surfaces(shape: Any) -> list[dict[str, Any]]:
    found = []
    for face in shape.faces().filter_by(GeomType.CYLINDER):
        adaptor = BRepAdaptor_Surface(face.wrapped)
        cylinder = adaptor.Cylinder()
        loc, axis = cylinder.Location(), cylinder.Axis().Direction()
        origin, direction = (loc.X(),loc.Y(),loc.Z()), (axis.X(),axis.Y(),axis.Z())
        point, normal = tuple(face.position_at(.25,.5)), tuple(face.normal_at(.25,.5))
        offset = [point[i]-origin[i] for i in range(3)]
        axial = sum(a*b for a,b in zip(offset,direction))
        radial = [offset[i]-axial*direction[i] for i in range(3)]
        inward = sum(a*b for a,b in zip(radial,normal)) < 0
        found.append({"radius": cylinder.Radius(), "origin": list(origin), "direction": list(direction),
                      "inward": inward, "angular_span": abs(adaptor.LastUParameter()-adaptor.FirstUParameter()), "axial_length": abs(adaptor.LastVParameter()-adaptor.FirstVParameter())})
    return found


def _holes(shape: Any, hole: dict[str, Any], tolerance: float, prefix: str) -> list[dict[str, Any]]:
    checks = []
    surfaces = cylindrical_surfaces(shape)
    direction = _direction(hole.get("direction", (0,0,1)))
    diameter = hole["diameter"]
    through = hole.get("through", True)
    lo, hi = _axis_points(shape, direction)
    for index, pos in enumerate(hole["positions"]):
        matched = []
        for surface in surfaces:
            parallel = abs(sum(a*b for a,b in zip(surface["direction"],direction)))
            delta = [surface["origin"][i]-pos[i] for i in range(3)]
            along = sum(a*b for a,b in zip(delta,direction))
            axis_distance = math.sqrt(sum((delta[i]-along*direction[i])**2 for i in range(3)))
            if surface["inward"] and surface["angular_span"] >= 2*math.pi-1e-5 and abs(parallel-1)<1e-6 and axis_distance<=tolerance and abs(surface["radius"]*2-diameter)<=tolerance:
                matched.append(surface)
        checks.append(_check(f"{prefix}.{index}.cylindrical_wall", {"diameter":diameter,"axis_position":pos},
                             matched, bool(matched), tolerance, "OCP BRepAdaptor_Surface; inward cylindrical face and measured axis/radius"))
        if not matched:
            continue
        projection = sum(a*b for a,b in zip(pos,direction))
        margin = max(0.1, hi-lo)
        if through:
            origin = tuple(pos[i]+direction[i]*(lo-projection-margin) for i in range(3))
            depth = hi-lo+2*margin
        else:
            origin = tuple(pos[i]+direction[i]*tolerance*2 for i in range(3))
            depth = max(hole["depth"]-4*tolerance, tolerance)
        probe = Plane(origin=origin,z_dir=direction)*Cylinder(diameter*0.45,depth,align=(Align.CENTER,Align.CENTER,Align.MIN))
        remaining_material = _volume(shape & probe)
        empty = remaining_material<=max(1e-7,tolerance**3)
        if through:
            checks.append(_check(f"{prefix}.{index}.through", True, empty, empty, tolerance,
                                 {"method":"exact B-Rep boolean intersection of bore-clearance cylinder across whole body bounds", "remaining_material_volume":remaining_material}))
        else:
            axial_lengths = [s["axial_length"] for s in matched]
            depth_ok = any(math.isclose(length,hole["depth"],abs_tol=tolerance) for length in axial_lengths)
            cap_point = tuple(pos[i]+direction[i]*(hole["depth"]+max(tolerance*10,1e-3)) for i in range(3))
            cap_present = any(solid.is_inside(cap_point,tolerance=tolerance) for solid in shape.solids())
            checks.append(_check(f"{prefix}.{index}.blind_depth", hole["depth"], {"lengths":axial_lengths,"cap_present":cap_present,"bore_clear":empty},
                                 depth_ok and cap_present and empty,tolerance,"Cylindrical face V-domain; bore boolean and solid point classification beyond bottom"))
    return checks


def validate(result: BuildResult) -> dict[str, Any]:
    shape, geom, compiled = result.shape, result.geometry, result.compiled
    expected = compiled.expected_geometry
    tolerance = expected.get("tolerance",1e-4)
    checks = [
        _check("brep_valid",True,bool(BRepCheck_Analyzer(shape.wrapped).IsValid()),bool(BRepCheck_Analyzer(shape.wrapped).IsValid()),evidence="OpenCascade BRepCheck_Analyzer"),
        _check("positive_volume","> 0",geom["volume"],math.isfinite(geom["volume"]) and geom["volume"]>1e-9,evidence="B-Rep mass properties"),
        _check("solid_count",expected.get("solids",1),geom["solids"],geom["solids"]==expected.get("solids",1),evidence="Kernel solid traversal"),
        _check("finite_bounds",True,geom["bbox"],all(math.isfinite(n) and abs(n)<=1e9 for numbers in geom["bbox"].values() for n in numbers),evidence="Kernel bounding box"),
    ]
    if expected.get("bbox") is not None:
        checks.append(_check("bounding_box",expected["bbox"],geom["bbox"]["size"],all(math.isclose(a,b,abs_tol=tolerance,rel_tol=0) for a,b in zip(expected["bbox"],geom["bbox"]["size"])),tolerance,"B-Rep bounding box"))
    for key in ("volume", "volume_min", "volume_max"):
        if expected.get(key) is not None:
            target=expected[key]
            ok = math.isclose(geom["volume"],target,rel_tol=1e-7,abs_tol=tolerance) if key=="volume" else geom["volume"]>=target-tolerance if key=="volume_min" else geom["volume"]<=target+tolerance
            checks.append(_check(key,target,geom["volume"],ok,tolerance,"B-Rep volume"))
    for index,hole in enumerate(expected.get("holes",[])):
        checks.extend(_holes(shape,hole,tolerance,f"expected_hole.{index}"))
    if expected.get("holes"):
        # A nonempty expected list is the complete final bore collection. Distinct
        # diameters on one axis are separate profiles (counterbores require explicit profiles).
        actual_bores=[]
        for bore in cylindrical_surfaces(shape):
            if not bore["inward"] or bore["angular_span"] < 2*math.pi-1e-5:
                continue
            direction=_direction(bore["direction"])
            duplicate=False
            for other in actual_bores:
                delta=[bore["origin"][i]-other["origin"][i] for i in range(3)]
                projected=sum(a*b for a,b in zip(delta,direction))
                distance=math.sqrt(sum((delta[i]-projected*direction[i])**2 for i in range(3)))
                if abs(abs(sum(a*b for a,b in zip(direction,other["direction"])))-1)<1e-6 and distance<=tolerance and abs(bore["radius"]-other["radius"])<=tolerance:
                    duplicate=True
                    break
            if not duplicate:
                actual_bores.append(bore)
        for bore in actual_bores:
            direction=_direction(bore["direction"])
            lo,hi=_axis_points(shape,direction)
            projection=sum(a*b for a,b in zip(bore["origin"],direction))
            origin=tuple(bore["origin"][i]+direction[i]*(lo-projection-1) for i in range(3))
            probe=Plane(origin=origin,z_dir=direction)*Cylinder(bore["radius"]*.9,hi-lo+2,align=(Align.CENTER,Align.CENTER,Align.MIN))
            bore["through"]=_volume(shape & probe)<=max(1e-7,tolerance**3)
        requested_modes={h.get("through",True) for h in expected["holes"]}
        relevant_bores=[bore for bore in actual_bores if bore["through"] in requested_modes]
        expected_count=sum(len(h["positions"]) for h in expected["holes"])
        checks.append(_check("complete_hole_profile_count",expected_count,len(relevant_bores),len(relevant_bores)==expected_count,tolerance,
                             {"method":"Unique inward cylindrical axes/radii; exact B-Rep bore clearance distinguishes through bores from blind pockets", "requested_modes":["through" if mode else "blind" for mode in sorted(requested_modes)]}))
    features=list(compiled.features)
    by_id={f.id:f for f in features}
    reachable=set()
    def visit(feature_id):
        if feature_id in reachable:
            return
        reachable.add(feature_id)
        for parent in by_id[feature_id].inputs:
            visit(parent)
    visit(compiled.output_feature)
    features=[f for f in features if f.id in reachable]
    def consumes(candidate_id, ancestor_id):
        return any(parent==ancestor_id or consumes(parent,ancestor_id) for parent in by_id[candidate_id].inputs)
    for i,feature in enumerate(features):
        if feature.type=="Hole":
            transformed=any(f.type in {"Transform","LinearPattern","CircularPattern"} and consumes(f.id,feature.id) for f in features[i+1:])
            if transformed and not expected.get("holes"):
                checks.append(_check(f"feature.{feature.id}.final_holes","final-frame hole verification",None,None,evidence="A downstream transform/pattern needs a complete expected_geometry.holes collection"))
            elif not transformed:
                checks.extend(_holes(shape,feature.params,tolerance,f"feature.{feature.id}"))
        for index,condition in enumerate(feature.validation_conditions):
            checks.append(_check(f"feature.{feature.id}.validation_condition.{index}",condition,None,None,evidence="Arbitrary feature validation conditions need a registered geometric verifier"))
    def matches_surface(wanted,actual):
        if wanted["type"]!=actual["type"]:
            return False
        for lo,hi,alo,ahi in zip(wanted["bbox_min"],wanted["bbox_max"],actual["bbox_min"],actual["bbox_max"]):
            if hi<alo-tolerance or ahi<lo-tolerance:
                return False
        if "radius" in wanted and not math.isclose(wanted["radius"],actual.get("radius",-1),abs_tol=tolerance,rel_tol=0):
            return False
        if "major_radius" in wanted and not math.isclose(wanted["major_radius"],actual.get("major_radius",-1),abs_tol=tolerance,rel_tol=0):
            return False
        for key in ("origin","axis","normal"):
            if key in wanted and math.dist(wanted[key],actual.get(key,[]))>tolerance:
                return False
        if "offset" in wanted and abs(wanted["offset"]-actual.get("offset",float("inf")))>tolerance:
            return False
        return True
    final_surfaces=[surface_signature(face) for face in shape.faces()]
    for entry in result.trace:
        if entry["feature_id"] not in reachable or entry["type"] not in {"Fillet","Chamfer"}:
            continue
        evidence=entry["evidence"]
        index=next(i for i,f in enumerate(features) if f.id==entry["feature_id"])
        transformed=any(f.type in {"Transform","LinearPattern","CircularPattern"} and consumes(f.id,entry["feature_id"]) for f in features[index+1:])
        signatures=evidence.get("finish_surfaces",[])
        dimensional=(len(evidence.get("measured_radii",[]))>=evidence["selected_edges"] if entry["type"]=="Fillet" else evidence.get("length_verified",False))
        retained=all(any(matches_surface(s,actual) for actual in final_surfaces) for s in signatures)
        verified=None if transformed or not dimensional else bool(signatures) and retained
        checks.append(_check(f"feature.{entry['feature_id']}.final_finish",True,
                             {"retained_surfaces":retained,"dimension_measured":dimensional,"evidence":evidence},verified,tolerance,
                             "New B-Rep cylinder/torus radius or planar chamfer-to-original-edge distances; final geometric surface signatures (no face indices). Post-transform finish matching is unverified."))
    for c in compiled.constraints:
        name=f"constraint.{c['id']}"
        target=c.get("expected")
        tol=c.get("tolerance",tolerance)
        final_target=c.get("feature_id") in (None,compiled.output_feature)
        if c["type"]=="dimension" and c.get("axis") and final_target:
            actual=geom["bbox"]["size"]["xyz".index(c["axis"])]
            checks.append(_check(name,target,actual,isinstance(target,(float,int)) and math.isclose(target,actual,abs_tol=tol,rel_tol=0),tol,"Final B-Rep bounding box"))
        elif c["type"] in {"volume","solid_count"} and final_target:
            actual=geom["volume" if c["type"]=="volume" else "solids"]
            checks.append(_check(name,target,actual,isinstance(target,(float,int)) and math.isclose(target,actual,abs_tol=tol,rel_tol=0),tol,"Final B-Rep mass/topology"))
        elif c["type"]=="hole" and isinstance(target,dict) and {"diameter","positions"}<=target.keys():
            checks.extend(_holes(shape,target,tol,name))
        else:
            checks.append(_check(name,target,None,None,tol,"This design-intent constraint does not have a geometric verifier yet"))
    status="failed" if any(c["status"]=="failed" for c in checks) else "unverified" if any(c["status"]=="unverified" for c in checks) else "passed"
    report={"status":status,"checks":checks,"tolerance_mm":tolerance,
            "scope":"B-Rep validity, measured dimensions, supported hole and feature checks; not a manufacturing certification"}
    result.validation=report
    return report
