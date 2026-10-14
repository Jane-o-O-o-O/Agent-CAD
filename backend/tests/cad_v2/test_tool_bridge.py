"""The chat bridge shares CAD ownership and reports jobs without false completion."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.services.tools.cad_v2 import CADV2Toolkit
from app.infrastructure.cad_v2.store import CADStore, StoreNotFound


@pytest.mark.asyncio
async def test_registration_schema_is_lazy_and_owner_is_not_a_tool_argument():
    from app.domain.services.agentscope_runtime.tool_adapter import create_agentscope_toolkit
    calls = []
    local = CADV2Toolkit("alice", service_factory=lambda: calls.append("constructed"))
    tools = {tool.name: tool for tool in local.get_tools()}
    assert set(tools) == {"cad_v2_generate", "cad_v2_job_status", "cad_v2_edit", "cad_v2_revision"}
    schemas = await create_agentscope_toolkit([local]).get_tool_schemas()
    for schema in schemas:
        properties = schema["function"]["parameters"]["properties"]
        assert "user_id" not in properties
        assert "owner" not in properties
        assert "service_factory" not in properties
    by_name = {schema["function"]["name"]: schema["function"] for schema in schemas}
    assert by_name["cad_v2_edit"]["parameters"]["required"] == ["document_id", "expected_revision"]
    assert {"prompt", "ir"} <= by_name["cad_v2_generate"]["parameters"]["properties"].keys()
    assert calls == []


def test_empty_owner_is_rejected_without_constructing_service():
    with pytest.raises(ValueError):
        CADV2Toolkit("")


@pytest.mark.asyncio
async def test_generate_and_edit_use_bound_owner_and_return_pending_job():
    calls = []
    job = {"id": "job-1", "document_id": "document-1", "status": "queued", "revision_id": None}
    class Service:
        async def create(self, owner, **payload):
            calls.append(("create", owner, payload))
            return job
        async def edit(self, owner, document_id, **payload):
            calls.append(("edit", owner, document_id, payload))
            return job
    toolkit = CADV2Toolkit("alice", service_factory=Service)
    generated = await toolkit.cad_v2_generate(prompt="80 x 50 x 8 mm plate")
    edited = await toolkit.cad_v2_edit("document-1", "rev-1", parameters={"hole_diameter": 8})
    assert calls[0] == ("create", "alice", {"prompt": "80 x 50 x 8 mm plate", "ir": None})
    assert calls[1] == ("edit", "alice", "document-1", {
        "expected_revision": "rev-1", "parameters": {"hole_diameter": 8}, "prompt": None,
    })
    for result in (generated, edited):
        assert result.success
        assert result.data["status"] == "queued"
        assert result.data["revision_id"] is None
        assert "not ready" in result.message
        assert result.data["links"]["events"] == "/api/v1/cad-v2/jobs/job-1/events"
        assert "revision" not in result.data["links"]


@pytest.mark.asyncio
async def test_real_store_job_lookup_rejects_other_owner(tmp_path):
    store = CADStore(tmp_path)
    job, _ = store.create_job("alice", "document-1", {"kind": "create", "ir": {}})
    factory = lambda: SimpleNamespace(store=store)
    own = await CADV2Toolkit("alice", factory).cad_v2_job_status(job["id"])
    other = await CADV2Toolkit("bob", factory).cad_v2_job_status(job["id"])
    assert own.success and own.data["job_id"] == job["id"]
    assert not other.success and other.data["error"]["code"] == "NotFound"
    assert job["id"] not in other.model_dump_json()


@pytest.mark.asyncio
async def test_failed_jobs_are_tool_failures_without_discarding_diagnosis(tmp_path):
    store = CADStore(tmp_path)
    job, _ = store.create_job("alice", "document-1", {"kind": "create", "ir": {}})
    store.update_job(job["id"], status="failed", error={"code": "InvalidTopologyError", "message": "No match"})
    result = await CADV2Toolkit("alice", lambda: SimpleNamespace(store=store)).cad_v2_job_status(job["id"])
    assert not result.success
    assert result.data["job"]["error"]["code"] == "InvalidTopologyError"


@pytest.mark.asyncio
async def test_revision_downloads_and_preview_share_exact_owned_revision():
    calls = []
    revision = {"id": "rev-old", "document_id": "document-1", "artifacts": {
        "step": {"filename": "model.step"}, "glb": {"filename": "model.glb"},
    }, "report": {"validation": {"status": "passed"}}}
    class Store:
        def get_revision(self, document_id, revision_id, owner):
            calls.append((document_id, revision_id, owner))
            if owner != "alice":
                raise StoreNotFound("CAD revision not found")
            return revision
    factory = lambda: SimpleNamespace(store=Store())
    result = await CADV2Toolkit("alice", factory).cad_v2_revision("document-1", "rev-old")
    assert result.success
    assert calls == [("document-1", "rev-old", "alice")]
    assert result.data["downloads"]["step"].endswith("/revisions/rev-old/artifacts/step")
    assert result.data["downloads"]["glb"].endswith("/revisions/rev-old/artifacts/glb")
    assert parse_qs(urlparse(result.data["workbench"]).query) == {"document": ["document-1"], "revision": ["rev-old"]}
    denied = await CADV2Toolkit("bob", factory).cad_v2_revision("document-1", "rev-old")
    assert not denied.success


@pytest.mark.asyncio
async def test_tool_uses_http_service_singleton_lazily(monkeypatch):
    from app.application.services import cad_v2_service
    calls = []
    singleton = SimpleNamespace(store=SimpleNamespace(get_job=lambda job_id, owner: {
        "id": job_id, "document_id": "doc", "status": "running", "revision_id": None,
    }))
    monkeypatch.setattr(cad_v2_service, "get_cad_v2_service", lambda: calls.append(True) or singleton)
    toolkit = CADV2Toolkit("alice")
    assert calls == []
    result = await toolkit.cad_v2_job_status("job")
    assert calls == [True]
    assert result.data["status"] == "running"
