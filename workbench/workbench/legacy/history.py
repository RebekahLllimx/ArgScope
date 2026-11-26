"""Append-only reconstruction snapshots with transactional head comparison.

This is a separate workbench database, not a migration of SourceLens.
"""
from contextlib import contextmanager, closing
import json
import sqlite3

from ..contracts import canonical, digest
from .reconstruction import validate


@contextmanager
def connection(path):
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("BEGIN IMMEDIATE")
        # Refuse existing unrelated databases rather than initializing over them.
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if tables and tables != {"workbench_metadata", "snapshots", "heads"}:
            raise ValueError("Not a workbench history database")
        db.execute(
            "CREATE TABLE IF NOT EXISTS workbench_metadata(version TEXT NOT NULL)"
        )
        row = db.execute("SELECT version FROM workbench_metadata").fetchall()
        if row and row != [("1",)]:
            raise ValueError("Unsupported history schema")
        if not row:
            db.execute("INSERT INTO workbench_metadata(version) VALUES ('1')")
        db.execute(
            "CREATE TABLE IF NOT EXISTS snapshots(hash TEXT PRIMARY KEY, reconstruction_id TEXT NOT NULL, revision_hash TEXT NOT NULL, parent_snapshot TEXT, payload TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS heads(reconstruction_id TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL)"
        )
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def save(document, path, expected_head=None):
    revision = validate(document)
    snapshot = digest(document)
    key = document["reconstruction_id"]
    with connection(path) as db:
        head = db.execute(
            "SELECT snapshot_hash FROM heads WHERE reconstruction_id=?", (key,)
        ).fetchone()
        actual = head[0] if head else None
        if actual != expected_head:
            raise ValueError("History head conflict; reload before saving")
        if actual:
            old = json.loads(
                db.execute(
                    "SELECT payload FROM snapshots WHERE hash=?", (actual,)
                ).fetchone()[0]
            )
            if digest(old) != actual:
                raise ValueError("Corrupt history snapshot")
            old_revision = validate(old)
            if revision != old_revision and document["parent_revision"] != old_revision:
                raise ValueError(
                    "Changed content must reference the previous revision hash"
                )
            if document["reviews"][: len(old["reviews"])] != old["reviews"]:
                raise ValueError("Review history must be append-only")
        elif document["parent_revision"] is not None:
            raise ValueError("Initial snapshot cannot have a missing parent revision")
        if snapshot != actual:
            db.execute(
                "INSERT INTO snapshots(hash,reconstruction_id,revision_hash,parent_snapshot,payload) VALUES (?,?,?,?,?)",
                (snapshot, key, revision, actual, canonical(document)),
            )
            db.execute(
                "INSERT INTO heads(reconstruction_id,snapshot_hash) VALUES (?,?) ON CONFLICT(reconstruction_id) DO UPDATE SET snapshot_hash=excluded.snapshot_hash",
                (key, snapshot),
            )
    return {
        "snapshot_hash": snapshot,
        "revision_hash": revision,
        "reconstruction_id": key,
    }


def load(path, snapshot):
    from pathlib import Path

    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        if db.execute("SELECT version FROM workbench_metadata").fetchall() != [("1",)]:
            raise ValueError("Unsupported history schema")
        row = db.execute(
            "SELECT payload FROM snapshots WHERE hash=?", (snapshot,)
        ).fetchone()
    if row is None:
        raise ValueError("Unknown history snapshot")
    document = json.loads(row[0])
    validate(document)
    if digest(document) != snapshot:
        raise ValueError("History content hash mismatch")
    return document
