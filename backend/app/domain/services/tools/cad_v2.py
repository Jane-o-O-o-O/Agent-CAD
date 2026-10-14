"""Owner-bound AgentScope bridge to the same durable CAD service used by HTTP."""
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote, urlencode

from app.domain.cad_v2.errors import CADError
from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit, tool
from app.infrastructure.cad_v2.store import StoreConflict, StoreNotFound


class CADV2Toolkit(BaseToolkit):
    """Precise 3D jobs; service construction stays lazy until a tool is called."""

    name = "cad_v2"

    def __init__(self, user_id: str, service_factory: Optional[Callable[[], Any]] = None):
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("CAD tools require an authenticated owner")
        self._owner = user_id
        self._service_factory = service_factory
        super().__init__()

    def _service(self):
        if self._service_factory is not None:
            return self._service_factory()
        from app.application.services.cad_v2_service import get_cad_v2_service
        return get_cad_v2_service()

    @staticmethod
    def _failure(error: Exception) -> ToolResult:
        if isinstance(error, CADError):
            payload = error.to_dict()
        elif isinstance(error, StoreNotFound):
            payload = {"code": "NotFound", "message": "CAD resource was not found for the current user"}
        elif isinstance(error, StoreConflict):
            payload = {"code": "RevisionConflictError", "message": str(error)}
        else:
            payload = {"code": "SchemaValidationError", "message": str(error)}
        return ToolResult(success=False, message=payload["message"], data={"error": payload})

    @staticmethod
    def _job_result(job: dict, *, submitted: bool = False) -> ToolResult:
        document_id = job["document_id"]
        query = {"document": document_id}
        revision_id = job.get("revision_id")
        if revision_id:
            query["revision"] = revision_id
        job_id = quote(job["id"], safe="")
        status = job["status"]
        links = {
            "workbench": "/chat/cad-v2?" + urlencode(query),
            "job": f"/api/v1/cad-v2/jobs/{job_id}",
            "events": f"/api/v1/cad-v2/jobs/{job_id}/events",
        }
        if revision_id:
            links["revision"] = (
                f"/api/v1/cad-v2/documents/{quote(document_id, safe='')}/revisions/{quote(revision_id, safe='')}"
            )
        message = (
            "CAD job submitted. Geometry is not ready; inspect job status before reporting completion."
            if submitted else f"CAD job status: {status}."
        )
        return ToolResult(
            success=status not in {"failed", "cancelled"}, message=message,
            data={"job": job, "job_id": job["id"], "document_id": document_id,
                  "revision_id": revision_id, "status": status, "links": links},
        )

    @tool(parse_docstring=True)
    async def cad_v2_generate(
        self, prompt: Optional[str] = None, ir: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Submit a real 3D CAD job using exactly one complete prompt or typed CAD IR 2.0.

        This creates an asynchronous job, not a completed model. Use cad_v2_job_status
        to inspect validation and cad_v2_revision for revision-bound STEP/STL/GLB downloads.
        Never send Python, shell commands, or secrets as IR. Preserve user dimensions.

        Args:
            prompt: Complete 3D mechanical modeling request, including units and dimensions.
            ir: Optional CAD IR 2.0 object; omit prompt when supplying explicit IR.
        """
        try:
            job = await self._service().create(self._owner, prompt=prompt, ir=ir)
            return self._job_result(job, submitted=True)
        except (CADError, StoreConflict, StoreNotFound, ValueError) as exc:
            return self._failure(exc)

    @tool(parse_docstring=True)
    async def cad_v2_job_status(self, job_id: str) -> ToolResult:
        """Inspect a submitted CAD job, actual geometry validation, repair and failure state.

        A queued/running/validating/repairing job is not complete. Report generation
        success only for completed jobs with a published revision and passed validation.
        Use the workbench link while a long job runs instead of repeatedly submitting it.

        Args:
            job_id: Stable job ID returned by cad_v2_generate or cad_v2_edit.
        """
        try:
            job = self._service().store.get_job(job_id, self._owner)
            return self._job_result(job)
        except (CADError, StoreConflict, StoreNotFound, ValueError) as exc:
            return self._failure(exc)

    @tool(parse_docstring=True)
    async def cad_v2_edit(
        self, document_id: str, expected_revision: str,
        parameters: Optional[Dict[str, float]] = None, prompt: Optional[str] = None,
    ) -> ToolResult:
        """Submit an atomic parameter or natural-language edit of an existing 3D revision.

        Supply exactly one parameters mapping or edit prompt. The last valid revision
        stays available if rebuilding fails. Read job status and the new revision
        before claiming success; do not substitute browser mesh scaling for a rebuild.

        Args:
            document_id: Existing owner-accessible CAD document ID.
            expected_revision: Exact current revision ID, preventing overwriting a newer edit.
            parameters: Editable parameter names and new numeric values in document units.
            prompt: Optional natural-language edit; omit parameters when using this.
        """
        try:
            job = await self._service().edit(
                self._owner, document_id, expected_revision=expected_revision,
                parameters=parameters, prompt=prompt,
            )
            return self._job_result(job, submitted=True)
        except (CADError, StoreConflict, StoreNotFound, ValueError) as exc:
            return self._failure(exc)

    @tool(parse_docstring=True)
    async def cad_v2_revision(self, document_id: str, revision_id: str) -> ToolResult:
        """Read one immutable CAD revision, its exact IR, measured validation and artifact links.

        All download links and the workbench preview refer to this same revision.
        STEP is the precise engineering solid; STL and GLB are derived meshes.

        Args:
            document_id: Owner-accessible CAD document ID.
            revision_id: Published revision ID returned by a completed CAD job.
        """
        try:
            revision = self._service().store.get_revision(document_id, revision_id, self._owner)
            prefix = (
                f"/api/v1/cad-v2/documents/{quote(document_id, safe='')}/revisions/{quote(revision_id, safe='')}"
            )
            downloads = {format: f"{prefix}/artifacts/{format}" for format in revision.get("artifacts", {})
                         if format in {"step", "stl", "glb", "ir", "python"}}
            return ToolResult(success=True, message="Loaded immutable CAD revision and its measured report.", data={
                "revision": revision, "downloads": downloads,
                "workbench": "/chat/cad-v2?" + urlencode({"document": document_id, "revision": revision_id}),
            })
        except (CADError, StoreConflict, StoreNotFound, ValueError) as exc:
            return self._failure(exc)
