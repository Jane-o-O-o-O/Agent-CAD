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
    parser.add_argument("--isolation", choices=("strict", "process-dev"), default="strict",
                        help="Explicit process-dev only on trusted local development hosts")
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
               CAD_V2_FRONTEND_DIST=str(repo / "frontend/dist"), PYTHONPATH=str(repo / "backend"),
               CAD_V2_ISOLATION=args.isolation)
    base = f"http://127.0.0.1:{port}"
    report = {"status": "failed", "recorded_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Built Vue SPA, local login, HTTP/SSE, real OCP workers, WebGL, revision exports and restore",
              "checks": [], "page_errors": [], "api_failures": [], "model_calls": 0,
              "requested_isolation": args.isolation}
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
            report["actual_isolation"] = revision["report"]["isolation"]
            for kind in ("step", "stl", "glb", "ir", "python", "dxf", "svg"):
                page.get_by_label("导出格式").select_option(kind)
                with page.expect_download() as event:
                    page.get_by_role("button", name="导出", exact=True).click()
                target = args.output / event.value.suggested_filename
                event.value.save_as(target)
                assert hashlib.sha256(target.read_bytes()).hexdigest() == revision["artifacts"][kind]["sha256"]
                if kind == "step":
                    step_check(target, 8)
                if kind == "dxf":
                    import ezdxf
                    drawing = ezdxf.readfile(target)
                    assert not drawing.audit().has_errors
                    assert drawing.header['$INSUNITS'] == 4
                    assert len(drawing.modelspace().query('DIMENSION')) >= 6
            checked("seven_UI_downloads_match_displayed_revision_hashes")
            page.get_by_role("tab", name="二维工程图", exact=True).click()
            drawing_image = page.get_by_role("img", name=re.compile("当前模型版本的前视"))
            drawing_image.wait_for()
            page.wait_for_function("() => { const img = document.querySelector('img[alt^=\"当前模型版本\"]'); return img?.complete && img.naturalWidth > 0; }")
            page.get_by_text(f"图纸版本 {second['revision_id'][:8]}", exact=False).wait_for()
            page.get_by_role("button", name="放大工程图", exact=True).click()
            page.get_by_role("button", name="适配图纸", exact=True).click()
            page.screenshot(path=str(args.output / "drawings.png"), full_page=True)
            checked("same_revision_2D_three_views_and_zoom")
            page.get_by_role("tab", name="三维模型", exact=True).click()
            page.get_by_text(f"预览版本 {second['revision_id'][:8]}").wait_for()
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
            page.locator('#cad-v2-prompt').fill('将左边两个孔的直径改成8mm，并将右边两个孔向左移动5mm，其他保持不变。')
            with page.expect_response(lambda r: r.url.endswith('/edits') and r.request.method == 'POST') as submitted:
                page.get_by_role('button', name='修改并重新建模', exact=True).click()
            natural_edit = wait_job(submitted.value.json()['data']['id'])
            page.get_by_text(f"预览版本 {natural_edit['revision_id'][:8]}").wait_for()
            natural_step = args.output / 'natural-edit.step'
            response = client.get(f"/api/v1/cad-v2/documents/{natural_edit['document_id']}/revisions/{natural_edit['revision_id']}/artifacts/step")
            response.raise_for_status()
            natural_step.write_bytes(response.content)
            sys.path.insert(0, str(repo / 'backend'))
            from build123d import import_step
            from app.infrastructure.cad_v2.validation import cylindrical_surfaces
            bores = [surface for surface in cylindrical_surfaces(import_step(natural_step)) if surface['inward']]
            assert {(round(s['origin'][0], 4), round(s['origin'][1], 4), round(s['radius']*2, 4)) for s in bores} == {(10,10,8), (10,40,8), (65,10,6), (65,40,6)}
            checked('natural_language_multi_operation_UI_edit_with_independent_STEP_check')
            # Real assembly created through the same authenticated public API.
            sys.path.insert(0, str(repo / 'backend'))
            from tests.cad_v2.test_assembly import flange_shaft_spacer_ir
            response = client.post('/api/v1/cad-v2/jobs', json={"ir": flange_shaft_spacer_ir(), "name": "Flange / shaft / spacer"})
            response.raise_for_status()
            assembly_job = wait_job(response.json()['data']['id'])
            page.reload(wait_until="domcontentloaded")
            page.locator('#document-select').select_option(assembly_job['document_id'])
            page.get_by_text(f"预览版本 {assembly_job['revision_id'][:8]}").wait_for()
            page.get_by_role('button', name='选择组件 shaft_1', exact=True).click()
            page.get_by_label('显示组件 flange_1', exact=True).uncheck()
            page.get_by_label('显示组件 flange_1', exact=True).check()
            page.get_by_label('组件 Z', exact=True).fill('-7')
            with page.expect_response(lambda r: r.url.endswith('/edits') and r.request.method == 'POST') as submitted:
                page.get_by_role('button', name='移动组件并验证', exact=True).click()
            moved = wait_job(submitted.value.json()['data']['id'])
            page.get_by_text(f"预览版本 {moved['revision_id'][:8]}").wait_for()
            page.screenshot(path=str(args.output / 'assembly.png'), full_page=True)
            assembly_revision = client.get(f"/api/v1/cad-v2/documents/{moved['document_id']}/revisions/{moved['revision_id']}").json()['data']
            for kind in ('step', 'bom_json', 'bom_csv'):
                page.get_by_label('导出格式').select_option(kind)
                with page.expect_download() as event:
                    page.get_by_role('button', name='导出', exact=True).click()
                target = args.output / ('assembly-' + event.value.suggested_filename)
                event.value.save_as(target)
                assert hashlib.sha256(target.read_bytes()).hexdigest() == assembly_revision['artifacts'][kind]['sha256']
                if kind == 'step':
                    from build123d import import_step
                    shape = import_step(target)
                    assert len(shape.solids()) == 3
                    assert {child.label for child in shape.children} == {'flange_1', 'shaft_1', 'spacer_1'}
                    shaft = next(child for child in shape.children if child.label == 'shaft_1')
                    assert abs(shaft.bounding_box().min.Z + 7) < 1e-4
                if kind == 'bom_json':
                    assert json.loads(target.read_text())['component_count'] == 3
            checked('assembly_tree_select_hide_move_and_STEP_BOM_downloads')
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
