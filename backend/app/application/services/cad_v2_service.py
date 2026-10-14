"""CAD V2 application orchestration; no geometry kernel or model SDK imports here."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from app.infrastructure.cad_v2.store import CADStore, StoreConflict, StoreNotFound, TERMINAL_STATUSES, dumps, utcnow

logger = logging.getLogger(__name__)


def ir_dict(ir: Any) -> dict:
    return ir.model_dump(mode="json") if hasattr(ir, "model_dump") else ir


def revision_diff(before: dict | None, after: dict) -> dict:
    previous = before or {}
    old_params, new_params = previous.get("parameters", {}), after.get("parameters", {})
    parameters = {name: {"before": old_params.get(name), "after": new_params.get(name)}
                  for name in sorted(set(old_params) | set(new_params)) if old_params.get(name) != new_params.get(name)}
    old_features = {f["id"]: f for f in previous.get("features", [])}
    new_features = {f["id"]: f for f in after.get("features", [])}
    features = {name: {"before": old_features.get(name), "after": new_features.get(name)}
                for name in sorted(set(old_features) | set(new_features)) if old_features.get(name) != new_features.get(name)}
    result = {"parameters": parameters, "features": features}
    for key in ("output_feature", "expected_geometry", "constraints"):
        if previous.get(key) != after.get(key):
            result[key] = {"before": previous.get(key), "after": after.get(key)}
    return result


class CADV2Service:
    def __init__(self, data_dir: str | Path, *, executor=None, planner=None, edit_planner=None,
                 repair_policy=None, max_concurrency: int = 2, max_queue: int = 32, max_repairs: int = 3):
        if not 1 <= max_concurrency <= 16 or not 1 <= max_queue <= 1000 or not 0 <= max_repairs <= 5:
            raise ValueError("Invalid CAD V2 concurrency, queue, or repair budget")
        self.store = CADStore(data_dir)
        self._executor = executor
        self._planner = planner
        self._edit_planner = edit_planner
        self._repair_policy = repair_policy
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._start_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._max_queue = max_queue
        self._max_repairs = max_repairs
        self._started = False
        self._closing = False

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            if self._closing:
                raise RuntimeError("CAD V2 supervisor is shutting down")
            self.store.acquire_supervisor_lock()
            try:
                committed = self.store.committed_artifact_directories()
                artifact_root = self.store.data_dir / "artifacts"
                if artifact_root.is_dir() and not artifact_root.is_symlink():
                    for document_dir in artifact_root.iterdir():
                        if document_dir.is_dir() and not document_dir.is_symlink():
                            for revision_dir in document_dir.iterdir():
                                if revision_dir.is_dir() and not revision_dir.is_symlink() and revision_dir.resolve() not in committed:
                                    shutil.rmtree(revision_dir)
                pending = self.store.incomplete_jobs()
                for _owner, job, _payload in pending:
                    shutil.rmtree(self.store.data_dir / "jobs" / job["id"], ignore_errors=True)
                    if job["status"] in {"created", "queued"}:
                        self.store.update_job(job["id"], status="queued", stage="queued", progress=0)
                        self._schedule(job["id"])
                    else:
                        self.store.update_job(job["id"], status="failed", stage="interrupted", error={
                            "type": "InfrastructureError", "message": "Supervisor stopped before this job completed; the last valid revision is unchanged"})
                self._started = True
            except BaseException:
                self.store.release_supervisor_lock()
                raise

    async def shutdown(self) -> None:
        self._closing = True
        tasks = list(self._tasks.values())
        for job_id in self._tasks:
            self.store.update_job(job_id, status="cancelled", stage="cancelled", error={
                "code": "ExecutionCancelledError", "type": "ExecutionCancelledError",
                "message": "Supervisor shutdown cancelled this job; the last valid revision is unchanged"})
        for event in self._cancel_events.values():
            event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.store.release_supervisor_lock()
        self._started = False

    def _schedule(self, job_id: str) -> None:
        self._cancel_events[job_id] = asyncio.Event()
        task = asyncio.create_task(self._run_job(job_id), name=f"cad-v2-{job_id}")
        self._tasks[job_id] = task
        def finish(completed: asyncio.Task) -> None:
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            if not completed.cancelled():
                exc = completed.exception()
                if exc:
                    logger.error("CAD supervisor task failed job=%s error_type=%s", job_id, type(exc).__name__)
        task.add_done_callback(finish)

    async def _ready(self) -> None:
        await self.start()
        if self._closing:
            raise StoreConflict("CAD supervisor is shutting down")

    def _admit_job(self, owner: str, document_id: str, payload: dict, **kwargs) -> tuple[dict, bool]:
        replay = self.store.replay_request(owner, document_id, payload, **kwargs)
        if replay is not None:
            return replay, False
        if len(self._tasks) >= self._max_queue:
            raise StoreConflict("CAD task queue is full; retry after a running job finishes")
        return self.store.create_job(owner, document_id, payload, **kwargs)

    async def create(self, owner: str, *, prompt: str | None = None, ir: dict | None = None,
                     name: str | None = None, request_id: str | None = None) -> dict:
        if bool(prompt and prompt.strip()) == (ir is not None):
            raise ValueError("Provide exactly one nonempty prompt or CAD IR")
        if ir is not None and len(dumps(ir)) > 2_000_000:
            raise ValueError("CAD IR exceeds the 2 MB input limit")
        await self._ready()
        payload = {"kind": "create", "prompt": prompt, "ir": ir}
        job, created = self._admit_job(owner, str(uuid.uuid4()), payload, name=name, request_id=request_id)
        if created:
            job = self.store.update_job(job["id"], status="queued", stage="queued")
            self._schedule(job["id"])
        return job

    async def edit(self, owner: str, document_id: str, *, expected_revision: str,
                   parameters: dict | None = None, feature_patches: list[dict] | None = None,
                   add_features: list[dict] | None = None, delete_features: list[str] | None = None,
                   output_feature: str | None = None, expected_geometry: dict | None = None,
                   constraints: list[dict] | None = None, prompt: str | None = None, request_id: str | None = None) -> dict:
        await self._ready()
        self.store.get_revision(document_id, expected_revision, owner)
        if prompt and (any((parameters, feature_patches, add_features, delete_features, output_feature, expected_geometry)) or constraints is not None):
            raise ValueError("Use either a natural-language edit or explicit patches")
        if not any((parameters, feature_patches, add_features, delete_features, output_feature, expected_geometry, prompt and prompt.strip())) and constraints is None:
            raise ValueError("Edit contains no changes")
        payload = {"kind": "edit", "parameters": parameters or {}, "feature_patches": feature_patches or [],
                   "add_features": add_features or [], "delete_features": delete_features or [], "output_feature": output_feature,
                   "expected_geometry": expected_geometry, "constraints": constraints, "prompt": prompt}
        if len(dumps(payload)) > 2_000_000:
            raise ValueError("CAD edit exceeds the 2 MB input limit")
        job, created = self._admit_job(owner, document_id, payload, base_revision=expected_revision, request_id=request_id)
        if created:
            job = self.store.update_job(job["id"], status="queued", stage="queued")
            self._schedule(job["id"])
        return job

    async def restore(self, owner: str, document_id: str, *, revision_id: str, expected_revision: str,
                      request_id: str | None = None) -> dict:
        await self._ready()
        self.store.get_revision(document_id, revision_id, owner)
        payload = {"kind": "restore", "revision_id": revision_id}
        job, created = self._admit_job(owner, document_id, payload, base_revision=expected_revision, request_id=request_id)
        if created:
            job = self.store.update_job(job["id"], status="queued", stage="queued")
            self._schedule(job["id"])
        return job

    async def cancel(self, owner: str, job_id: str) -> dict:
        job = self.store.get_job(job_id, owner)
        if job["status"] in TERMINAL_STATUSES:
            return job
        job = self.store.update_job(job_id, status="cancelled", stage="cancelled", error={
            "type": "ExecutionCancelledError", "message": "Cancelled by the user; the last valid revision is unchanged"})
        if event := self._cancel_events.get(job_id):
            event.set()
        if task := self._tasks.get(job_id):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return job

    async def wait(self, job_id: str) -> None:
        """Test/CLI helper; HTTP callers use job status and durable SSE events."""
        if task := self._tasks.get(job_id):
            await asyncio.shield(task)

    async def _prepare_ir(self, owner: str, job: dict, payload: dict):
        from app.domain.cad_v2.models import CADDocumentIR
        from app.domain.cad_v2.compiler import compile_ir
        before = None
        if job["base_revision"]:
            before = self.store.get_revision(job["document_id"], job["base_revision"], owner)["ir"]
        if payload["kind"] == "create":
            if payload.get("ir") is not None:
                raw = dict(payload["ir"])
                raw["document_id"] = job["document_id"]
                ir = CADDocumentIR.model_validate(raw)
            else:
                planner = self._planner
                if planner is None:
                    from app.application.services.cad_v2_planner import plan
                    planner = plan
                self.store.update_job(job["id"], stage="parsing", progress=5)
                ir = await planner(payload["prompt"], job["document_id"])
        elif payload["kind"] == "restore":
            ir = CADDocumentIR.model_validate(self.store.get_revision(job["document_id"], payload["revision_id"], owner)["ir"])
        else:
            from app.domain.cad_v2.compiler import apply_edits
            parameters = payload.get("parameters", {})
            if payload.get("prompt"):
                planner = self._edit_planner
                if planner is None:
                    from app.application.services.cad_v2_planner import edit_prompt
                    planner = edit_prompt
                self.store.update_job(job["id"], stage="parsing", progress=5)
                parameters = await planner(payload["prompt"], CADDocumentIR.model_validate(before))
            patches = payload.get("feature_patches", [])
            if len({p["id"] for p in patches}) != len(patches):
                raise ValueError("Repeated feature patch IDs are ambiguous")
            ir = apply_edits(CADDocumentIR.model_validate(before), parameters=parameters,
                             feature_patches={p["id"]: {key: value for key, value in p.items() if key != "id" and value is not None} for p in patches},
                             add_features=payload.get("add_features", []), delete_features=payload.get("delete_features", []),
                             output_feature=payload.get("output_feature"), expected_geometry=payload.get("expected_geometry"),
                             constraints=payload.get("constraints"))
        ir = CADDocumentIR.model_validate(ir_dict(ir))
        if ir.document_id != job["document_id"]:
            raise ValueError("Planner returned an IR for a different document")
        compile_ir(ir)
        return ir, before

    @staticmethod
    def _error(exc: Exception) -> dict:
        if hasattr(exc, "to_dict"):
            error = exc.to_dict()
            return {**error, "type": error.get("code", type(exc).__name__)}
        if isinstance(exc, StoreConflict):
            return {"type": "RevisionConflictError", "message": str(exc), "http_status": 409}
        if isinstance(exc, (ValueError, TypeError, KeyError)):
            return {"type": "SchemaValidationError", "message": str(exc)[:3000]}
        return {"type": "InfrastructureError", "message": str(exc)[:1500] or type(exc).__name__}

    async def _execute(self, ir, output_dir: Path, cancel_event: asyncio.Event, on_stage: Callable):
        if self._executor is None:
            from app.infrastructure.cad_v2.executor import CADExecutor
            self._executor = CADExecutor(timeout_seconds=float(os.environ.get("CAD_V2_TIMEOUT_SECONDS", "90")))
        kwargs = {"cancel_event": cancel_event}
        if "on_stage" in inspect.signature(self._executor.execute).parameters:
            kwargs["on_stage"] = on_stage
        return await self._executor.execute(ir, output_dir, **kwargs)

    def _materialize(self, job: dict, ir: dict, report: dict, output_dir: Path) -> tuple[Path, dict]:
        from app.domain.cad_v2.errors import ExportError
        revision_id = str(uuid.uuid4())
        target = self.store.data_dir / "artifacts" / job["document_id"] / revision_id
        target.mkdir(parents=True, mode=0o700)
        artifacts = {}
        try:
            items = list(report.get("artifacts", []))
            ir_path = output_dir / "model.cad-ir.json"
            ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            items = [a for a in items if a["format"] != "ir"] + [{"format": "ir", "filename": ir_path.name}]
            for artifact in items:
                format = {"py": "python", "json": "ir"}.get(artifact["format"], artifact["format"])
                if format not in {"step", "stl", "glb", "ir", "python"} or format in artifacts:
                    raise ExportError("Executor produced an unsupported or duplicate artifact format")
                raw_source = output_dir / artifact["filename"]
                source = raw_source.resolve()
                if not source.is_relative_to(output_dir.resolve()) or not source.is_file() or raw_source.is_symlink():
                    raise ExportError("Executor artifact path is outside the isolated job directory")
                destination = target / source.name
                if destination.exists():
                    raise ExportError("Executor produced duplicate artifact filenames")
                shutil.copyfile(source, destination)
                digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                if artifact.get("sha256") and artifact["sha256"] != digest:
                    raise ExportError("Executor artifact hash mismatch")
                size = destination.stat().st_size
                if not size:
                    raise ExportError("Executor produced an empty artifact")
                with destination.open("rb") as written:
                    os.fsync(written.fileno())
                destination.chmod(0o400)
                artifacts[format] = {"filename": destination.name, "sha256": digest, "size": size}
            if not {"step", "stl", "glb", "ir", "python"}.issubset(artifacts):
                raise ExportError("Executor did not produce all required revision artifacts")
            directory_fd = os.open(target, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return target, artifacts
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise

    async def _run_job(self, job_id: str) -> None:
        root_dir = self.store.data_dir / "jobs" / job_id
        published_dir = None
        try:
            async with self._semaphore:
                owner, job, payload = self.store.job_private(job_id)
                if job["status"] in TERMINAL_STATUSES:
                    return
                self.store.update_job(job_id, status="running", stage="planning", progress=5)
                ir, before = await self._prepare_ir(owner, job, payload)
                history = []
                report = None
                for attempt in range(self._max_repairs + 1):
                    if self._cancel_events[job_id].is_set():
                        raise asyncio.CancelledError
                    output_dir = root_dir / f"attempt-{attempt}"
                    output_dir.mkdir(parents=True, mode=0o700)
                    self.store.update_job(job_id, status="running", stage="building", progress=25, repair_history=history)
                    async def on_stage(stage: str) -> None:
                        states = {"building": ("running", 30), "validating": ("validating", 70), "exporting": ("validating", 85)}
                        if stage in states:
                            status, progress = states[stage]
                            self.store.update_job(job_id, status=status, stage=stage, progress=progress)
                    report = await self._execute(ir, output_dir, self._cancel_events[job_id], on_stage)
                    if report.get("status") == "cancelled":
                        raise asyncio.CancelledError
                    passed = report.get("status") == "completed" and report.get("validation", {}).get("status") == "passed"
                    if passed:
                        self.store.update_job(job_id, status="validating", stage="publishing", progress=95, report=report)
                        published_dir, artifacts = self._materialize(job, ir_dict(ir), report, output_dir)
                        report = {**report, "artifacts": [{"format": fmt, **value} for fmt, value in artifacts.items()]}
                        revision = {"id": published_dir.name, "document_id": job["document_id"], "parent_revision": job["base_revision"],
                                    "created_at": utcnow(), "ir": ir_dict(ir), "report": report, "artifacts": artifacts,
                                    "diff": revision_diff(before, ir_dict(ir))}
                        self.store.publish(job_id, owner, revision, published_dir)
                        published_dir = None
                        return
                    error = report.get("error") or {"type": "ConstraintViolationError", "message": "Geometry validation did not pass; no revision was published"}
                    error = {**error, "type": error.get("type") or error.get("code"), "code": error.get("code") or error.get("type")}
                    repair = None
                    if attempt < self._max_repairs:
                        repair_policy = self._repair_policy
                        if repair_policy is None:
                            from app.application.services.cad_v2_repair import next_repair
                            repair_policy = next_repair
                        repair = repair_policy(ir, error, history, max_attempts=self._max_repairs)
                    if repair is None:
                        self.store.update_job(job_id, status="failed", stage="failed", error=error, report=report, repair_history=history)
                        return
                    history.append({"attempt": attempt + 1, "error": error, "patch": repair["patch"],
                                    "reason": repair["reason"], "fingerprint": repair["fingerprint"], "created_at": utcnow()})
                    self.store.update_job(job_id, status="repairing", stage="repairing", progress=20, error=error, report=report, repair_history=history)
                    from app.domain.cad_v2.compiler import compile_ir
                    ir = repair["ir"]
                    compile_ir(ir)
        except asyncio.CancelledError:
            self.store.update_job(job_id, status="cancelled", stage="cancelled", error={
                "type": "ExecutionCancelledError", "message": "Job execution was cancelled; the last valid revision is unchanged"})
        except Exception as exc:
            logger.warning("CAD job failed job=%s error_type=%s", job_id, type(exc).__name__)
            self.store.update_job(job_id, status="failed", stage="failed", error=self._error(exc))
        finally:
            shutil.rmtree(root_dir, ignore_errors=True)
            if published_dir is not None:
                shutil.rmtree(published_dir, ignore_errors=True)


_service: CADV2Service | None = None


def get_cad_v2_service() -> CADV2Service:
    global _service
    if _service is None:
        default = Path(__file__).resolve().parents[3] / ".cad-v2-data"
        _service = CADV2Service(os.environ.get("CAD_V2_DATA_DIR", str(default)),
                               max_concurrency=int(os.environ.get("CAD_V2_MAX_CONCURRENCY", "2")),
                               max_queue=int(os.environ.get("CAD_V2_MAX_QUEUE", "32")),
                               max_repairs=int(os.environ.get("CAD_V2_MAX_REPAIRS", "3")))
    return _service
