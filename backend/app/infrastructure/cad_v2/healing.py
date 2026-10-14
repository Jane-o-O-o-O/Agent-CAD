"""Bounded OCCT topology recovery without changing the requested design.

These are candidate repairs, not a promise that arbitrary B-Reps can be fixed.
All algorithms run on a deep copy.  A candidate must retain the original face
geometry, absolute volume, bounds and (existing) solid count.  The caller must
still run the original IR constraints and the STEP round-trip validation.
Policies belong to the trusted execution request, never to model IR metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from build123d import Shape

from app.domain.cad_v2.errors import InvalidTopologyError, ParameterError


MAX_TOLERANCE_MM = 1e-5
STRATEGIES = frozenset({"orient_solids", "shape_fix", "sew_closed_shells"})


@dataclass
class HealingResult:
    shape: Shape
    evidence: dict[str, Any]


def validate_healing_options(options: dict | None) -> dict[str, dict[str, Any]]:
    """Validate the feature-to-policy map supplied by the application service."""
    if options is None:
        return {}
    if not isinstance(options, dict) or len(options) > 200:
        raise ParameterError("Healing options must be a bounded feature policy map")
    result = {}
    for feature_id, policy in options.items():
        if not isinstance(feature_id, str) or not feature_id or len(feature_id) > 200:
            raise ParameterError("Healing policy needs a valid feature ID")
        if not isinstance(policy, dict) or set(policy) != {"strategy", "tolerance_mm"}:
            raise ParameterError("Healing policy accepts only strategy and tolerance_mm")
        tolerance = policy["tolerance_mm"]
        if (not isinstance(policy["strategy"], str) or policy["strategy"] not in STRATEGIES
                or isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                or not math.isfinite(tolerance) or not 1e-7 <= tolerance <= MAX_TOLERANCE_MM):
            raise ParameterError("Healing strategy or tolerance is outside the trusted bounds")
        result[feature_id] = {"strategy": policy["strategy"], "tolerance_mm": float(tolerance)}
    return result


def _volume(shape: Shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    # Unlike Shape.volume this also measures a closed set of oriented faces
    # before sewing has restored its missing shell/solid containers.
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, properties)
    return float(properties.Mass())


def _metrics(shape: Shape) -> dict[str, Any]:
    from OCP.BRep import BRep_Tool
    bounds = shape.bounding_box()
    vertices, edges, faces = shape.vertices(), shape.edges(), shape.faces()
    tolerances = [BRep_Tool.Tolerance_s(item.wrapped) for item in [*vertices, *edges, *faces]]
    metrics = {
        "valid": bool(shape.is_valid), "volume": _volume(shape), "area": float(shape.area),
        "bbox": {"min": list(bounds.min), "max": list(bounds.max), "size": list(bounds.size)},
        "solids": len(shape.solids()), "shells": len(shape.shells()), "faces": len(faces),
        "edges": len(edges), "vertices": len(vertices),
        "max_topology_tolerance_mm": max(tolerances, default=0.0),
    }
    values = [metrics[key] for key in ("volume", "area", "max_topology_tolerance_mm")]
    values.extend(value for coordinates in metrics["bbox"].values() for value in coordinates)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Kernel returned non-finite geometric measurements")
    return metrics


def _face_geometry(shape: Shape) -> list[dict[str, Any]]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    signatures = []
    for face in shape.faces():
        adaptor = BRepAdaptor_Surface(face.wrapped)
        box = face.bounding_box()
        signature = {
            "type": face.geom_type.name, "area": float(face.area),
            "center": list(face.center()), "min": list(box.min), "max": list(box.max),
            # Retain inner loops as well as cylindrical surfaces: a repair may
            # not close a bore by deleting its wire or replacing its face.
            "wires": len(face.wires()),
        }
        if signature["type"] == "CYLINDER":
            signature["radius"] = adaptor.Cylinder().Radius()
        elif signature["type"] == "SPHERE":
            signature["radius"] = adaptor.Sphere().Radius()
        elif signature["type"] == "TORUS":
            signature["radius"] = adaptor.Torus().MinorRadius()
            signature["major_radius"] = adaptor.Torus().MajorRadius()
        elif signature["type"] == "CONE":
            signature["radius"] = adaptor.Cone().RefRadius()
            signature["angle"] = adaptor.Cone().SemiAngle()
        signatures.append(signature)
    return signatures


def _same_faces(before: list[dict], after: list[dict], tolerance: float) -> bool:
    if len(before) != len(after):
        return False
    remaining = list(after)
    for old in before:
        match = None
        for index, new in enumerate(remaining):
            if old.keys() != new.keys() or old["type"] != new["type"] or old["wires"] != new["wires"]:
                continue
            if not math.isclose(old["area"], new["area"], rel_tol=1e-9, abs_tol=tolerance**2):
                continue
            if any(not math.isclose(a, b, rel_tol=0, abs_tol=tolerance)
                   for key in ("center", "min", "max") for a, b in zip(old[key], new[key])):
                continue
            if any(not math.isclose(old[key], new[key], rel_tol=1e-10, abs_tol=1e-7)
                   for key in ("radius", "major_radius", "angle") if key in old):
                continue
            match = index
            break
        if match is None:
            return False
        remaining.pop(match)
    return True


def _orient(shape: Shape) -> Shape:
    from build123d import Compound
    from OCP.BRepLib import BRepLib
    solids = list(shape.solids())
    if not solids:
        raise ValueError("Orientation repair requires an existing solid")
    for solid in solids:
        if not BRepLib.OrientClosedSolid_s(solid.wrapped):
            raise ValueError("Solid is open or cannot be oriented coherently")
    # OrientClosedSolid changes shell orientation in the extracted solid. Build
    # the containers anew because a reversed compound otherwise reverses it again.
    if len(shape.faces()) != sum(len(solid.faces()) for solid in solids):
        raise ValueError("Orientation repair cannot discard faces outside solids")
    return solids[0] if len(solids) == 1 else Compound(children=solids)


def _fix(shape: Shape, tolerance: float) -> Shape:
    from build123d import Compound
    from OCP.ShapeFix import ShapeFix_Shape
    fixer = ShapeFix_Shape(shape.wrapped)
    fixer.SetPrecision(tolerance)
    fixer.SetMinTolerance(1e-7)
    fixer.SetMaxTolerance(tolerance)
    # Correct tolerance/parameter/orientation defects. No hole filling, feature
    # suppression, dimensional edits or same-domain simplification is requested.
    fixer.FixVertexPositionMode = False
    fixer.FixSolidTool().CreateOpenSolidMode = False
    fixer.Perform()
    return Compound.cast(fixer.Shape())


def _sew(shape: Shape, tolerance: float) -> tuple[Shape, dict]:
    from build123d import Compound
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.BRepCheck import BRepCheck_NoError, BRepCheck_Shell
    from OCP.BRepLib import BRepLib
    sewing = BRepBuilderAPI_Sewing(tolerance)
    sewing.SetMinTolerance(1e-7)
    sewing.SetMaxTolerance(tolerance)
    sewing.SetNonManifoldMode(False)
    for face in shape.faces():
        sewing.Add(face.wrapped)
    sewing.Perform()
    diagnostics = {"free_edges": sewing.NbFreeEdges(), "multiple_edges": sewing.NbMultipleEdges(),
                   "deleted_faces": sewing.NbDeletedFaces()}
    if any(diagnostics.values()):
        raise ValueError(f"Sewing left an open/non-manifold boundary or deleted faces: {diagnostics}")
    sewed = Compound.cast(sewing.SewedShape())
    shells = list(sewed.shells())
    if not shells or len(sewed.faces()) != sum(len(shell.faces()) for shell in shells):
        raise ValueError("Sewing did not account for every face in closed shells")
    solids = []
    for shell in shells:
        if BRepCheck_Shell(shell.wrapped).Closed() != BRepCheck_NoError:
            raise ValueError("Sewing produced an open shell; filling missing faces is forbidden")
        maker = BRepBuilderAPI_MakeSolid(shell.wrapped)
        solid = maker.Solid()
        if not BRepLib.OrientClosedSolid_s(solid):
            raise ValueError("Sewn shell does not enclose an orientable solid")
        solids.append(Compound.cast(solid))
    return (solids[0] if len(solids) == 1 else Compound(children=solids)), diagnostics


def heal_shape(shape: Shape, *, strategy: str, tolerance_mm: float,
               feature_id: str | None = None, require_solid: bool = True,
               expected_solids: int | None = None) -> HealingResult:
    """Try one bounded recovery and reject every unverified candidate.

    ``expected_solids`` must come from compiled original design constraints, if
    supplied. Existing solids can never silently merge/disappear. Sewing can
    restore absent solid containers only from closed shells containing all the
    original faces, while preserving their integrated volume and geometry.
    """
    from build123d import Compound
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    policy = validate_healing_options({feature_id or "shape": {
        "strategy": strategy, "tolerance_mm": tolerance_mm}})[feature_id or "shape"]
    tolerance_mm = policy["tolerance_mm"]
    started = time.perf_counter()
    evidence: dict[str, Any] = {"strategy": strategy, "tolerance_mm": tolerance_mm,
                              "feature_id": feature_id, "status": "rejected"}
    try:
        before = _metrics(shape)
        evidence["before"] = before
        original_faces = _face_geometry(shape)
        candidate = Compound.cast(BRepBuilderAPI_Copy(shape.wrapped, True, False).Shape())
        if strategy == "orient_solids":
            candidate = _orient(candidate)
        elif strategy == "shape_fix":
            candidate = _fix(candidate, tolerance_mm)
        else:
            candidate, evidence["sewing"] = _sew(candidate, tolerance_mm)
        after = _metrics(candidate)
        evidence["after"] = after
        geometry_tolerance = max(2e-7, tolerance_mm)
        checks = {
            "valid_topology": after["valid"],
            "finite_metrics": all(math.isfinite(after[key]) for key in ("volume", "area", "max_topology_tolerance_mm")),
            "positive_solid_volume": not require_solid or (after["solids"] > 0 and after["volume"] > 1e-9
                and all(_volume(solid) > 1e-9 for solid in candidate.solids())),
            "absolute_volume_preserved": math.isclose(abs(before["volume"]), abs(after["volume"]), rel_tol=1e-8, abs_tol=1e-7),
            "bounds_preserved": all(math.isclose(a, b, rel_tol=0, abs_tol=geometry_tolerance)
                for key in ("min", "max", "size") for a, b in zip(before["bbox"][key], after["bbox"][key])),
            "area_preserved": math.isclose(before["area"], after["area"], rel_tol=1e-9, abs_tol=1e-7),
            "solid_count_preserved": after["solids"] == before["solids"] if before["solids"] else
                (strategy == "sew_closed_shells" or after["solids"] == 0),
            "expected_solid_count": expected_solids is None or after["solids"] == expected_solids,
            "face_geometry_preserved": _same_faces(original_faces, _face_geometry(candidate), geometry_tolerance),
            "bounded_topology_tolerance": after["max_topology_tolerance_mm"] <= max(tolerance_mm, before["max_topology_tolerance_mm"]) + 1e-12,
        }
        evidence["checks"] = checks
        if not all(checks.values()):
            raise ValueError("Healing candidate violated invariants: " + ", ".join(key for key, passed in checks.items() if not passed))
        evidence["status"] = "accepted"
        evidence["duration_seconds"] = round(time.perf_counter() - started, 6)
        evidence["original_constraints_required"] = True
        return HealingResult(candidate, evidence)
    except Exception as exc:
        evidence["duration_seconds"] = round(time.perf_counter() - started, 6)
        evidence["reason"] = str(exc)
        raise InvalidTopologyError("Bounded topology repair did not produce an invariant-preserving shape",
            feature_id=feature_id, recoverable=True,
            details={"healing_candidate": True, "healing": evidence}) from exc
