"""Durable, owner-scoped CAD metadata with immutable revisions and atomic publication.

SQLite lives on the same persistent filesystem as immutable artifacts. The service
holds a process lock for its lifetime: this implementation supports one supervisor
on one host, not multiple independent API replicas or an NFS database.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "awaiting_input"})


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


class StoreConflict(Exception):
    pass


class StoreNotFound(Exception):
    pass


class CADStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database_path = self.data_dir / "cad.sqlite3"
        self._lock_file = None
        with self._connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                    current_revision TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS documents_owner ON documents(owner, updated_at);
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    owner TEXT NOT NULL, parent_revision TEXT, created_at TEXT NOT NULL,
                    ir_json TEXT NOT NULL, report_json TEXT NOT NULL, artifacts_json TEXT NOT NULL,
                    diff_json TEXT NOT NULL, artifact_dir TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS revisions_document ON revisions(document_id, created_at);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, document_id TEXT NOT NULL,
                    status TEXT NOT NULL, snapshot_json TEXT NOT NULL, payload_json TEXT NOT NULL,
                    request_scope TEXT NOT NULL, request_id TEXT, request_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(owner, request_scope, request_id)
                );
                CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, document_id);
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
                    snapshot_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON job_events(job_id,id);
            """)

    @contextmanager
    def _connection(self, transaction: bool = False):
        db = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if transaction:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if transaction:
                db.execute("COMMIT")
        except BaseException:
            if transaction and db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def acquire_supervisor_lock(self) -> None:
        if self._lock_file is not None:
            return
        lock = (self.data_dir / "supervisor.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock.close()
            raise RuntimeError("CAD V2 data directory already has an active supervisor; use exactly one API worker") from exc
        self._lock_file = lock

    def release_supervisor_lock(self) -> None:
        if self._lock_file is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self._lock_file.seek(0)
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None

    @staticmethod
    def _document(row: sqlite3.Row) -> dict:
        return {key: row[key] for key in ("id", "name", "current_revision", "created_at", "updated_at")}

    @staticmethod
    def _revision(row: sqlite3.Row) -> dict:
        result = {key: row[key] for key in ("id", "document_id", "parent_revision", "created_at")}
        for key in ("ir", "report", "artifacts", "diff"):
            result[key] = json.loads(row[f"{key}_json"])
        return result

    def get_document(self, document_id: str, owner: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM documents WHERE id=? AND owner=?", (document_id, owner)).fetchone()
        if row is None:
            raise StoreNotFound("CAD document not found")
        return self._document(row)

    def list_documents(self, owner: str) -> list[dict]:
        with self._connection() as db:
            rows = db.execute("SELECT * FROM documents WHERE owner=? ORDER BY updated_at DESC", (owner,)).fetchall()
        return [self._document(row) for row in rows]

    def get_revision(self, document_id: str, revision_id: str, owner: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT * FROM revisions WHERE id=? AND document_id=? AND owner=?", (revision_id, document_id, owner)).fetchone()
        if row is None:
            raise StoreNotFound("CAD revision not found")
        return self._revision(row)

    def list_revisions(self, document_id: str, owner: str) -> list[dict]:
        self.get_document(document_id, owner)
        with self._connection() as db:
            rows = db.execute("SELECT * FROM revisions WHERE document_id=? AND owner=? ORDER BY created_at", (document_id, owner)).fetchall()
        return [self._revision(row) for row in rows]

    def artifact_location(self, document_id: str, revision_id: str, owner: str, format: str) -> tuple[Path, dict]:
        with self._connection() as db:
            row = db.execute("SELECT * FROM revisions WHERE id=? AND document_id=? AND owner=?", (revision_id, document_id, owner)).fetchone()
        if row is None:
            raise StoreNotFound("CAD revision not found")
        artifact = json.loads(row["artifacts_json"]).get(format)
        if artifact is None:
            raise StoreNotFound("CAD artifact not found")
        root = Path(row["artifact_dir"]).resolve()
        path = (root / artifact["filename"]).resolve()
        if not root.is_relative_to(self.data_dir) or not path.is_relative_to(root) or not path.is_file():
            raise StoreNotFound("CAD artifact is unavailable")
        return path, artifact

    def create_job(self, owner: str, document_id: str, payload: dict, name: str | None = None,
                   base_revision: str | None = None, request_id: str | None = None) -> tuple[dict, bool]:
        now = utcnow()
        scope = "create" if payload["kind"] == "create" else f"{payload['kind']}:{document_id}"
        # Randomly allocated document IDs must not affect create request deduplication.
        fingerprint = hashlib.sha256(dumps({"payload": payload, "name": name, "base_revision": base_revision}).encode()).hexdigest()
        with self._connection(transaction=True) as db:
            if request_id:
                existing = db.execute("SELECT snapshot_json,request_hash FROM jobs WHERE owner=? AND request_scope=? AND request_id=?", (owner, scope, request_id)).fetchone()
                if existing:
                    if existing["request_hash"] != fingerprint:
                        raise StoreConflict("request_id was already used with a different request")
                    return json.loads(existing["snapshot_json"]), False
            clarification_id = payload.get("clarification_job_id")
            if clarification_id:
                parent = db.execute("SELECT document_id,status,snapshot_json,payload_json FROM jobs WHERE id=? AND owner=?",
                                    (clarification_id, owner)).fetchone()
                if parent is None:
                    raise StoreNotFound("CAD clarification job not found")
                previous = json.loads(parent["snapshot_json"])
                if (parent["document_id"] != document_id or previous["base_revision"] != base_revision
                        or parent["status"] not in {"awaiting_input", "failed"}
                        or (previous.get("error") or {}).get("code") != "RequirementError"
                        or json.loads(parent["payload_json"])["kind"] != payload["kind"]):
                    raise StoreConflict("Clarification no longer matches this request")
                children = db.execute("SELECT payload_json FROM jobs WHERE document_id=? AND owner=?", (document_id, owner)).fetchall()
                if any(json.loads(child["payload_json"]).get("clarification_job_id") == clarification_id for child in children):
                    raise StoreConflict("This question already has an answer; continue from the latest job")
            if payload["kind"] == "create" and not clarification_id:
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", (document_id, owner, name or "CAD part", None, now, now))
            else:
                row = db.execute("SELECT current_revision FROM documents WHERE id=? AND owner=?", (document_id, owner)).fetchone()
                if row is None:
                    raise StoreNotFound("CAD document not found")
                if row["current_revision"] != base_revision:
                    raise StoreConflict("Document changed; reload the current revision before editing")
            job = {"id": str(uuid.uuid4()), "document_id": document_id, "status": "created", "stage": "created", "progress": 0,
                   "base_revision": base_revision, "revision_id": None, "created_at": now, "updated_at": now,
                   "error": None, "report": None, "repair_history": []}
            encoded = dumps(job)
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)", (job["id"], owner, document_id, "created", encoded, dumps(payload), scope, request_id or None, fingerprint, now, now))
            db.execute("INSERT INTO job_events(job_id,snapshot_json) VALUES(?,?)", (job["id"], encoded))
        return job, True

    def replay_request(self, owner: str, document_id: str, payload: dict, name: str | None = None,
                       base_revision: str | None = None, request_id: str | None = None) -> dict | None:
        if not request_id:
            return None
        scope = "create" if payload["kind"] == "create" else f"{payload['kind']}:{document_id}"
        fingerprint = hashlib.sha256(dumps({"payload": payload, "name": name, "base_revision": base_revision}).encode()).hexdigest()
        with self._connection() as db:
            row = db.execute("SELECT snapshot_json,request_hash FROM jobs WHERE owner=? AND request_scope=? AND request_id=?", (owner, scope, request_id)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != fingerprint:
            raise StoreConflict("request_id was already used with a different request")
        return json.loads(row["snapshot_json"])

    def get_job(self, job_id: str, owner: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT snapshot_json FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if row is None:
            raise StoreNotFound("CAD job not found")
        return json.loads(row["snapshot_json"])

    def job_private(self, job_id: str) -> tuple[str, dict, dict]:
        with self._connection() as db:
            row = db.execute("SELECT owner,snapshot_json,payload_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise StoreNotFound("CAD job not found")
        return row["owner"], json.loads(row["snapshot_json"]), json.loads(row["payload_json"])

    @staticmethod
    def _write_job(db, job: dict) -> None:
        job["updated_at"] = utcnow()
        encoded = dumps(job)
        db.execute("UPDATE jobs SET status=?,snapshot_json=?,updated_at=? WHERE id=?", (job["status"], encoded, job["updated_at"], job["id"]))
        db.execute("INSERT INTO job_events(job_id,snapshot_json) VALUES(?,?)", (job["id"], encoded))

    def update_job(self, job_id: str, **changes) -> dict:
        with self._connection(transaction=True) as db:
            row = db.execute("SELECT snapshot_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreNotFound("CAD job not found")
            job = json.loads(row["snapshot_json"])
            if job["status"] in TERMINAL_STATUSES:
                return job
            if changes.get("error"):
                error = changes["error"]
                code = error.get("code") or error.get("type") or "InfrastructureError"
                changes["error"] = {**error, "code": code, "type": code}
            job.update(changes)
            self._write_job(db, job)
        return job

    def events(self, job_id: str, owner: str, after: int = 0) -> list[tuple[int, dict]]:
        self.get_job(job_id, owner)
        with self._connection() as db:
            rows = db.execute("SELECT id,snapshot_json FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT 100", (job_id, after)).fetchall()
        return [(row["id"], json.loads(row["snapshot_json"])) for row in rows]

    def incomplete_jobs(self) -> list[tuple[str, dict, dict]]:
        with self._connection() as db:
            rows = db.execute("SELECT owner,snapshot_json,payload_json FROM jobs WHERE status NOT IN ('completed','failed','cancelled','awaiting_input') ORDER BY created_at").fetchall()
        return [(r["owner"], json.loads(r["snapshot_json"]), json.loads(r["payload_json"])) for r in rows]

    def committed_artifact_directories(self) -> set[Path]:
        with self._connection() as db:
            rows = db.execute("SELECT artifact_dir FROM revisions").fetchall()
        return {Path(row["artifact_dir"]).resolve() for row in rows}

    def publish(self, job_id: str, owner: str, revision: dict, artifact_dir: Path) -> dict:
        """Publish only if job is active and the document still has its base revision."""
        with self._connection(transaction=True) as db:
            row = db.execute("SELECT snapshot_json FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
            if row is None:
                raise StoreNotFound("CAD job not found")
            job = json.loads(row["snapshot_json"])
            if job["status"] in TERMINAL_STATUSES:
                raise StoreConflict("Job is already terminal; publication was rejected")
            doc = db.execute("SELECT current_revision FROM documents WHERE id=? AND owner=?", (job["document_id"], owner)).fetchone()
            if doc is None or doc["current_revision"] != job["base_revision"]:
                raise StoreConflict("Document changed while this model was building; result was not published")
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?,?)", (
                revision["id"], job["document_id"], owner, job["base_revision"], revision["created_at"],
                dumps(revision["ir"]), dumps(revision["report"]), dumps(revision["artifacts"]), dumps(revision["diff"]), str(artifact_dir)))
            db.execute("UPDATE documents SET current_revision=?,updated_at=? WHERE id=? AND owner=?", (revision["id"], revision["created_at"], job["document_id"], owner))
            job.update(status="completed", stage="completed", progress=100, revision_id=revision["id"], report=revision["report"], error=None)
            self._write_job(db, job)
        return job
