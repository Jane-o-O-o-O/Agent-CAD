"""Opt-in paid-provider acceptance through the real CAD V2 application service.

This module never runs in ordinary CI. Configure CAD_V2_API_BASE,
CAD_V2_PLANNER_MODEL (or CAD_V2_MODEL) and CAD_V2_API_KEY in the environment,
then explicitly run: python -m benchmarks.cad_v2.live --output /tmp/cad-v2-live
The key is never accepted as a command-line argument or written into evidence.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any

from app.application.services.cad_v2_service import CADV2Service
from app.infrastructure.cad_v2.model_gateway import ModelConfig

PROMPT = (
    "生成真实三维零件，单位mm：底板长80、宽50、厚8，底板最小角为(0,0,0)。"
    "在底板中央(40,25)添加半径10、高12的圆柱凸台，底面位于z=8，顶面z=20；"
    "凸台与底板融合为一个实体。沿(40,25)的Z轴开直径6的通孔，贯穿底板和凸台。"
    "最终外形尺寸80×50×20。所有上述尺寸设为可编辑参数，并验证孔径、孔位和通孔。"
)
OWNER = "opt-in-live-benchmark"


def scrub(value: Any) -> Any:
    """Keep reproducible evidence without credentials or machine-local paths."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in {
            "api_key", "apikey", "authorization", "access_token", "refresh_token", "password", "secret"
        } else scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
        if value.startswith(("/workspace/", "/tmp/", "/home/", "/root/")):
            return "<local>/" + Path(value).name
    return value


def independent_checks(service: CADV2Service, revision: dict) -> list[dict]:
    """Measure exported STEP against fixed analytic intent, independent of model IR."""
    from build123d import Align, Cylinder, Pos, import_step
    from app.infrastructure.cad_v2.validation import cylindrical_surfaces

    checks = []

    def check(name: str, expected: Any, actual: Any, passed: bool, evidence: str) -> None:
        checks.append({"name": name, "expected": expected, "actual": actual,
                       "status": "passed" if passed else "failed", "evidence": evidence})

    artifacts = revision["artifacts"]
    required = {"step", "stl", "glb", "ir", "python"}
    check("core_revision_formats", sorted(required), sorted(artifacts), required.issubset(artifacts),
          "Published revision manifest; engineering and preview artifacts share one revision")
    hashes_ok = True
    for kind, manifest in artifacts.items():
        path, _ = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, kind)
        hashes_ok &= hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
        hashes_ok &= revision["id"] in path.parts
        if kind == "glb":
            hashes_ok &= path.read_bytes()[:4] == b"glTF"
        if kind == "ir":
            hashes_ok &= json.loads(path.read_text()) == revision["ir"]
    check("artifact_consistency", True, hashes_ok, hashes_ok, "Byte hashes, GLB header, persisted IR equality and revision-specific paths")
    path, _ = service.store.artifact_location(revision["document_id"], revision["id"], OWNER, "step")
    shape = import_step(path)
    check("reimport_valid_solid", {"valid": True, "solids": 1},
          {"valid": bool(shape.is_valid), "solids": len(shape.solids())},
          bool(shape.is_valid) and len(shape.solids()) == 1, "Actual STEP import and B-Rep topology")
    bounds = shape.bounding_box()
    check("absolute_bounds", {"min": [0, 0, 0], "size": [80, 50, 20]},
          {"min": list(bounds.min), "size": list(bounds.size)},
          all(math.isclose(a, b, abs_tol=1e-4, rel_tol=0)
              for a, b in zip([*bounds.min, *bounds.size], [0, 0, 0, 80, 50, 20])),
          "Reimported exact B-Rep bounding box, not expected_geometry supplied by the model")
    expected_volume = 80*50*8 + math.pi*10**2*12 - math.pi*3**2*20
    check("analytic_volume", expected_volume, float(shape.volume),
          math.isclose(float(shape.volume), expected_volume, abs_tol=1e-4, rel_tol=0),
          "Plate + cylindrical boss − continuous cylindrical bore; fixed analytic specification")
    surfaces = cylindrical_surfaces(shape)
    inward = [s for s in surfaces if s["inward"]]

    def matches(surface: dict, radius: float) -> bool:
        return (abs(surface["radius"]-radius) < 1e-4 and
                abs(surface["origin"][0]-40) < 1e-4 and
                abs(surface["origin"][1]-25) < 1e-4 and
                abs(abs(surface["direction"][2])-1) < 1e-4)

    bore_length = sum(s["axial_length"] for s in inward if matches(s, 3))
    check("bore_profile", {"radius": 3, "axis_xy": [40, 25], "axial_length": 20}, inward,
          bool(inward) and all(matches(s, 3) for s in inward) and abs(bore_length-20) < 1e-4,
          "OCP cylindrical surface radius/axis and summed axial face spans in reimported STEP")
    probe = Pos(40, 25, -1) * Cylinder(2.99, 22, align=(Align.CENTER, Align.CENTER, Align.MIN))
    intersection = shape & probe
    remaining = 0.0 if intersection is None else sum(float(solid.volume) for solid in intersection.solids())
    check("bore_is_through", 0, remaining, remaining < 1e-7,
          "Exact B-Rep intersection with a clearance cylinder spanning below plate through above boss")
    boss_faces = [s for s in surfaces if not s["inward"] and matches(s, 10)]
    boss_length = sum(s["axial_length"] for s in boss_faces)
    check("boss_profile", {"radius": 10, "axis_xy": [40, 25], "axial_length": 12}, boss_faces,
          bool(boss_faces) and abs(boss_length-12) < 1e-4,
          "Outward cylindrical B-Rep surfaces in actual STEP")
    return checks


async def run(output: Path) -> dict:
    config = ModelConfig.from_env("planner")
    config.validate()  # Credentials are consumed only through environment configuration.
    service = CADV2Service(output / "data")
    started = time.perf_counter()
    result: dict[str, Any] = {
        "scope": "One opt-in live model prompt through CADV2Service, isolated kernel, revision publication and independent STEP acceptance; excludes HTTP/browser",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "prompt": PROMPT,
        "requested_model": config.model, "status": "failed", "usage": None,
        "limitations": "One fixed mechanical design, not a statistically meaningful natural-language benchmark. Configured bounded provider retries may make multiple API attempts.",
    }
    try:
        job = await service.create(OWNER, prompt=PROMPT, name="Live plate and boss acceptance")
        await service.wait(job["id"])
        job = service.store.get_job(job["id"], OWNER)
        result["job"] = job
        result["stages"] = [entry[1]["stage"] for entry in service.store.events(job["id"], OWNER)]
        if job["status"] == "completed":
            document = service.store.get_document(job["document_id"], OWNER)
            revision = service.store.get_revision(document["id"], document["current_revision"], OWNER)
            metadata = revision["ir"]["metadata"]
            result.update({"ir": revision["ir"], "revision_id": revision["id"],
                           "report": revision["report"], "artifacts": revision["artifacts"],
                           "planning_mode": metadata.get("planning_mode"), "model": metadata.get("model"),
                           "usage": metadata.get("usage"), "model_attempts": metadata.get("model_attempts"),
                           "model_elapsed_seconds": metadata.get("model_elapsed_seconds")})
            checks = independent_checks(service, revision)
            checks.append({"name": "real_structured_model_path", "expected": "structured-model",
                           "actual": metadata.get("planning_mode"),
                           "status": "passed" if metadata.get("planning_mode") == "structured-model" else "failed",
                           "evidence": "Published IR model-call metadata; fixture or deterministic-parser paths are rejected"})
            result["independent_checks"] = checks
            if all(check["status"] == "passed" for check in checks):
                result["status"] = "completed"
            else:
                result["error"] = {"code": "IndependentAcceptanceFailure", "message": "Published geometry did not match the fixed external acceptance requirements"}
        else:
            result["error"] = job.get("error")
    except Exception as exc:
        result["error"] = exc.to_dict() if hasattr(exc, "to_dict") else {"code": type(exc).__name__, "message": str(exc)}
    finally:
        await service.shutdown()
    result["elapsed_seconds"] = round(time.perf_counter()-started, 6)
    return scrub(result)


async def recheck(output: Path) -> dict:
    """Recheck the already-published revision; never call a model or rebuild it."""
    previous = json.loads((output / "report.json").read_text())
    service = CADV2Service(output / "data")
    try:
        job = service.store.get_job(previous["job"]["id"], OWNER)
        if job["status"] != "completed":
            raise ValueError("Only a completed, published job can be rechecked")
        revision = service.store.get_revision(job["document_id"], previous["revision_id"], OWNER)
        checks = independent_checks(service, revision)
        mode = revision["ir"]["metadata"].get("planning_mode")
        checks.append({"name": "real_structured_model_path", "expected": "structured-model",
                       "actual": mode, "status": "passed" if mode == "structured-model" else "failed",
                       "evidence": "Persisted revision metadata from the original live call"})
        return scrub({"scope": "Independent acceptance of the unchanged published STEP; no additional model call or geometry rebuild",
                      "recorded_at": datetime.now(timezone.utc).isoformat(),
                      "source_job_id": job["id"], "revision_id": revision["id"],
                      "original_runner_status": previous["status"], "original_runner_error": previous.get("error"),
                      "model_calls_in_this_run": 0, "artifacts": revision["artifacts"],
                      "independent_checks": checks,
                      "status": "completed" if all(c["status"] == "passed" for c in checks) else "failed"})
    finally:
        await service.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Isolated output directory for data and report.json")
    parser.add_argument("--report-copy", type=Path, help="Optional credential-free evidence JSON destination")
    parser.add_argument("--verify-existing", action="store_true", help="Recheck report.json's published revision without calling the model; writes recheck.json")
    args = parser.parse_args()
    try:
        if not args.verify_existing:
            ModelConfig.from_env("planner").validate()
    except Exception as exc:
        # The configuration error is generic; never show the config object or key.
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(recheck(args.output) if args.verify_existing else run(args.output))
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    (args.output / ("recheck.json" if args.verify_existing else "report.json")).write_text(serialized)
    if args.report_copy:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(serialized)
    print(json.dumps({key: report.get(key) for key in (
        "status", "model", "planning_mode", "usage", "model_attempts", "model_elapsed_seconds", "elapsed_seconds", "error"
    )}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
