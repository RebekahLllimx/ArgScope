"""Durable artifacts and audit records; graph cursor authority stays in LangGraph."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid
from workbench.contracts import canonical, digest


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            tables = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            allowed = {
                "review_metadata",
                "review_runs",
                "review_artifacts",
                "review_events",
                "review_operations",
                "review_human",
            }
            if tables and tables != allowed:
                raise ValueError("Use a separate argument-review database")
            db.execute(
                "CREATE TABLE IF NOT EXISTS review_metadata(version INTEGER NOT NULL)"
            )
            version = db.execute("SELECT version FROM review_metadata").fetchall()
            if version and version != [(1,)]:
                raise ValueError("Unknown review database version")
            if not version:
                db.execute("INSERT INTO review_metadata VALUES(1)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS review_runs(id TEXT PRIMARY KEY,request TEXT NOT NULL,created REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS review_artifacts(hash TEXT PRIMARY KEY,body TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS review_events(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,created REAL NOT NULL,node TEXT NOT NULL,payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS review_operations(run_id TEXT NOT NULL,operation TEXT NOT NULL,input_hash TEXT NOT NULL,artifact TEXT NOT NULL,PRIMARY KEY(run_id,operation,input_hash))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS review_human(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,draft TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL)"
            )

    @contextmanager
    def db(self):
        """Open a write transaction.

        Kept as the public write helper for compatibility with maintenance tools.
        Read paths use ``read`` so they do not acquire SQLite's write-intent lock.
        """
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def read(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA query_only=ON")
            yield db
        finally:
            db.close()

    def create(self, request):
        run_id = uuid.uuid4().hex
        with self.db() as db:
            db.execute(
                "INSERT INTO review_runs VALUES(?,?,?)",
                (run_id, canonical(request), time.time()),
            )
        return run_id

    def request(self, run_id):
        with self.read() as db:
            row = db.execute(
                "SELECT request FROM review_runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Unknown review run")
        return json.loads(row[0])

    def runs(self):
        with self.read() as db:
            return [
                {"id": r[0], "question": json.loads(r[1])["question"], "created": r[2]}
                for r in db.execute(
                    "SELECT id,request,created FROM review_runs ORDER BY created DESC LIMIT 100"
                )
            ]

    def archive(self, kind, payload, inputs=()):
        body = {
            "schema": "review-artifact-v1",
            "kind": kind,
            "inputs": list(inputs),
            "payload": payload,
        }
        key = digest(body)
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO review_artifacts VALUES(?,?)",
                (key, canonical(body)),
            )
        return key

    def artifact(self, key):
        with self.read() as db:
            row = db.execute(
                "SELECT body FROM review_artifacts WHERE hash=?", (key,)
            ).fetchone()
        if row is None:
            raise KeyError("Archived artifact is missing")
        body = json.loads(row[0])
        if digest(body) != key:
            raise ValueError("Archived artifact integrity mismatch")
        return body

    def event(self, run_id, node, payload, identity=None):
        event_id = identity or digest([run_id, node, payload])
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO review_events VALUES(?,?,?,?,?)",
                (event_id, run_id, time.time(), node, canonical(payload)),
            )

    def events(self, run_id, limit=200):
        if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 1000):
            raise ValueError("Event limit must be between 1 and 1000")
        suffix = "" if limit is None else f" LIMIT {limit}"
        with self.read() as db:
            return [
                {"id": r[0], "created": r[1], "node": r[2], **json.loads(r[3])}
                for r in db.execute(
                    "SELECT id,created,node,payload FROM review_events "
                    "WHERE run_id=? ORDER BY created,rowid" + suffix,
                    (run_id,),
                )
            ]

    def latest_event(self, run_id, nodes):
        nodes = tuple(nodes)
        if not nodes:
            return None
        placeholders = ",".join("?" for _ in nodes)
        with self.read() as db:
            row = db.execute(
                "SELECT id,created,node,payload FROM review_events "
                f"WHERE run_id=? AND node IN ({placeholders}) "
                "ORDER BY created DESC,rowid DESC LIMIT 1",
                (run_id, *nodes),
            ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "created": row[1], "node": row[2], **json.loads(row[3])}

    def has_event(self, run_id, node):
        with self.read() as db:
            return (
                db.execute(
                    "SELECT 1 FROM review_events WHERE run_id=? AND node=? LIMIT 1",
                    (run_id, node),
                ).fetchone()
                is not None
            )

    def reserve(self, run_id, kind, maximum, payload):
        with self.db() as db:
            count = db.execute(
                "SELECT count(*) FROM review_events WHERE run_id=? AND node=?",
                (run_id, kind),
            ).fetchone()[0]
            if count >= maximum:
                raise ValueError(f"{kind} budget exhausted")
            db.execute(
                "INSERT INTO review_events VALUES(?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    run_id,
                    time.time(),
                    kind,
                    canonical({**payload, "attempt": count + 1}),
                ),
            )
        return count + 1

    def receipt(self, run_id, operation, input_hash):
        with self.read() as db:
            row = db.execute(
                "SELECT artifact FROM review_operations WHERE run_id=? AND operation=? AND input_hash=?",
                (run_id, operation, input_hash),
            ).fetchone()
        return row[0] if row else None

    def record(self, run_id, operation, input_hash, artifact):
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO review_operations VALUES(?,?,?,?)",
                (run_id, operation, input_hash, artifact),
            )
        return self.receipt(run_id, operation, input_hash)

    def human(self, run_id, draft, body):
        key = digest([run_id, draft, body])
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO review_human VALUES(?,?,?,?,?)",
                (key, run_id, draft, canonical(body), time.time()),
            )
        return key

    def human_records(self, run_id):
        with self.read() as db:
            return [
                {"id": r[0], "draft": r[1], "review": json.loads(r[2]), "created": r[3]}
                for r in db.execute(
                    "SELECT id,draft,body,created FROM review_human WHERE run_id=? ORDER BY created",
                    (run_id,),
                )
            ]
