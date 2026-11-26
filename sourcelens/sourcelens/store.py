"""SQLite repository: immutable versions, indexed evidence, atomic job checkpoints."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .documents import PARSER_VERSION, Chunk, DocumentError


class Conflict(ValueError):
    """An idempotency key or state transition conflicts with an existing record."""


class NotFound(LookupError):
    """The requested record does not exist."""


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, current_version TEXT);
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    digest TEXT NOT NULL, label TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT,
                    parser_version TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(document_id, digest, parser_version));
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id),
                    ordinal INTEGER NOT NULL, heading TEXT NOT NULL, body TEXT NOT NULL,
                    page INTEGER, line_start INTEGER, line_end INTEGER,
                    UNIQUE(version_id, ordinal));
                CREATE INDEX IF NOT EXISTS chunks_version ON chunks(version_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED, title, heading, body, tokenize='porter unicode61');
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    question TEXT NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, error TEXT);
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, seq);
            """
            )
            row = db.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if row and row[0] != "1":
                raise Conflict("Unsupported database schema version")
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version', '1')")

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def ingest(
        self,
        *,
        source_key: str,
        title: str,
        label: str,
        source_url: str | None,
        data: bytes,
        chunks: list[Chunk],
    ) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        with self.connection(write=True) as db:
            document = db.execute(
                "SELECT * FROM documents WHERE source_key=?", (source_key,)
            ).fetchone()
            if not document:
                count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                if count >= 100:
                    raise DocumentError(
                        "This local library supports up to 100 documents"
                    )
                doc_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO documents VALUES (?,?,?,NULL)",
                    (doc_id, source_key, title),
                )
            else:
                doc_id = document["id"]
                existing = db.execute(
                    "SELECT id FROM versions WHERE document_id=? AND digest=? AND parser_version=?",
                    (doc_id, digest, PARSER_VERSION),
                ).fetchone()
                if existing:
                    # Reimporting old content must not silently roll the library back.
                    return {
                        "document_id": doc_id,
                        "version_id": existing[0],
                        "created": False,
                        "current": existing[0] == document["current_version"],
                    }
            total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if total + len(chunks) > 4000:
                raise DocumentError(
                    "Library exceeds 4000 stored chunks, including version history"
                )
            version_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    doc_id,
                    digest,
                    label,
                    title,
                    source_url,
                    PARSER_VERSION,
                    time.time(),
                ),
            )
            for index, chunk in enumerate(chunks):
                key = hashlib.sha256(
                    f"{version_id}:{index}:{chunk.text}".encode()
                ).hexdigest()
                db.execute(
                    "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
                    (
                        key,
                        version_id,
                        index,
                        chunk.heading,
                        chunk.text,
                        chunk.page,
                        chunk.line_start,
                        chunk.line_end,
                    ),
                )
                db.execute(
                    "INSERT INTO chunks_fts VALUES (?,?,?,?)",
                    (key, title, chunk.heading, chunk.text),
                )
            db.execute(
                "UPDATE documents SET title=?, current_version=? WHERE id=?",
                (title, version_id, doc_id),
            )
        return {
            "document_id": doc_id,
            "version_id": version_id,
            "created": True,
            "current": True,
        }

    def documents(self):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                SELECT d.*, v.label, v.created_at, v.digest,
                    (SELECT COUNT(*) FROM chunks c WHERE c.version_id=v.id) chunk_count,
                    (SELECT COUNT(*) FROM versions x WHERE x.document_id=d.id) version_count
                FROM documents d JOIN versions v ON v.id=d.current_version ORDER BY lower(d.title)
            """
                )
            ]

    def versions(self, document_id):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                SELECT v.*, v.id=d.current_version is_current FROM versions v
                JOIN documents d ON v.document_id=d.id WHERE d.id=? ORDER BY v.created_at DESC
            """,
                    (document_id,),
                )
            ]

    def snapshot(self):
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT current_version FROM documents ORDER BY id"
                )
                if row[0]
            ]

    def chunks(self, snapshot):
        if not snapshot:
            return []
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                SELECT c.*, v.document_id, v.label, v.source_url, v.digest, v.title,
                    v.id=d.current_version is_current
                FROM chunks c JOIN versions v ON c.version_id=v.id
                JOIN documents d ON d.id=v.document_id
                WHERE c.version_id IN (%s) ORDER BY c.version_id, c.ordinal
            """
                    % ",".join("?" * len(snapshot)),
                    snapshot,
                )
            ]

    def chunk(self, chunk_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT version_id FROM chunks WHERE id=?", (chunk_id,)
            ).fetchone()
        if not row:
            raise NotFound("Evidence chunk not found")
        return next(c for c in self.chunks([row[0]]) if c["id"] == chunk_id)

    def fts(self, expression, snapshot, limit):
        if not snapshot or not expression:
            return []
        with self.connection() as db:
            return [
                row[0]
                for row in db.execute(
                    """
                SELECT f.chunk_id FROM chunks_fts f JOIN chunks c ON c.id=f.chunk_id
                WHERE chunks_fts MATCH ? AND c.version_id IN (%s)
                ORDER BY bm25(chunks_fts, 0, 1, 2, 1), f.chunk_id LIMIT ?
            """
                    % ",".join("?" * len(snapshot)),
                    [expression, *snapshot, limit],
                )
            ]

    @staticmethod
    def _event(db, job_id, kind, payload):
        db.execute(
            "INSERT INTO events(job_id,kind,payload,created_at) VALUES (?,?,?,?)",
            (job_id, kind, encode(payload), time.time()),
        )

    def create_job(self, question, method, request_key):
        fingerprint = hashlib.sha256(encode([question, method]).encode()).hexdigest()
        with self.connection(write=True) as db:
            old = db.execute(
                "SELECT * FROM jobs WHERE request_key=?", (request_key,)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Conflict(
                        "Idempotency key was already used for a different question or method"
                    )
                return old["id"], False
            snapshot = [
                row[0]
                for row in db.execute(
                    "SELECT current_version FROM documents ORDER BY id"
                )
                if row[0]
            ]
            if not snapshot:
                raise DocumentError("Import a document before starting research")
            job_id, now = uuid.uuid4().hex, time.time()
            state = {
                "job_id": job_id,
                "question": question,
                "method": method,
                "snapshot": snapshot,
                "next_node": "plan",
                "round": 0,
                "evidence": [],
                "queries": [],
            }
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    request_key,
                    fingerprint,
                    question,
                    method,
                    "queued",
                    encode(state),
                    0,
                    now,
                    now,
                    None,
                ),
            )
            self._event(db, job_id, "queued", {"stage": "plan"})
        return job_id, True

    def job(self, job_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise NotFound("Research run not found")
        value = dict(row)
        value["state"] = json.loads(value["state"])
        value.pop("fingerprint")
        value.pop("request_key")
        return value

    def jobs(self):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    """
                SELECT id, question, method, status, created_at, updated_at, error
                FROM jobs ORDER BY created_at DESC LIMIT 30
            """
                )
            ]

    def events(self, job_id, after=0):
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT 200",
                (job_id, after),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def claim(self):
        with self.connection(write=True) as db:
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE jobs SET status='running', updated_at=? WHERE id=?",
                (time.time(), row[0]),
            )
            self._event(db, row[0], "running", {})
        return row[0]

    def checkpoint(self, job_id, state, completed):
        with self.connection(write=True) as db:
            result = db.execute(
                "UPDATE jobs SET state=?,updated_at=? WHERE id=? AND status='running'",
                (encode(state), time.time(), job_id),
            )
            if result.rowcount != 1:
                raise Conflict("Only a running job can commit a checkpoint")
            self._event(
                db,
                job_id,
                "checkpoint",
                {
                    "completed": completed,
                    "stage": state["next_node"],
                    "round": state["round"],
                },
            )

    def finish(self, job_id):
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE jobs SET status='succeeded', error=NULL, updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
            self._event(db, job_id, "succeeded", {})

    def fail(self, job_id, message, *, transient=False):
        with self.connection(write=True) as db:
            attempts = (
                db.execute(
                    "SELECT attempts FROM jobs WHERE id=?", (job_id,)
                ).fetchone()[0]
                + 1
            )
            status = "queued" if transient and attempts <= 2 else "failed"
            db.execute(
                "UPDATE jobs SET status=?,attempts=?,error=?,updated_at=? WHERE id=?",
                (status, attempts, message, time.time(), job_id),
            )
            self._event(db, job_id, status, {"message": message})

    def retry(self, job_id):
        with self.connection(write=True) as db:
            result = db.execute(
                "UPDATE jobs SET status='queued',attempts=0,error=NULL,updated_at=? WHERE id=? AND status='failed'",
                (time.time(), job_id),
            )
            if result.rowcount != 1:
                raise Conflict("Only failed runs can be retried")
            self._event(db, job_id, "queued", {"reason": "manual_retry"})

    def recover(self):
        with self.connection(write=True) as db:
            rows = db.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
            db.execute(
                "UPDATE jobs SET status='queued', updated_at=? WHERE status='running'",
                (time.time(),),
            )
            for row in rows:
                self._event(db, row[0], "queued", {"reason": "process_restart"})
        return len(rows)
