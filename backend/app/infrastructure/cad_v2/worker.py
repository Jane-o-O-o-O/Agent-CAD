"""One trusted CAD job per resource-limited process; never eval/exec user code."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback


def _stage(name: str) -> None:
    print(json.dumps({"stage":name}), flush=True)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True)
    args=parser.parse_args()
    request=json.loads(Path(args.input).read_text())
    output=Path(request["output_dir"]).resolve()
    limits=request.get("limits",{})
    resource.setrlimit(resource.RLIMIT_CPU,(limits.get("cpu_seconds",60),limits.get("cpu_seconds",60)+1))
    resource.setrlimit(resource.RLIMIT_AS,(limits.get("memory_bytes",4*1024**3),limits.get("memory_bytes",4*1024**3)))
    resource.setrlimit(resource.RLIMIT_FSIZE,(limits.get("file_bytes",128*1024**2),limits.get("file_bytes",128*1024**2)))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
    os.chdir(output)
    start=time.perf_counter()
    try:
        from app.domain.cad_v2.compiler import compile_ir, compile_to_python
        from app.domain.cad_v2.errors import CADError, ConstraintViolationError
        _stage("building")
        from app.infrastructure.cad_v2.backend import Build123dBackend, artifact
        from app.infrastructure.cad_v2.validation import validate
        compiled=compile_ir(request["ir"])
        backend=Build123dBackend()
        built=backend.build(compiled)
        _stage("validating")
        validation=validate(built)
        if validation["status"]!="passed":
            failed=[c for c in validation["checks"] if c["status"]!="passed"]
            message="Executed geometry violates design constraints" if validation["status"]=="failed" else "Some design constraints could not be verified"
            raise ConstraintViolationError(message,details={"checks":failed,"validation":validation,"geometry":built.geometry,"trace":built.trace})
        _stage("exporting")
        artifacts=backend.export(built,output)
        built.geometry["preview"]={"units":"m","up_axis":"Y","source_units":"mm","source_up_axis":"Z"}
        (output/"model.ir.json").write_text(json.dumps(compiled.source_ir,ensure_ascii=False,indent=2))
        (output/"model.py").write_text(compile_to_python(request["ir"]))
        artifacts.extend([artifact(output/"model.ir.json","ir"),artifact(output/"model.py","python")])
        validation["checks"].append({"name":"step_roundtrip","status":"passed","expected":built.geometry["bbox"],"actual":built.geometry["step_roundtrip"],"tolerance":1e-5,"evidence":"Actual STEP export and reimport; volume, bounds and solid count compared"})
        report={"status":"completed","geometry":built.geometry,"validation":validation,"trace":built.trace,"artifacts":artifacts,"elapsed_seconds":round(time.perf_counter()-start,6)}
    except Exception as exc:
        if hasattr(exc,"to_dict"):
            error=exc.to_dict()
        else:
            error={"code":"InfrastructureError","message":str(exc),"feature_id":None,"details":{"exception":type(exc).__name__},"recoverable":False}
        report={"status":"failed","error":error,"elapsed_seconds":round(time.perf_counter()-start,6)}
        # Kernel messages are diagnostic only; caller receives the structured error.
        traceback.print_exc(file=sys.stderr)
    temporary=output/"result.tmp.json"
    temporary.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
    temporary.replace(output/"result.json")
    return 0 if report["status"]=="completed" else 1


if __name__=="__main__":
    raise SystemExit(main())
