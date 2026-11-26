"""Read-only SourceLens schema-v1 passage snapshots; never initialize a store."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3

from ..contracts import digest, fields, text


@contextmanager
def connection(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if row is None or row[0] != "1":
            raise ValueError("Only SourceLens schema version 1 is supported")
        yield db
    finally:
        db.rollback()
        db.close()


def selection(db, version_id, chunk_id, start, end, expected):
    if type(start) is not int or type(end) is not int:
        raise ValueError("Passage offsets must be integers")
    row = db.execute(
        """
        SELECT c.*, v.document_id, v.digest, v.parser_version, v.source_url,
               d.source_key, d.current_version
        FROM chunks c JOIN versions v ON c.version_id=v.id
        JOIN documents d ON v.document_id=d.id WHERE c.id=? AND v.id=?
    """,
        (chunk_id, version_id),
    ).fetchone()
    if row is None:
        raise ValueError("Chunk does not belong to the selected source version")
    if not 0 <= start < end <= len(row["body"]):
        raise ValueError("Passage offsets are outside the parsed chunk")
    if row["body"][start:end] != expected:
        raise ValueError("Selected text does not match the stored passage")
    passage = {
        "document_id": row["document_id"],
        "version_id": version_id,
        "chunk_id": chunk_id,
        "start": start,
        "end": end,
        "chunk_hash": digest(row["body"]),
        "selection_hash": digest(expected),
        "upload_digest": row["digest"],
        "parser_version": row["parser_version"],
        "page": row["page"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
    }
    return row, passage


def bind(case, database, version_id, chunk_id, start, end, expected):
    from ..contracts import validate_case

    validate_case(case)
    with connection(database) as db:
        row, passage = selection(db, version_id, chunk_id, start, end, expected)
        bound = deepcopy(case)
        bound["schema_version"] = "research-case-v2"
        bound["source"] = {
            "key": row["source_key"],
            "version": version_id,
            "kind": case["source"]["kind"],
            "text": expected,
            "locator": "SourceLens parsed chunk " + chunk_id,
            "reference_url": row["source_url"],
            "passage": passage,
        }
        bound["reading"]["review"] = {
            "status": "draft",
            "reviewer": "unassigned",
            "rationale": "Source binding changed; review this exact passage and reading before execution.",
        }
        validate_case(bound)
        return bound


def verify_source(case, database):
    from ..contracts import validate_case

    validate_case(case)
    if case["schema_version"] != "research-case-v2":
        raise ValueError("This case has no SourceLens passage binding")
    source, p = case["source"], case["source"]["passage"]
    with connection(database) as db:
        row, actual = selection(
            db, p["version_id"], p["chunk_id"], p["start"], p["end"], source["text"]
        )
        if (
            actual != p
            or source["key"] != row["source_key"]
            or source["reference_url"] != row["source_url"]
        ):
            raise ValueError("Source provenance mismatch")
        return {
            "status": "source_verified",
            "is_current": row["current_version"] == p["version_id"],
            "version_id": p["version_id"],
            "chunk_id": p["chunk_id"],
        }


def validate_passage(source):
    p = source["passage"]
    fields(
        p,
        (
            "document_id",
            "version_id",
            "chunk_id",
            "start",
            "end",
            "chunk_hash",
            "selection_hash",
            "upload_digest",
            "parser_version",
            "page",
            "line_start",
            "line_end",
        ),
    )
    for key in ("document_id", "version_id", "chunk_id", "parser_version"):
        text(p[key], 200)
    for key in ("chunk_hash", "selection_hash", "upload_digest"):
        value = p[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("Expected a SHA-256 digest")
    if (
        type(p["start"]) is not int
        or type(p["end"]) is not int
        or not 0 <= p["start"] < p["end"]
    ):
        raise ValueError("Invalid passage offsets")
    if p["end"] - p["start"] != len(source["text"]) or p["selection_hash"] != digest(
        source["text"]
    ):
        raise ValueError("Passage identity mismatch")
    if source["version"] != p["version_id"]:
        raise ValueError("Source and passage version mismatch")
    for key in ("page", "line_start", "line_end"):
        if p[key] is not None and (type(p[key]) is not int or p[key] < 1):
            raise ValueError("Invalid passage location")
