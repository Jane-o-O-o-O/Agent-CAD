"""Measured prompt/edit/repair/version workflows, using real isolated workers.

Run separately from fixture geometry benchmarks and from paid-model benchmarks.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from app.application.services.cad_v2_service import CADV2Service
from .cases import corpus, repair_ir
from .run import environment, ratio

OWNER = "benchmark-owner"


async def run(output: Path) -> dict:
    service = CADV2Service(output / "data")
    records = []

    async def measure(name, kind, action, expected_status="completed"):
        start = time.perf_counter()
        job = await action
        await service.wait(job["id"])
        job = service.store.get_job(job["id"], OWNER)
        record = {"name": name, "kind": kind, "job_id": job["id"], "document_id": job["document_id"],
                  "expected_status": expected_status, "status": job["status"],
                  "expected_outcome": job["status"] == expected_status,
                  "latency_seconds": round(time.perf_counter()-start, 6),
                  "repair_count": len(job["repair_history"]), "repair_history": job["repair_history"],
                  "error": job.get("error"),
                  "stages": [entry[1]["stage"] for entry in service.store.events(job["id"], OWNER)]}
        revision = None
        if job["status"] == "completed":
            doc = service.store.get_document(job["document_id"], OWNER)
            revision = service.store.get_revision(doc["id"], doc["current_revision"], OWNER)
            record.update({"revision_id": revision["id"], "parent_revision": revision["parent_revision"],
                           "geometry": revision["report"]["geometry"], "validation": revision["report"]["validation"],
                           "artifacts": revision["artifacts"], "diff": revision["diff"],
                           "planning_mode": revision["ir"]["metadata"].get("planning_mode", "provided-ir")})
            record["expected_outcome"] &= revision["report"]["validation"]["status"] == "passed"
        records.append(record)
        print(json.dumps({"workflow": name, "status": job["status"], "seconds": record["latency_seconds"],
                          "repairs": record["repair_count"]}), flush=True)
        if job["status"] != expected_status:
            raise RuntimeError(f"{name}: expected {expected_status}; received {job['status']}: {job.get('error')}")
        return revision

    try:
        plate = next(c for c in corpus() if c.name == "06_four_hole_plate")
        first = await measure("A_explicit_chinese_prompt", "generation", service.create(OWNER, prompt=plate.prompt))
        second = await measure("B_natural_language_hole_edit", "edit", service.edit(
            OWNER, first["document_id"], expected_revision=first["id"], prompt="将四个孔的直径改成 8 mm，其他保持不变。"))
        assert second["ir"]["features"] == first["ir"]["features"]
        assert set(second["diff"]["parameters"]) == {"hole_diameter"}
        assert math.isclose(second["report"]["geometry"]["volume"], 32000-512*math.pi, abs_tol=1e-4)
        await measure("invalid_edit_rejected", "expected_rejection", service.edit(
            OWNER, first["document_id"], expected_revision=second["id"], parameters={"hole_diameter": -1}), "failed")
        assert service.store.get_document(first["document_id"], OWNER)["current_revision"] == second["id"]
        restored = await measure("restore_first_revision", "restore", service.restore(
            OWNER, first["document_id"], revision_id=first["id"], expected_revision=second["id"]))
        assert restored["ir"] == first["ir"]
        await measure("C_real_selection_failure_recovery", "repair", service.create(OWNER, ir=repair_ir()))
        bracket = next(c for c in corpus() if c.name == "22_l_bracket")
        await measure("D_two_face_L_bracket_from_IR", "generation", service.create(OWNER, ir=bracket.ir))
    except Exception as exc:
        records.append({"name": "runner_error", "expected_outcome": False, "error": str(exc)})
    finally:
        await service.shutdown()
    edits = [r for r in records if r.get("kind") == "edit"]
    repairs = [r for r in records if r.get("kind") == "repair"]
    return {"mode": "application_workflow_with_real_isolated_kernel", "recorded_at": datetime.now(timezone.utc).isoformat(),
            "environment": environment(), "summary": {
                "expected_outcomes": ratio(sum(r["expected_outcome"] for r in records), len(records)),
                "edit_success_rate": ratio(sum(r["status"] == "completed" for r in edits), len(edits)),
                "repair_recovery_rate": ratio(sum(r["status"] == "completed" for r in repairs), len(repairs)),
                "note": "Edit denominator is valid requested parameter edits; expected invalid-edit rejection and restore are separate. Repair denominator includes the explicitly injected first-attempt failure, not successful first attempts. Latency includes application queue, process startup, compilation, real kernel, validation, export and publication. No HTTP, browser, or paid model call is included."},
            "records": records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-copy", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(run(args.output))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    (args.output/"workflow-report.json").write_text(serialized)
    if args.report_copy:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(serialized)
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0 if all(r["expected_outcome"] for r in report["records"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
