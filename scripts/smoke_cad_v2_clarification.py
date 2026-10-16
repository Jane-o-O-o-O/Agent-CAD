#!/usr/bin/env python3
"""Real HTTP clarification/restart/STEP smoke, without model or executor stubs.

Set CAD_V2_LOCAL_TOKEN, run --phase create, restart the server with its data
volume intact, then run --phase verify with the same --state path. --phase all
executes both phases in one process and explicitly does not verify a restart.
Verification requires build123d and OCP in the script's Python environment.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


PROMPT = (
    "长80mm宽50mm安装板，居中对称四个直径6mm的通孔，水平孔距60mm，五个孔"
)
EXPECTED = {"bbox": [80, 50, 8], "solids": 1, "hole_count": 4, "hole_diameter": 6,
            "hole_positions": [[10, 10, 0], [10, 40, 0], [70, 10, 0], [70, 40, 0]],
            "volume": 80 * 50 * 8 - 4 * math.pi * 3 ** 2 * 8}
STOP_STATUSES = {"completed", "failed", "cancelled", "awaiting_input"}


def ensure(condition, message, actual=None):
    if not condition:
        detail = "" if actual is None else ": " + json.dumps(actual, ensure_ascii=True)
        raise AssertionError(message + detail)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


class HTTPClient:
    def __init__(self, base_url, token, timeout):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        self.timeout = timeout

    def request(self, path, payload=None, raw=False):
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8") if payload is not None else None
        request = Request(self.base_url + path, data=data, headers=self.headers)
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read()
                if raw:
                    return content, response.headers
                result = json.loads(content)
                ensure(result.get("code") == 0, "API returned an error", result)
                return result["data"]
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise AssertionError(f"HTTP {error.code} at {path}: {detail}") from error

    def ready(self):
        deadline = time.monotonic() + min(self.timeout, 60)
        while True:
            try:
                status = self.request("/api/v1/auth/status")
                ensure(status.get("auth_provider") == "local", "This smoke requires the authenticated local CAD application")
                ensure(status.get("authenticated") is True, "CAD_V2_LOCAL_TOKEN was rejected")
                ensure(status.get("user", {}).get("created_at"), "Local application startup marker is absent")
                return status["user"]["created_at"]
            except (URLError, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.5)

    def wait_job(self, job):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            current = self.request(f"/api/v1/cad-v2/jobs/{job['id']}")
            if current["status"] in STOP_STATUSES:
                return current
            time.sleep(.3)
        raise AssertionError(f"Job {job['id']} exceeded the smoke deadline")


def awaiting(job, missing):
    ensure(job.get("status") == "awaiting_input", "Expected a clarification question", job)
    ensure(job.get("revision_id") is None, "A waiting job must not publish a revision", job)
    error = job.get("error") or {}
    ensure(error.get("code", error.get("type")) == "RequirementError", "Expected a design requirement question", error)
    ensure(set(error.get("details", {}).get("missing", [])) == set(missing),
           "Unexpected missing requirements", error)
    ensure(bool(error.get("message", "").strip()), "Clarification question is empty", error)


def submit_step(client, state, path, label, payload):
    steps = state.setdefault("steps", {})
    if label not in steps:
        steps[label] = {"submitted_at": timestamp(), "payload": payload}
        save_json(path, state)
    step = steps[label]
    if "job_id" not in step:
        admitted = client.request("/api/v1/cad-v2/jobs", step["payload"])
        step["job_id"] = admitted["id"]
        save_json(path, state)
    job = client.wait_job({"id": step["job_id"]})
    step["job"] = job
    save_json(path, state)
    return job


def create_phase(client, path, marker):
    run_id = uuid4().hex
    state = {"schema": "cad-v2-clarification-smoke-1", "run_id": run_id,
             "phase": "creating", "created_at": timestamp(), "base_url": client.base_url,
             "server_before_restart": marker, "prompt": PROMPT, "expected": EXPECTED,
             "request_ids": {label: f"clarification-smoke-{run_id}-{label}"
                             for label in ("initial", "count", "thickness", "pitch_y")}}
    save_json(path, state)
    initial = submit_step(client, state, path, "initial", {
        "prompt": state["prompt"], "name": "Clarification restart and independent STEP smoke",
        "request_id": state["request_ids"]["initial"],
    })
    awaiting(initial, ["hole_count"])
    state["document_id"] = initial["document_id"]
    count = submit_step(client, state, path, "count", {
        "prompt": "四个孔", "clarification_job_id": initial["id"],
        "request_id": state["request_ids"]["count"],
    })
    awaiting(count, ["thickness", "pitch_y"])
    ensure(count["document_id"] == state["document_id"], "Clarification created another document")
    prefix = f"/api/v1/cad-v2/documents/{state['document_id']}"
    document = client.request(prefix)
    ensure(document["current_revision"] is None, "Incomplete requirements published a revision", document)
    ensure(client.request(prefix + "/revisions") == [], "Incomplete draft has published revisions")
    state.update(phase="awaiting_restart", waiting_job_id=count["id"], waiting_question=count["error"],
                 saved_before_restart=timestamp())
    save_json(path, state)
    print(json.dumps({"phase": "create", "passed": True, "document_id": state["document_id"],
                      "waiting_job_id": count["id"], "missing": ["thickness", "pitch_y"],
                      "state": str(path)}, ensure_ascii=True))
    return state


def independent_step_geometry(step_path, expected):
    from build123d import Align, Cylinder, GeomType, Pos, import_step
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepCheck import BRepCheck_Analyzer

    shape = import_step(step_path)
    bbox = shape.bounding_box()
    tolerance = 1e-5
    measured = {"method": "independent STEP import, OCP surfaces and exact bore-clearance booleans",
                "build123d_version": version("build123d"), "bbox": {"min": list(bbox.min),
                "max": list(bbox.max), "size": list(bbox.size)},
                "volume": sum(float(solid.volume) for solid in shape.solids()),
                "solids": len(shape.solids()), "valid": bool(BRepCheck_Analyzer(shape.wrapped).IsValid()),
                "holes": []}
    ensure(measured["valid"], "Downloaded STEP has invalid topology", measured)
    ensure(measured["solids"] == expected["solids"], "Downloaded STEP has the wrong solid count", measured)
    ensure(all(math.isclose(a, b, abs_tol=tolerance, rel_tol=0)
               for a, b in zip(measured["bbox"]["size"], expected["bbox"])), "STEP dimensions differ", measured)
    ensure(all(abs(value) <= tolerance for value in measured["bbox"]["min"]), "STEP body origin differs", measured)
    ensure(math.isclose(measured["volume"], expected["volume"], abs_tol=tolerance, rel_tol=1e-8),
           "STEP volume differs from the analytical four-hole plate", measured)
    for face in shape.faces().filter_by(GeomType.CYLINDER):
        adaptor = BRepAdaptor_Surface(face.wrapped)
        cylinder = adaptor.Cylinder()
        location, direction = cylinder.Location(), cylinder.Axis().Direction()
        axis = [direction.X(), direction.Y(), direction.Z()]
        origin = [location.X(), location.Y(), location.Z()]
        point, normal = list(face.position_at(.25, .5)), list(face.normal_at(.25, .5))
        offset = [point[index] - origin[index] for index in range(3)]
        projection = sum(a * b for a, b in zip(offset, axis))
        radial = [offset[index] - projection * axis[index] for index in range(3)]
        inward = sum(a * b for a, b in zip(radial, normal)) < 0
        if not inward or abs(adaptor.LastUParameter() - adaptor.FirstUParameter()) < 2 * math.pi - tolerance:
            continue
        ensure(abs(axis[0]) <= tolerance and abs(axis[1]) <= tolerance and abs(abs(axis[2]) - 1) <= tolerance,
               "STEP has an unexpected bore direction", axis)
        diameter = cylinder.Radius() * 2
        ensure(math.isclose(diameter, expected["hole_diameter"], abs_tol=tolerance, rel_tol=0),
               "STEP has an unexpected bore diameter", diameter)
        wall_box = face.bounding_box()
        ensure(abs(wall_box.min.Z) <= tolerance and abs(wall_box.max.Z - expected["bbox"][2]) <= tolerance,
               "STEP bore wall does not span the full plate", [wall_box.min.Z, wall_box.max.Z])
        position = [origin[0], origin[1], 0.0]
        if any(math.dist(position, hole["position"]) <= tolerance for hole in measured["holes"]):
            continue
        probe = Pos(origin[0], origin[1], -1) * Cylinder(cylinder.Radius() * .9, expected["bbox"][2] + 2,
                                                      align=(Align.CENTER, Align.CENTER, Align.MIN))
        intersection = shape & probe
        remaining = sum(float(solid.volume) for solid in intersection.solids()) if intersection is not None else 0.0
        ensure(remaining <= 1e-7, "STEP bore contains material and is not through", {"position": position, "volume": remaining})
        measured["holes"].append({"position": position, "diameter": diameter, "through": True,
                                  "wall_z_bounds": [wall_box.min.Z, wall_box.max.Z], "remaining_material_volume": remaining})
    ensure(len(measured["holes"]) == expected["hole_count"], "STEP has the wrong measured hole count", measured)
    measured["holes"].sort(key=lambda hole: hole["position"])
    ensure(all(math.dist(hole["position"], position) <= tolerance
               for hole, position in zip(measured["holes"], sorted(expected["hole_positions"]))),
           "STEP hole coordinates differ", measured)
    return measured


def verify_phase(client, path, marker, require_restart):
    state = json.loads(path.read_text(encoding="utf-8"))
    ensure(state.get("schema") == "cad-v2-clarification-smoke-1", "Unrecognized smoke state")
    restarted = marker != state["server_before_restart"]
    ensure(not require_restart or restarted, "The application has not restarted since phase create; use all for a run without restart")
    state.update(server_after_restart=marker, restart_verified=restarted if require_restart else False)
    prefix = f"/api/v1/cad-v2/documents/{state['document_id']}"
    if state["phase"] != "verified":
        recovered = client.request(f"/api/v1/cad-v2/jobs/{state['waiting_job_id']}")
        awaiting(recovered, ["thickness", "pitch_y"])
        ensure(recovered["error"] == state["waiting_question"], "Persisted clarification question changed after restart")
        if "pitch_y" not in state.get("steps", {}):
            ensure(client.request(prefix)["current_revision"] is None, "A waiting draft was published after restart")
        thickness = submit_step(client, state, path, "thickness", {
            "prompt": "厚度8mm", "clarification_job_id": recovered["id"],
            "request_id": state["request_ids"]["thickness"],
        })
        awaiting(thickness, ["pitch_y"])
        ensure(thickness["document_id"] == state["document_id"], "Thickness answer created another document")
        final = submit_step(client, state, path, "pitch_y", {
            "prompt": "30mm", "clarification_job_id": thickness["id"],
            "request_id": state["request_ids"]["pitch_y"],
        })
        ensure(final["status"] == "completed", "Clarified job did not complete", final)
        ensure(final["document_id"] == state["document_id"], "Final answer created another document")
        state["revision_id"] = final["revision_id"]
    else:
        final = client.request(f"/api/v1/cad-v2/jobs/{state['steps']['pitch_y']['job_id']}")
    ensure(final.get("report", {}).get("validation", {}).get("status") == "passed", "Server geometry validation failed", final)
    isolation = final["report"].get("isolation", {})
    ensure(isolation.get("mode") == "linux-landlock-seccomp" and isolation.get("os_enforced") is True,
           "Real Linux isolation was not used", isolation)
    revision = client.request(prefix + f"/revisions/{state['revision_id']}")
    requirements = revision["report"].get("requirements", {})
    coverage = revision["report"].get("requirement_coverage", {})
    ensure(coverage.get("status") == "passed", "Trusted requirement coverage did not pass", coverage)
    pitch_requirements = [item for item in requirements.get("requirements", [])
                          if item.get("kind") == "hole_pitch" and item.get("axis") == "y"
                          and isinstance(item.get("value"), (int, float))
                          and math.isclose(item["value"], 30, abs_tol=1e-7, rel_tol=0)
                          and item.get("source_quote", "").strip() == "30mm"]
    ensure(bool(pitch_requirements), "Trusted requirements omitted the bare vertical-pitch answer", requirements)
    coverage_by_id = {check["id"]: check for check in coverage.get("checks", [])}
    ensure(all(coverage_by_id.get(item["id"], {}).get("status") == "passed" for item in pitch_requirements),
           "The source-backed vertical-pitch obligation was not verified", coverage)
    requirements = revision["report"].get("requirements", {})
    ensure(requirements.get("raw_request") == state["prompt"], "Original requirements were not persisted")
    ensure(requirements.get("answers") == ["四个孔", "厚度8mm", "30mm"], "Clarification answers were lost", requirements)
    ensure(revision["report"].get("requirement_coverage", {}).get("status") == "passed",
           "Independent requirement coverage did not pass", revision["report"])
    ensure(client.request(prefix)["current_revision"] == state["revision_id"], "Completed revision was not published")
    ensure(len(client.request(prefix + "/revisions")) == 1, "Clarification published extra revisions")
    hashes = {}
    artifact_paths = {}
    for kind in ("step", "ir"):
        data, headers = client.request(prefix + f"/revisions/{state['revision_id']}/artifacts/{kind}", raw=True)
        digest = hashlib.sha256(data).hexdigest()
        ensure(bool(data) and digest == revision["artifacts"][kind]["sha256"], "Artifact hash differs from stored revision", kind)
        ensure(headers.get("x-cad-revision") == state["revision_id"], "Artifact revision header differs", kind)
        if kind == "step":
            ensure(b"ISO-10303-21" in data, "Downloaded artifact is not STEP")
        artifact_path = path.with_name(path.stem + (".step" if kind == "step" else ".ir.json"))
        artifact_path.write_bytes(data)
        hashes[kind] = digest
        artifact_paths[kind] = str(artifact_path)
    measured = independent_step_geometry(Path(artifact_paths["step"]), state["expected"])
    report = {"passed": True, "verified_at": timestamp(), "restart_verified": state["restart_verified"],
              "server_before_restart": state["server_before_restart"], "server_after_restart": marker,
              "document_id": state["document_id"], "revision_id": state["revision_id"],
              "expected": state["expected"], "jobs": {label: step["job"] for label, step in state["steps"].items()},
              "server_report": revision["report"], "independent_step_geometry": measured,
              "bare_pitch_requirement_ids": [item["id"] for item in pitch_requirements],
              "artifact_paths": artifact_paths, "artifact_sha256": hashes}
    report_path = path.with_name(path.stem + ".report.json")
    save_json(report_path, report)
    state.update(phase="verified", verified_at=report["verified_at"], artifact_sha256=hashes,
                 artifact_paths=artifact_paths, report_path=str(report_path),
                 report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest())
    save_json(path, state)
    print(json.dumps({"phase": "verify", "passed": True, "restart_verified": state["restart_verified"],
                      "document_id": state["document_id"], "revision_id": state["revision_id"],
                      "hole_count": len(measured["holes"]), "bbox": measured["bbox"]["size"],
                      "artifact_sha256": hashes, "report": str(report_path)}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5179")
    parser.add_argument("--phase", choices=("create", "verify", "all"), required=True,
                        help="create stops at a durable question; verify requires restart; all skips restart verification")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240, help="Seconds allowed for each real build job")
    args = parser.parse_args()
    ensure(math.isfinite(args.timeout) and args.timeout > 0, "Timeout must be finite and positive")
    token = os.environ.get("CAD_V2_LOCAL_TOKEN", "")
    ensure(bool(token), "Set CAD_V2_LOCAL_TOKEN before running the smoke")
    client = HTTPClient(args.base_url, token, args.timeout)
    marker = client.ready()
    if args.phase in {"create", "all"}:
        create_phase(client, args.state, marker)
    if args.phase in {"verify", "all"}:
        verify_phase(client, args.state, client.ready(), require_restart=args.phase == "verify")


if __name__ == "__main__":
    main()
