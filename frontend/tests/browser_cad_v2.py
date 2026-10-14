"""Opt-in real browser acceptance of the built frontend and standalone CAD server.

Requires the CAD Python environment, Playwright and Chromium; npm run build first.
Run: python frontend/tests/browser_cad_v2.py --output /tmp/cad-v2-browser
Optional --chromium supplies an installed Chromium binary. No model calls are made.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--chromium", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True, exist_ok=True)
    if not (repo / "frontend/dist/index.html").is_file():
        parser.error("Build frontend/dist first with npm run build")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = {key: value for key, value in os.environ.items()
           if key not in {"API_KEY", "CAD_V2_API_KEY", "API_BASE", "CAD_V2_API_BASE"}}
    env.update(CAD_V2_LOCAL_TOKEN=token, CAD_V2_DATA_DIR=tempfile.mkdtemp(prefix="data-", dir=args.output),
               CAD_V2_FRONTEND_DIST=str(repo / "frontend/dist"), PYTHONPATH=str(repo / "backend"))
    base = f"http://127.0.0.1:{port}"
    report = {"status": "failed", "recorded_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Built Vue SPA, local login, HTTP/SSE, real OCP workers, WebGL, revision exports and restore",
              "checks": [], "page_errors": [], "api_failures": [], "model_calls": 0}
    log = (args.output / "server.log").open("w")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.interfaces.cad_v2_app:app",
                                "--host", "127.0.0.1", "--port", str(port), "--workers", "1"],
                               cwd=repo / "backend", env=env, stdout=log, stderr=subprocess.STDOUT)
    client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, trust_env=False, timeout=10)

    def checked(name: str, detail: object = True):
        report["checks"].append({"name": name, "status": "passed", "detail": detail})

    def wait_job(identifier: str) -> dict:
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/cad-v2/jobs/{identifier}")
            response.raise_for_status()
            job = response.json()["data"]
            if job["status"] in {"completed", "failed", "cancelled"}:
                assert job["status"] == "completed", job.get("error")
                return job
            time.sleep(0.15)
        raise TimeoutError("Browser-submitted CAD job did not finish")

    def step_check(path: Path, diameter: float):
        from build123d import import_step
        shape = import_step(path)
        assert shape.is_valid and len(shape.solids()) == 1
        assert all(abs(a-b) < 1e-4 for a, b in zip(shape.bounding_box().size, (80, 50, 8)))
        assert abs(shape.volume - (32000 - 4*math.pi*(diameter/2)**2*8)) < 1e-4
        checked(f"downloaded_STEP_diameter_{diameter}", {"volume": shape.volume, "bbox": list(shape.bounding_box().size)})

    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("CAD server exited; inspect server.log")
            try:
                if client.get("/health").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.1)
        else:
            raise TimeoutError("CAD server startup timed out")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=str(args.chromium) if args.chromium else None,
                args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            report["browser"] = browser.version
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
            page.set_default_timeout(20000)
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("response", lambda response: report["api_failures"].append({"path": response.url.split(base)[-1], "status": response.status})
                    if response.status >= 400 and "/api/" in response.url else None)
            page.goto(base + "/chat/cad-v2", wait_until="domcontentloaded")
            page.locator("#email").fill("local@localhost")
            page.locator('input[type="password"]').fill(token)
            page.locator('button[type="submit"]').click()
            page.locator("#cad-v2-prompt").wait_for()
            checked("real_local_login_and_workbench_navigation")
            with page.expect_response(lambda r: r.url.endswith("/cad-v2/jobs") and r.request.method == "POST") as submitted:
                page.get_by_role("button", name="生成三维零件", exact=True).click()
            first = wait_job(submitted.value.json()["data"]["id"])
            page.get_by_text(f"预览版本 {first['revision_id'][:8]}").wait_for()
            assert page.locator("canvas").count() == 1
            checked("prompt_HTTP_worker_SSE_WebGL_generation", {"revision": first["revision_id"], "stages": first["stage"]})
            for view in ("前", "后", "左", "右", "上", "下", "等轴", "适配"):
                page.get_by_role("button", name=view, exact=True).click()
            page.get_by_role("button", name="正交", exact=True).click()
            page.get_by_role("button", name="透视", exact=True).click()
            page.get_by_role("button", name="实体", exact=True).click()
            page.get_by_role("button", name="线框", exact=True).click()
            for control in ("网格", "坐标轴"):
                page.get_by_role("button", name=control, exact=True).click()
                page.get_by_role("button", name=control, exact=True).click()
            checked("standard_views_projection_wireframe_grid_axes_controls")
            page.locator("#param-hole_diameter").fill("8")
            with page.expect_response(lambda r: r.url.endswith("/edits") and r.request.method == "POST") as submitted:
                page.get_by_role("button", name="应用并重建", exact=True).click()
            second = wait_job(submitted.value.json()["data"]["id"])
            page.get_by_text(f"预览版本 {second['revision_id'][:8]}").wait_for()
            assert second["revision_id"] != first["revision_id"]
            checked("parameter_editor_publishes_new_geometry", {"before": first["revision_id"], "after": second["revision_id"]})
            revision = client.get(f"/api/v1/cad-v2/documents/{second['document_id']}/revisions/{second['revision_id']}").json()["data"]
            for kind in ("step", "stl", "glb", "ir", "python"):
                page.get_by_label("导出格式").select_option(kind)
                with page.expect_download() as event:
                    page.get_by_role("button", name="导出", exact=True).click()
                target = args.output / event.value.suggested_filename
                event.value.save_as(target)
                assert hashlib.sha256(target.read_bytes()).hexdigest() == revision["artifacts"][kind]["sha256"]
                if kind == "step":
                    step_check(target, 8)
            checked("five_UI_downloads_match_displayed_revision_hashes")
            page.get_by_label("导出格式").select_option("step")
            page.screenshot(path=str(args.output / "workbench.png"), full_page=True)
            page.get_by_role("tab", name="历史", exact=True).click()
            page.get_by_role("button", name=re.compile(r"版本 1")).click()
            page.get_by_text(f"预览版本 {first['revision_id'][:8]}").wait_for()
            with page.expect_download() as event:
                page.get_by_role("button", name="导出", exact=True).click()
            historical_step = args.output / "historical-diameter-6.step"
            event.value.save_as(historical_step)
            step_check(historical_step, 6)
            checked("history_preview_and_download_share_old_revision")
            with page.expect_response(lambda r: r.url.endswith("/restore") and r.request.method == "POST") as submitted:
                page.get_by_role("button", name="恢复为新版本", exact=True).click()
            restored = wait_job(submitted.value.json()["data"]["id"])
            page.get_by_text(f"预览版本 {restored['revision_id'][:8]}").wait_for()
            assert restored["revision_id"] not in {first["revision_id"], second["revision_id"]}
            checked("UI_restore_builds_new_revision")
            page.reload(wait_until="domcontentloaded")
            page.get_by_text(f"预览版本 {restored['revision_id'][:8]}").wait_for()
            checked("browser_reload_recovers_revision")
            assert not report["page_errors"], report["page_errors"]
            assert not report["api_failures"], report["api_failures"]
            report["status"] = "passed"
            browser.close()
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        client.close()
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
