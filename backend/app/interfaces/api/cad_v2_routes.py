"""Additive CAD V2 API, shared by the production and authenticated local app."""
import asyncio
import json
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, model_validator

from app.application.services.cad_v2_service import CADV2Service
from app.infrastructure.cad_v2.store import StoreConflict, StoreNotFound, TERMINAL_STATUSES


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CreateJob(InputModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=16000)
    ir: dict[str, Any] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.prompt and self.prompt.strip()) == (self.ir is not None):
            raise ValueError("Provide exactly one nonempty prompt or IR")
        return self


class FeaturePatch(InputModel):
    id: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] | None = Field(default=None, max_length=500)
    depends_on: list[str] | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=200)


class EditJob(InputModel):
    expected_revision: str = Field(min_length=1, max_length=128)
    parameters: dict[str, StrictFloat] | None = None
    feature_patches: list[FeaturePatch] | None = Field(default=None, max_length=200)
    add_features: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    delete_features: list[str] | None = Field(default=None, max_length=200)
    output_feature: str | None = Field(default=None, min_length=1, max_length=64)
    expected_geometry: dict[str, Any] | None = None
    constraints: list[dict[str, Any]] | None = Field(default=None, max_length=1000)
    prompt: str | None = Field(default=None, min_length=1, max_length=16000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    instance_patches: dict[str, dict[str, Any]] | None = None
    add_instances: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    delete_instances: list[str] | None = Field(default=None, max_length=200)
    mate_patches: dict[str, dict[str, Any]] | None = None
    clarification_job_id: str | None = Field(default=None, min_length=1, max_length=128)


class RestoreJob(InputModel):
    revision_id: str = Field(min_length=1, max_length=128)
    expected_revision: str = Field(min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


def success(data: Any) -> dict:
    return {"code": 0, "msg": "success", "data": data}


class CADRequestSizeLimit:
    """Bound CAD JSON before parsing; leave existing multipart file APIs unchanged."""
    def __init__(self, app, max_bytes: int = 2_100_000):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/v1/cad-v2"):
            await self.app(scope, receive, send)
            return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                body = b'{"code":413,"msg":"CAD request exceeds the input size limit","data":null}'
                await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
            chunks.append(message)
            if not message.get("more_body", False):
                break
        async def replay():
            if chunks:
                return chunks.pop(0)
            return await receive()
        await self.app(scope, replay, send)


def _owner(user) -> str:
    owner = user.id if hasattr(user, "id") else user["id"] if isinstance(user, dict) else str(user)
    if not owner:
        raise HTTPException(401, "Authentication required")
    return owner


def _translate(exc: Exception):
    if isinstance(exc, StoreNotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, StoreConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(422, str(exc)) from exc
    raise exc


def build_router(service_dep: Callable, user_dep: Callable) -> APIRouter:
    """Dependency injection avoids production auth/AgentScope imports in local mode."""
    router = APIRouter(prefix="/cad-v2", tags=["cad-v2"])
    Service = Annotated[CADV2Service, Depends(service_dep)]
    User = Annotated[Any, Depends(user_dep)]

    @router.post("/jobs", status_code=202)
    async def create_job(body: CreateJob, service: Service, user: User):
        try:
            return success(await service.create(_owner(user), **body.model_dump()))
        except (ValueError, StoreConflict, StoreNotFound) as exc:
            _translate(exc)

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, service: Service, user: User):
        try:
            return success(service.store.get_job(job_id, _owner(user)))
        except StoreNotFound as exc:
            _translate(exc)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, service: Service, user: User):
        try:
            return success(await service.cancel(_owner(user), job_id))
        except StoreNotFound as exc:
            _translate(exc)

    @router.get("/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, service: Service, user: User,
                         after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None)):
        owner = _owner(user)
        try:
            service.store.get_job(job_id, owner)
        except StoreNotFound as exc:
            _translate(exc)
        if last_event_id:
            try:
                after = max(after, int(last_event_id))
            except ValueError:
                raise HTTPException(400, "Last-Event-ID must be an integer")
        async def stream():
            cursor = after
            idle = 0
            while not await request.is_disconnected():
                events = service.store.events(job_id, owner, cursor)
                for event_id, job in events:
                    cursor = event_id
                    yield f"id: {event_id}\nevent: job\ndata: {json.dumps(job, ensure_ascii=False, allow_nan=False)}\n\n"
                    if job["status"] in TERMINAL_STATUSES:
                        return
                if not events and service.store.get_job(job_id, owner)["status"] in TERMINAL_STATUSES:
                    return
                idle += 1
                if idle >= 30:
                    yield ": keepalive\n\n"
                    idle = 0
                await asyncio.sleep(0.25)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/documents")
    async def list_documents(service: Service, user: User):
        return success(service.store.list_documents(_owner(user)))

    @router.get("/documents/{document_id}")
    async def get_document(document_id: str, service: Service, user: User):
        try:
            return success(service.store.get_document(document_id, _owner(user)))
        except StoreNotFound as exc:
            _translate(exc)

    @router.get("/documents/{document_id}/revisions")
    async def revisions(document_id: str, service: Service, user: User):
        try:
            return success(service.store.list_revisions(document_id, _owner(user)))
        except StoreNotFound as exc:
            _translate(exc)

    @router.get("/documents/{document_id}/revisions/{revision_id}")
    async def revision(document_id: str, revision_id: str, service: Service, user: User):
        try:
            return success(service.store.get_revision(document_id, revision_id, _owner(user)))
        except StoreNotFound as exc:
            _translate(exc)

    @router.post("/documents/{document_id}/edits", status_code=202)
    async def edit(document_id: str, body: EditJob, service: Service, user: User):
        try:
            return success(await service.edit(_owner(user), document_id, **body.model_dump()))
        except (ValueError, StoreConflict, StoreNotFound) as exc:
            _translate(exc)

    @router.post("/documents/{document_id}/restore", status_code=202)
    async def restore(document_id: str, body: RestoreJob, service: Service, user: User):
        try:
            return success(await service.restore(_owner(user), document_id, **body.model_dump()))
        except (ValueError, StoreConflict, StoreNotFound) as exc:
            _translate(exc)

    @router.get("/documents/{document_id}/revisions/{revision_id}/artifacts/{format}")
    async def artifact(document_id: str, revision_id: str, format: str, service: Service, user: User):
        try:
            path, item = service.store.artifact_location(document_id, revision_id, _owner(user), format)
        except StoreNotFound as exc:
            _translate(exc)
        media_types = {"step": "application/step", "stl": "model/stl", "glb": "model/gltf-binary", "ir": "application/json", "python": "text/x-python",
                       "dxf": "image/vnd.dxf", "svg": "image/svg+xml", "bom_json": "application/json", "bom_csv": "text/csv"}
        return FileResponse(path, media_type=media_types.get(format, "application/octet-stream"), filename=item["filename"],
                            headers={"ETag": f'"{item["sha256"]}"', "Cache-Control": "private, no-cache", "X-CAD-Revision": revision_id})
    return router
