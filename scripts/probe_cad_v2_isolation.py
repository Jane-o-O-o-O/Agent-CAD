#!/usr/bin/env python3
"""Deployment gate: exit nonzero unless actual strict denial probes all pass."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="cad-isolation-") as directory:
        root = Path(directory)
        outside = root / "supervisor-secret"
        outside.write_text("private supervisor data")
        runtime = root / "runtime-file"
        runtime.write_text("read-only runtime data")
        job = root / "job"
        job.mkdir(mode=0o700)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONNOUSERSITE": "1", "PYTHONPATH": os.pathsep.join(
                   str(Path(p).resolve()) for p in os.environ.get("PYTHONPATH", str(Path.cwd() / "backend")).split(os.pathsep) if p),
               "HOME": str(job), "TMPDIR": str(job)}
        result = subprocess.run([sys.executable, "-s", "-m", "app.infrastructure.cad_v2.sandbox",
                                 "--mode", "strict", "--job-dir", str(job), "--probe-outside", str(outside), "--probe-runtime", str(runtime)],
                                cwd=job, env=env, capture_output=True, text=True, timeout=30)
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode:
            return result.returncode
        proof = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if not any(item.get("probe", {}).get("passed") for item in proof):
            print("Strict isolation did not provide verified denial evidence", file=sys.stderr)
            return 1
        if outside.read_text() != "private supervisor data":
            return 1
        if runtime.read_text() != "read-only runtime data":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
