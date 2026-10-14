"""Run with `python -m benchmarks.cad_v2.run --output /tmp/cad-v2-benchmark`.

This is an IR-to-real-kernel benchmark, not an NLP or paid-model benchmark.
Corpus constraints are saved before execution and hashed for reviewability.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import statistics
import time
import traceback
from typing import Any

from .cases import CORPUS_VERSION, BenchmarkCase, corpus, corpus_hash


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def environment() -> dict[str, str]:
    # build123d 0.11 can install the lighter no-VTK OCP distribution.
    packages = ("cadquery-ocp", "cadquery-ocp-novtk")
    ocp = "unknown"
    for name in packages:
        try:
            ocp = f"{name} {importlib.metadata.version(name)}"
            break
        except importlib.metadata.PackageNotFoundError:
            continue
    return {"python": platform.python_version(), "platform": platform.platform(),
            "build123d": importlib.metadata.version("build123d"), "ocp": ocp}


def run_case(case: BenchmarkCase, output: Path) -> dict[str, Any]:
    from app.domain.cad_v2.compiler import compile_ir
    from app.infrastructure.cad_v2.backend import Build123dBackend
    from app.infrastructure.cad_v2.validation import validate

    start = time.perf_counter()
    row: dict[str, Any] = {"name": case.name, "category": case.category, "prompt": case.prompt,
                           "execution_success": False, "geometry_valid": False,
                           "constraints_satisfied": False, "export_success": False,
                           "repair_count": 0, "repair_attempted": False, "failure_category": None}
    directory = output / case.name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "design.ir.json").write_text(json.dumps(case.ir, ensure_ascii=False, indent=2))
    try:
        compiled = compile_ir(case.ir)
        engine = Build123dBackend()
        result = engine.build(compiled)
        row["execution_success"] = True
        row["geometry_valid"] = bool(result.geometry["valid"] and result.geometry["volume"] > 0)
        validation = validate(result)
        if case.bbox_min is not None:
            actual = result.geometry["bbox"]["min"]
            passed = all(math.isclose(x, y, abs_tol=1e-4, rel_tol=0) for x, y in zip(actual, case.bbox_min))
            validation["checks"].append({"name": "benchmark.absolute_position", "status": "passed" if passed else "failed",
                                          "expected": list(case.bbox_min), "actual": actual, "tolerance": 1e-4,
                                          "evidence": "Kernel bounding-box minimum compared with independent coordinate requirement"})
            if not passed:
                validation["status"] = "failed"
        row["validation"] = validation
        row["constraints_satisfied"] = validation["status"] == "passed"
        row["geometry"] = result.geometry
        row["feature_trace"] = result.trace
        # Even a design-constraint failure may produce a valid exchange file. Export is
        # measured independently and is never counted as a fully successful design.
        row["artifacts"] = engine.export(result, directory)
        row["export_success"] = bool(result.geometry.get("step_roundtrip", {}).get("valid"))
        if not row["constraints_satisfied"]:
            row["failure_category"] = "ConstraintViolationError" if validation["status"] == "failed" else "UnverifiedConstraint"
    except Exception as exc:
        row["failure_category"] = type(exc).__name__
        row["error"] = exc.to_dict() if hasattr(exc, "to_dict") else {"message": str(exc)}
        row["traceback"] = traceback.format_exc(limit=8)
    row["latency_seconds"] = round(time.perf_counter() - start, 6)
    row["complete_success"] = all(row[key] for key in ("execution_success", "geometry_valid", "constraints_satisfied", "export_success"))
    (directory / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2))
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    latencies = sorted(row["latency_seconds"] for row in rows)
    metric_fields = {"execution_success_rate": "execution_success", "geometry_validity_rate": "geometry_valid",
                     "constraint_satisfaction_rate": "constraints_satisfied", "export_success_rate": "export_success",
                     "complete_success_rate": "complete_success"}
    result = {name: ratio(sum(bool(row[field]) for row in rows), total) for name, field in metric_fields.items()}
    result.update({"latency_seconds": {"count": total, "mean": statistics.mean(latencies) if total else None,
                                        "median": statistics.median(latencies) if total else None,
                                        "p95": latencies[max(0, math.ceil(total*.95)-1)] if total else None},
                   "failure_categories": dict(Counter(row["failure_category"] for row in rows if row["failure_category"])),
                   "edit_success_rate": ratio(0, 0), "repair_recovery_rate": ratio(0, 0),
                   "metric_notes": "Generation denominator includes every selected case, including static-validation and kernel failures. Edit/repair not attempted by fixture runner; see acceptance workflow evidence. Latency covers compile, real build, validation and all three exports; excludes process startup, model calls and browser rendering."})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[], help="Exact case name; repeat to select multiple")
    parser.add_argument("--report-copy", type=Path, help="Optional small results JSON destination (no CAD binaries)")
    args = parser.parse_args()
    cases = corpus()
    if args.case:
        names = {c.name for c in cases}
        unknown = set(args.case)-names
        if unknown:
            parser.error(f"Unknown cases: {sorted(unknown)}")
        cases = [c for c in cases if c.name in args.case]
    args.output.mkdir(parents=True, exist_ok=True)
    frozen = {"version": CORPUS_VERSION, "sha256": corpus_hash(), "cases": [c.to_dict() for c in cases]}
    (args.output / "frozen-corpus.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2))
    started = datetime.now(timezone.utc).isoformat()
    rows = []
    for selected in cases:
        row = run_case(selected, args.output)
        rows.append(row)
        print(json.dumps({"case": selected.name, "success": row["complete_success"],
                          "failure": row["failure_category"], "seconds": row["latency_seconds"]}), flush=True)
    report = {"mode": "deterministic_ir_to_real_kernel", "started_at": started,
              "corpus_version": CORPUS_VERSION, "corpus_sha256": corpus_hash(),
              "environment": environment(),
              "summary": summarize(rows), "categories": {category: summarize([r for r in rows if r["category"] == category]) for category in ("basic", "medium")},
              "cases": rows, "baseline": {"comparable_3d_baseline": None,
                  "reason": "Pre-refactor checkout only has 2D DXF generation; no comparable B-Rep/STEP/3D edit benchmark exists. No improvement percentage is asserted."}}
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    (args.output / "report.json").write_text(serialized)
    if args.report_copy:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(serialized)
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0 if all(r["complete_success"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
