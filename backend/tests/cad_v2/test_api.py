from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application.services.cad_v2_service import CADV2Service
from app.interfaces.cad_v2_app import create_app
from .test_service import FakeExecutor, sample_ir


TOKEN = "cad-v2-tests-local-token-only"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.asyncio
async def test_authenticated_create_revision_export_and_durable_sse(tmp_path):
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    await service.start()
    app = create_app(service, token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        response = await client.post("/api/v1/cad-v2/jobs", json={"ir": sample_ir(), "request_id": "once"})
        assert response.status_code == 202, response.text
        assert response.json()["code"] == 0
        job = response.json()["data"]
        await service.wait(job["id"])
        completed = (await client.get(f"/api/v1/cad-v2/jobs/{job['id']}")).json()["data"]
        assert completed["status"] == "completed", completed
        doc, revision = job["document_id"], completed["revision_id"]
        prefix = f"/api/v1/cad-v2/documents/{doc}"
        result = (await client.get(f"{prefix}/revisions/{revision}")).json()["data"]
        assert set(result["artifacts"]) == {"step", "stl", "glb", "ir", "python"}
        downloaded = await client.get(f"{prefix}/revisions/{revision}/artifacts/step")
        assert downloaded.status_code == 200
        assert downloaded.headers["x-cad-revision"] == revision
        assert downloaded.headers["etag"] == f'"{result["artifacts"]["step"]["sha256"]}"'
        assert len((await client.get("/api/v1/cad-v2/documents")).json()["data"]) == 1
        assert len((await client.get(f"{prefix}/revisions")).json()["data"]) == 1
        sse = await client.get(f"/api/v1/cad-v2/jobs/{job['id']}/events")
        assert sse.headers["content-type"].startswith("text/event-stream")
        ids = [int(line[4:]) for line in sse.text.splitlines() if line.startswith("id: ")]
        assert ids == sorted(set(ids))
        snapshots = [json.loads(line[6:]) for line in sse.text.splitlines() if line.startswith("data: ")]
        assert snapshots[0]["status"] == "created"
        assert snapshots[-1]["status"] == "completed"
        assert any(item["status"] == "validating" for item in snapshots)
        resumed = await client.get(f"/api/v1/cad-v2/jobs/{job['id']}/events", headers={"Last-Event-ID": str(ids[-2])})
        assert resumed.text.count("event: job") == 1
        bad_cursor = await client.get(f"/api/v1/cad-v2/jobs/{job['id']}/events", headers={"Last-Event-ID": "invalid"})
        assert bad_cursor.status_code == 400
    await service.shutdown()


@pytest.mark.asyncio
async def test_api_rejects_unauthenticated_invalid_requests_and_other_owner(tmp_path):
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    await service.start()
    other = await service.create("different-owner", ir=sample_ir())
    await service.wait(other["id"])
    app = create_app(service, token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/cad-v2/documents")).status_code == 401
        assert (await client.get("/api/v1/cad-v2/documents", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await client.get(f"/api/v1/cad-v2/jobs/{other['id']}", headers=HEADERS)).status_code == 404
        assert (await client.get(f"/api/v1/cad-v2/jobs/{other['id']}/events", headers=HEADERS)).status_code == 404
        assert (await client.post("/api/v1/cad-v2/jobs", json={}, headers=HEADERS)).status_code == 422
        assert (await client.post("/api/v1/cad-v2/jobs", json={"prompt": "hello", "ir": {}}, headers=HEADERS)).status_code == 422
        assert (await client.post("/api/v1/cad-v2/jobs", json={"prompt": "hello", "arbitrary": True}, headers=HEADERS)).status_code == 422
        assert (await client.post("/api/v1/cad-v2/jobs", json={"prompt": "x" * 2_100_001}, headers=HEADERS)).status_code == 413
    await service.shutdown()


@pytest.mark.asyncio
async def test_api_edit_conflict_restore_and_version_specific_download(tmp_path):
    service = CADV2Service(tmp_path, executor=FakeExecutor())
    await service.start()
    app = create_app(service, token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        job = (await client.post("/api/v1/cad-v2/jobs", json={"ir": sample_ir()})).json()["data"]
        await service.wait(job["id"])
        first = service.store.get_job(job["id"], "cad-v2-local")
        doc, revision = first["document_id"], first["revision_id"]
        prefix = f"/api/v1/cad-v2/documents/{doc}"
        response = await client.post(f"{prefix}/edits", json={"expected_revision": revision, "parameters": {"width": 90}})
        assert response.status_code == 202
        edit = response.json()["data"]
        await service.wait(edit["id"])
        updated = service.store.get_job(edit["id"], "cad-v2-local")
        assert updated["status"] == "completed", updated
        conflict = await client.post(f"{prefix}/edits", json={"expected_revision": revision, "parameters": {"width": 100}})
        assert conflict.status_code == 409
        boolean = await client.post(f"{prefix}/edits", json={"expected_revision": updated["revision_id"], "parameters": {"width": True}})
        assert boolean.status_code == 422
        restore = await client.post(f"{prefix}/restore", json={"expected_revision": updated["revision_id"], "revision_id": revision})
        assert restore.status_code == 202
        await service.wait(restore.json()["data"]["id"])
        old = await client.get(f"{prefix}/revisions/{revision}/artifacts/step")
        new = await client.get(f"{prefix}/revisions/{updated['revision_id']}/artifacts/step")
        assert old.content != new.content
        assert (await client.get(f"{prefix}/revisions/{revision}/artifacts/unknown")).status_code == 404
    await service.shutdown()


def test_local_app_requires_explicit_private_token_at_startup(tmp_path):
    app = create_app(CADV2Service(tmp_path, executor=FakeExecutor()), token="")
    with pytest.raises(RuntimeError, match="CAD_V2_LOCAL_TOKEN"):
        with TestClient(app):
            pass


@pytest.mark.asyncio
async def test_local_login_refresh_logout_uses_explicit_configured_credential(tmp_path):
    app = create_app(CADV2Service(tmp_path, executor=FakeExecutor()), token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/auth/status")).json()["data"]["authenticated"] is False
        wrong = await client.post("/api/v1/auth/login", json={"email": "local@localhost", "password": "wrong"})
        assert wrong.status_code == 401
        assert TOKEN not in wrong.text
        wrong_email = await client.post("/api/v1/auth/login", json={"email": "another@example.com", "password": TOKEN})
        assert wrong_email.status_code == 401
        login = await client.post("/api/v1/auth/login", json={"email": "local@localhost", "password": TOKEN})
        assert login.status_code == 200
        data = login.json()["data"]
        assert data["access_token"] == TOKEN
        assert data["user"]["email"] == "local@localhost"
        client.headers["Authorization"] = f"Bearer {data['access_token']}"
        assert (await client.get("/api/v1/auth/status")).json()["data"]["authenticated"] is True
        assert (await client.get("/api/v1/auth/me")).json()["data"]["id"] == "cad-v2-local"
        assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": "wrong"})).status_code == 401
        refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert refreshed.json()["data"]["access_token"] == TOKEN
        assert (await client.post("/api/v1/auth/logout")).status_code == 200


@pytest.mark.asyncio
async def test_local_login_failed_attempt_budget_and_no_implicit_default(tmp_path):
    app = create_app(CADV2Service(tmp_path, executor=FakeExecutor()), token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(10):
            assert (await client.post("/api/v1/auth/login", json={"email": "local@localhost", "password": "bad"})).status_code == 401
        assert (await client.post("/api/v1/auth/login", json={"email": "local@localhost", "password": "bad"})).status_code == 429
