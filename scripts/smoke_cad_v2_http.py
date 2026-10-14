#!/usr/bin/env python3
"""Real HTTP/kernel/export/persistence smoke; no model or geometry stubs."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5179")
    parser.add_argument("--phase", choices=("create", "verify"), required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ["CAD_V2_LOCAL_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    def request(path, payload=None, auth=True, raw=False):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(args.base_url + path, data=data, headers={**(headers if auth else {}), "Content-Type": "application/json"})
        with urlopen(req, timeout=20) as response:
            content = response.read()
            return (content, response.headers) if raw else json.loads(content)["data"]
    for attempt in range(60):
        try:
            request("/api/v1/auth/status", auth=False)
            break
        except (URLError, OSError):
            if attempt == 59:
                raise
            time.sleep(1)
    def wait_job(job):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            current = request(f"/api/v1/cad-v2/jobs/{job['id']}")
            if current["status"] in {"completed", "failed", "cancelled"}:
                assert current["status"] == "completed", current
                assert current["report"]["isolation"]["mode"] == "linux-landlock-seccomp", current
                assert current["report"]["isolation"]["os_enforced"] is True
                return current
            time.sleep(.3)
        raise AssertionError(f"Job {job['id']} exceeded smoke deadline")
    if args.phase == "create":
        frontend, frontend_headers = request("/chat/cad-v2", auth=False, raw=True)
        assert b'<div id="app">' in frontend and b"/assets/" in frontend
        assert "text/html" in frontend_headers["content-type"]
        try:
            request("/api/v1/cad-v2/documents", auth=False)
            raise AssertionError("Unauthenticated document access unexpectedly succeeded")
        except HTTPError as error:
            assert error.code == 401
        login = request("/api/v1/auth/login", {"email": "local@localhost", "password": token}, auth=False)
        assert login["access_token"] == token
        ir = {"parameters": {"width": {"value": 20}, "depth": {"value": 10}, "height": {"value": 8}},
              "features": [{"id": "body", "type": "Box", "params": {"width": "width", "depth": "depth", "height": "height"}}],
              "expected_geometry": {"bbox": ["width", "depth", "height"], "volume": "width*depth*height"}}
        first = wait_job(request("/api/v1/cad-v2/jobs", {"ir": ir, "name": "Docker strict isolation smoke"}))
        prefix = f"/api/v1/cad-v2/documents/{first['document_id']}"
        edited = wait_job(request(prefix + "/edits", {"expected_revision": first["revision_id"], "parameters": {"width": 25}}))
        assert edited["report"]["geometry"]["volume"] > first["report"]["geometry"]["volume"]
        restored = wait_job(request(prefix + "/restore", {"expected_revision": edited["revision_id"], "revision_id": first["revision_id"]}))
        events, _ = request(f"/api/v1/cad-v2/jobs/{restored['id']}/events", raw=True)
        assert b'"status": "completed"' in events
        state = {"document_id": first["document_id"], "current_revision": restored["revision_id"], "revisions": {}}
        for item in (first, edited, restored):
            revision = request(prefix + f"/revisions/{item['revision_id']}")
            assert revision["report"]["validation"]["status"] == "passed"
            state["revisions"][item["revision_id"]] = {}
            for format in ("step", "stl", "glb", "ir", "python", "dxf", "svg"):
                data, metadata = request(prefix + f"/revisions/{item['revision_id']}/artifacts/{format}", raw=True)
                digest = hashlib.sha256(data).hexdigest()
                assert data and digest == revision["artifacts"][format]["sha256"]
                assert metadata["x-cad-revision"] == item["revision_id"]
                if format == "step":
                    assert b"ISO-10303-21" in data
                if format == "glb":
                    assert data[:4] == b"glTF"
                if format == "dxf":
                    lines = data.decode("utf-8").splitlines()
                    assert len(lines) % 2 == 0
                    groups = [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines), 2)]
                    assert (0, "SECTION") in groups and (2, "ENTITIES") in groups and groups[-1] == (0, "EOF")
                    assert groups.count((0, "LINE")) >= 12
                    # This unperforated box has one callout per overall size;
                    # the three views deliberately do not duplicate dimensions.
                    assert groups.count((0, "DIMENSION")) == 3
                    assert all(groups.count((1000, role)) == 1 for role in
                               ("overall_width", "overall_depth", "overall_height"))
                    units = groups.index((9, "$INSUNITS"))
                    assert groups[units + 1] == (70, "4")  # DXF millimetres
                if format == "svg":
                    root = ElementTree.fromstring(data)
                    namespace = {"svg": "http://www.w3.org/2000/svg"}
                    assert root.tag == "{http://www.w3.org/2000/svg}svg"
                    assert {element.attrib.get("data-view") for element in root.findall("svg:g", namespace)} == {"front", "top", "right"}
                    assert len(root.findall(".//svg:line", namespace)) >= 12
                    measurement = json.loads(root.find("svg:metadata", namespace).text)
                    assert measurement["method"] == "exact_brep_hlr" and measurement["units"] == "mm"
                    assert all(math.isclose(actual, expected, abs_tol=1e-5) for actual, expected in zip(
                        measurement["bbox"]["size"], item["report"]["geometry"]["bbox"]["size"]))
                state["revisions"][item["revision_id"]][format] = digest
        args.state.write_text(json.dumps(state))
        print(json.dumps({"phase": "before-restart", "passed": True, "revisions": len(state["revisions"]), "isolation": "linux-landlock-seccomp"}))
    else:
        state = json.loads(args.state.read_text())
        prefix = f"/api/v1/cad-v2/documents/{state['document_id']}"
        assert request(prefix)["current_revision"] == state["current_revision"]
        assert len(request(prefix + "/revisions")) == len(state["revisions"])
        for revision_id, artifacts in state["revisions"].items():
            for format, expected in artifacts.items():
                data, _ = request(prefix + f"/revisions/{revision_id}/artifacts/{format}", raw=True)
                assert hashlib.sha256(data).hexdigest() == expected
        print(json.dumps({"phase": "after-restart", "passed": True, "immutable_artifacts": sum(map(len, state["revisions"].values()))}))


if __name__ == "__main__":
    main()
