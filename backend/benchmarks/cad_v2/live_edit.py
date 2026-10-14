"""Opt-in live structured-model editing through CADV2Service and the real CAD worker.

Configure CAD_V2_API_KEY (or API_KEY), CAD_V2_API_BASE and CAD_V2_EDITOR_MODEL
(or CAD_V2_MODEL) in the environment, then explicitly invoke:

    python -m benchmarks.cad_v2.live_edit --output /tmp/cad-v2-live-edit

Default isolation is strict. Development containers without namespace support may
explicitly select --isolation process-dev; the evidence records the actual mode.
This entry point is never invoked by ordinary tests. No credentials are accepted
as CLI arguments or intentionally included in the report, IR, or artifact copies.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import time
from typing import Any

from app.application.services.cad_v2_service import CADV2Service
from app.infrastructure.cad_v2.executor import CADExecutor
from app.infrastructure.cad_v2.model_gateway import ModelConfig
from .live import scrub

OWNER = "opt-in-live-edit-benchmark"
PROMPT = (
    "Move the existing bore to x=20 mm, y=10 mm, keeping its diameter 6 mm and its through depth. "
    "Also add a rectangular through slot, 10 mm along X and 8 mm along Y, with its minimum "
    "corner at x=35 mm, y=20 mm, cutting the full plate thickness from z=0 to z=8 mm. "
    "Preserve the plate feature, its 80 by 50 by 8 mm dimensions, the existing height "
    "constraint, and all other geometry and requirements."
)


def baseline_ir() -> dict[str, Any]:
    bore = {"diameter": 6, "positions": [[10, 10, 0]], "through": True}
    return {
        "document_id": "live-edit-baseline", "units": "mm",
        "features": [
            {"id": "plate", "type": "Box", "params": {"width": 80, "depth": 50, "height": 8}},
            {"id": "bore", "type": "Hole", "inputs": ["plate"], "params": bore},
        ], "output_feature": "bore",
        "expected_geometry": {"bbox": [80, 50, 8], "solids": 1,
                              "volume": "80*50*8-pi*3**2*8", "holes": [{"feature_id": "bore", **bore}]},
        "constraints": [{"id": "height", "type": "dimension", "axis": "z", "expected": 8}],
        "metadata": {"planning_mode": "provided-ir", "purpose": "Fixed independent live-edit baseline"},
    }


def public_evidence(value: Any, api_key: str) -> Any:
    """Remove known secret field names/patterns and the exact configured credential."""
    def redact(item):
        if isinstance(item, str):
            return item.replace(api_key, "[REDACTED]") if api_key else item
        if isinstance(item, dict):
            return {key: redact(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [redact(nested) for nested in item]
        return item
    return scrub(redact(value))


def independent_checks(service: CADV2Service, before: dict, after: dict, requested_model: str) -> list[dict]:
    """Inspect the published STEP against constants, never model-supplied expectations."""
    from build123d import Align, Cylinder, Pos, Vector, import_step
    from app.infrastructure.cad_v2.validation import cylindrical_surfaces

    checks: list[dict] = []
    def check(name, expected, actual, passed, evidence):
        checks.append({"name": name, "expected": expected, "actual": actual,
                       "status": "passed" if passed else "failed", "evidence": evidence})
    required = {"step", "stl", "glb", "ir", "python"}
    check("published_formats", sorted(required), sorted(after["artifacts"]), required.issubset(after["artifacts"]),
          "All core exports belong to the same published revision")
    consistent = True
    for kind, manifest in after["artifacts"].items():
        path, _ = service.store.artifact_location(after["document_id"], after["id"], OWNER, kind)
        consistent &= hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
        consistent &= after["id"] in path.parts and path.stat().st_size == manifest["size"]
        if kind == "ir":
            consistent &= json.loads(path.read_text()) == after["ir"]
        if kind == "glb":
            consistent &= path.read_bytes()[:4] == b"glTF"
    check("artifact_consistency", True, consistent, consistent, "Actual file hashes, revision paths, IR equality and GLB header")

    step, _ = service.store.artifact_location(after["document_id"], after["id"], OWNER, "step")
    shape = import_step(step)
    check("valid_single_solid", {"valid": True, "solids": 1},
          {"valid": bool(shape.is_valid), "solids": len(shape.solids())},
          bool(shape.is_valid) and len(shape.solids()) == 1, "Independent reimport of exported exact STEP B-Rep")
    bounds = shape.bounding_box()
    expected_bounds = [0, 0, 0, 80, 50, 8]
    actual_bounds = [*bounds.min, *bounds.size]
    check("unchanged_plate_envelope", expected_bounds, actual_bounds,
          all(math.isclose(actual, expected, abs_tol=1e-4, rel_tol=0) for actual, expected in zip(actual_bounds, expected_bounds)),
          "Absolute minimum corner and size measured from reimported STEP")
    volume = 80 * 50 * 8 - math.pi * 3**2 * 8 - 10 * 8 * 8
    check("analytic_volume", volume, float(shape.volume), math.isclose(float(shape.volume), volume, abs_tol=1e-4, rel_tol=0),
          "Fixed external specification: plate minus 6 mm cylindrical bore minus 10 by 8 through slot")
    inward = [surface for surface in cylindrical_surfaces(shape) if surface["inward"]]
    matches = lambda surface: (abs(surface["radius"] - 3) < 1e-4
                               and abs(surface["origin"][0] - 20) < 1e-4
                               and abs(surface["origin"][1] - 10) < 1e-4
                               and abs(abs(surface["direction"][2]) - 1) < 1e-4)
    check("moved_bore_profile", {"diameter": 6, "xy": [20, 10], "axial_length": 8}, inward,
          bool(inward) and all(matches(surface) for surface in inward)
          and math.isclose(sum(surface["axial_length"] for surface in inward), 8, abs_tol=1e-4, rel_tol=0),
          "OCP cylindrical surface radii, axis positions and face spans from STEP, independent of edited IR")

    def remaining_column_volume(x, y, radius):
        probe = Pos(x, y, -1) * Cylinder(radius, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
        intersection = shape & probe
        return 0.0 if intersection is None else sum(float(solid.volume) for solid in intersection.solids())
    bore_material = remaining_column_volume(20, 10, 2.99)
    check("bore_is_through", 0, bore_material, bore_material < 1e-7,
          "Exact B-Rep intersection with a cylinder extending below and above the plate")
    slot_points = [[36, 21, 4], [44, 27, 4]]
    occupied = [any(solid.is_inside(Vector(*point), tolerance=1e-7) for solid in shape.solids()) for point in slot_points]
    slot_columns = [remaining_column_volume(point[0], point[1], 0.1) for point in slot_points]
    check("two_slot_points_are_through_voids", {"occupied": [False, False], "column_volume": [0, 0]},
          {"points": slot_points, "occupied": occupied, "column_volume": slot_columns},
          not any(occupied) and all(value < 1e-7 for value in slot_columns),
          "Two interior slot points and two full-thickness clearance columns in the reimported B-Rep")
    retained_points = [[10, 10, 4], [34, 24, 4], [46, 24, 4], [40, 19, 4], [40, 29, 4]]
    retained = [any(solid.is_inside(Vector(*point), tolerance=1e-7) for solid in shape.solids()) for point in retained_points]
    check("unrequested_material_retained", [True] * len(retained_points), {"points": retained_points, "occupied": retained},
          all(retained), "Old bore center is filled; material immediately outside all four slot sides remains")
    old_features = {feature["id"]: feature for feature in before["ir"]["features"]}
    new_features = {feature["id"]: feature for feature in after["ir"]["features"]}
    check("plate_feature_unchanged", old_features["plate"], new_features.get("plate"),
          old_features["plate"] == new_features.get("plate"), "Exact persisted feature equality including ID, dimensions and editability")
    check("constraints_preserved", before["ir"]["constraints"], after["ir"]["constraints"],
          before["ir"]["constraints"] == after["ir"]["constraints"], "Original constraint IDs, values and tolerances remain byte-equivalent")
    metadata = after["ir"]["metadata"].get("last_edit", {})
    check("real_structured_model_path", {"planning_mode": "structured-incremental-edit", "model": requested_model},
          {"planning_mode": metadata.get("planning_mode"), "model": metadata.get("model")},
          metadata.get("planning_mode") == "structured-incremental-edit" and metadata.get("model") == requested_model and bool(requested_model),
          "Persisted service edit-planner metadata; deterministic/parser paths are rejected")
    check("stable_identity_and_parent", {"document_id": before["document_id"], "parent_revision": before["id"]},
          {"document_id": after["document_id"], "parent_revision": after["parent_revision"]},
          after["document_id"] == before["document_id"] and after["parent_revision"] == before["id"]
          and new_features.get("bore", {}).get("type") == "Hole", "Incremental revision lineage and existing bore ID retained")
    return checks


def save_revision(service: CADV2Service, revision: dict, output: Path, name: str, api_key: str) -> dict:
    destination = output / name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "ir.json").write_text(json.dumps(public_evidence(revision["ir"], api_key), ensure_ascii=False, indent=2, allow_nan=False))
    exported = {}
    for kind, manifest in revision["artifacts"].items():
        source, _ = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, kind)
        target = destination / source.name
        # Kernel artifacts are generated from bounded IR; no environment or
        # provider response is embedded in them. Redact text exports defensively.
        if kind in {"ir", "python", "svg", "bom_json", "bom_csv"}:
            text = source.read_text()
            target.write_text(text.replace(api_key, "[REDACTED]") if api_key else text)
        else:
            shutil.copyfile(source, target)
        exported[kind] = {"file": str(target.relative_to(output)), "size": target.stat().st_size,
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    return exported


async def run(output: Path, isolation: str, config: ModelConfig) -> dict:
    service = CADV2Service(output / "data", executor=CADExecutor(isolation_mode=isolation), max_concurrency=1)
    started = time.perf_counter()
    result = {"scope": "One opt-in real structured edit model call, service revision workflow, real CAD worker and independent STEP checks",
              "recorded_at": datetime.now(timezone.utc).isoformat(), "status": "failed", "prompt": PROMPT,
              "requested_model": config.model, "requested_isolation": isolation,
              "limitations": "One fixed edit case, not a general success-rate benchmark; configured provider retries can make multiple attempts."}
    try:
        baseline_job = await service.create(OWNER, ir=baseline_ir(), name="Live incremental edit acceptance")
        await service.wait(baseline_job["id"])
        baseline_job = service.store.get_job(baseline_job["id"], OWNER)
        result["baseline_job"] = baseline_job
        if baseline_job["status"] != "completed":
            result["error"] = baseline_job.get("error")
            result["elapsed_seconds"] = round(time.perf_counter() - started, 6)
            return public_evidence(result, config.api_key)
        before = service.store.get_revision(baseline_job["document_id"], baseline_job["revision_id"], OWNER)
        result["baseline_artifacts"] = save_revision(service, before, output, "baseline", config.api_key)
        edit_job = await service.edit(OWNER, before["document_id"], expected_revision=before["id"], prompt=PROMPT)
        await service.wait(edit_job["id"])
        edit_job = service.store.get_job(edit_job["id"], OWNER)
        result["edit_job"] = edit_job
        result["stages"] = [event[1]["stage"] for event in service.store.events(edit_job["id"], OWNER)]
        result["current_revision"] = service.store.get_document(before["document_id"], OWNER)["current_revision"]
        if edit_job["status"] != "completed":
            result["error"] = edit_job.get("error")
            result["last_valid_revision_preserved"] = result["current_revision"] == before["id"]
        else:
            after = service.store.get_revision(before["document_id"], edit_job["revision_id"], OWNER)
            metadata = after["ir"]["metadata"].get("last_edit", {})
            result.update({"baseline_revision_id": before["id"], "revision_id": after["id"],
                           "ir": after["ir"], "report": after["report"], "diff": after["diff"],
                           "planning_mode": metadata.get("planning_mode"), "model": metadata.get("model"),
                           "usage": metadata.get("usage"), "artifacts": save_revision(service, after, output, "edited", config.api_key)})
            checks = independent_checks(service, before, after, config.model)
            result["independent_checks"] = checks
            if all(check["status"] == "passed" for check in checks):
                result["status"] = "completed"
            else:
                result["error"] = {"code": "IndependentAcceptanceFailure", "message": "Published edit failed the fixed external STEP or preservation checks"}
    except Exception as exc:
        result["error"] = exc.to_dict() if hasattr(exc, "to_dict") else {"code": type(exc).__name__, "message": str(exc)}
    finally:
        await service.shutdown()
        result["elapsed_seconds"] = round(time.perf_counter() - started, 6)
    return public_evidence(result, config.api_key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory containing report.json, IR and artifact copies, and durable service data")
    parser.add_argument("--isolation", choices=("strict", "process-dev"), default="strict", help="Explicit worker isolation selection; defaults to fail-closed strict mode")
    args = parser.parse_args()
    config = ModelConfig.from_env("editor")
    try:
        config.validate()
    except Exception:
        # Never format the configuration object or a credential-bearing exception.
        parser.error("Live edit requires valid editor model configuration and an API key in the environment")
    args.output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args.output, args.isolation, config))
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({key: report.get(key) for key in ("status", "model", "planning_mode", "usage", "requested_isolation", "elapsed_seconds", "error")}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
