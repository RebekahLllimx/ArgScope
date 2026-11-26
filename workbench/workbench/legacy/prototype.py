"""Offline local prototype server, using only Python's standard library."""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import webbrowser
import zipfile

from . import reconstruction as r, semantic_ir, history
from ..contracts import canonical, fields, digest, validate_case
from argscope.logic import parse
from argscope.sat import ast_data

MAX_BYTES = 100000


def decode(data):
    if len(data) > MAX_BYTES + 1000:
        raise ValueError("Request exceeds 101 KB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("Nonfinite JSON number")

    value = json.loads(
        data.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject
    )
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def inspect_document(document):
    revision = r.validate(document)
    readings = []
    for reading in document["readings"]:
        readings.append(
            {
                "id": reading["id"],
                "reading_hash": r.reading_hash(document, reading),
                "review_status": r.review_status(document, reading),
                "semantic_scope": semantic_ir.assess(reading)
                if document["schema_version"] == "reconstruction-v2"
                else None,
                "asts": [
                    {
                        "id": n["id"],
                        "formula": n["formula"],
                        "ast": ast_data(parse(n["formula"])),
                    }
                    for n in reading["nodes"]
                ]
                if reading["semantics"] == "classical-propositional-v1"
                else [],
            }
        )
    return {"revision_hash": revision, "readings": readings}


def import_draft(record):
    if not isinstance(record, dict):
        raise ValueError("Expected a case or reconstruction object")
    if record.get("schema_version") in ("research-case-v1", "research-case-v2"):
        validate_case(record)
        document = semantic_ir.annotate(r.upgrade(record, record["claim"]["id"]))
    else:
        r.validate(record)
        document = semantic_ir.annotate(record)
    document = deepcopy(document)
    document["parent_revision"] = None
    document["reviews"] = []
    r.validate(document)
    if (
        len(json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"))
        > MAX_BYTES
    ):
        raise ValueError("Imported draft exceeds the portable record size limit")
    return document


def example_documents():
    names = [
        "invalid_control",
        "valid_control",
        "semantic_readings",
        "classic_gettier-1963-case-i",
        "classic_nagel-1974-perspective-and-scope",
        "classic_quine-1948-naming-and-existence",
        "classic_quine-two-dogmas-substitution",
    ]
    result = []
    for name in names:
        resource = files("workbench").joinpath("demo", name + ".json")
        if resource.is_file():
            record = decode(resource.read_bytes())
        else:
            record = decode(
                (
                    Path(__file__).resolve().parents[2] / "examples" / (name + ".json")
                ).read_bytes()
            )
        document = import_draft(record)
        result.append({"name": name, "document": document})
    case = deepcopy(result[1]["document"])
    case["reconstruction_id"] = "propagation-chain-32"
    case["source"].update(
        key="propagation-chain",
        text="Assume p0 and the chain p0 implies p1, through p30 implies p31. Does p31 follow?",
    )
    reading = case["readings"][0]
    reading.update(
        id="chain-reading",
        summary="A 32-proposition propagation control; the exhaustive default exceeds its variable bound.",
        origin="user_defined",
    )
    premises = ["p0", *[f"p{i} -> p{i+1}" for i in range(31)]]
    reading["nodes"] = [
        {
            "id": f"P{i+1}",
            "formula": formula,
            "role": "premise",
            "speaker": "Control author",
            "commitment": "user_assumption",
            "scope": "Authored propagation control",
            "depends_on": [],
        }
        for i, formula in enumerate(premises)
    ]
    reading["nodes"].append(
        {
            "id": "S1",
            "formula": "p31",
            "role": "inference",
            "speaker": "Control author",
            "commitment": "user_assumption",
            "scope": "Selected conclusion",
            "depends_on": [n["id"] for n in reading["nodes"]],
        }
    )
    reading["target"] = "S1"
    r.validate(case)
    result.append({"name": "propagation-chain-32", "document": case})
    return result


def worker(operation, payload):
    source_db = payload["source_db"]
    if operation == "analyze":
        result = r.execute(payload["document"], source_db, backend=payload["backend"])
        if (
            len(json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8"))
            > MAX_BYTES
        ):
            raise ValueError("Result exceeds the portable bundle size limit")
        return result
    if operation == "replay":
        return r.replay_reconstruction(payload["bundle"], source_db)
    if operation == "report":
        from .report import render

        html = render(payload["bundle"], source_db)
        if len(html.encode("utf-8")) > 1000000:
            raise ValueError("Report exceeds 1 MB")
        return {"html": html}
    raise ValueError("Unknown calculation operation")


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class App:
    def __init__(self, data_dir, source_db=None, trajectory_store=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.data_dir / "history.sqlite3"
        with history.connection(self.database):
            pass
        self.source_db = str(Path(source_db).resolve()) if source_db else None
        self.trajectory_store = trajectory_store
        self.slots = threading.BoundedSemaphore(2)

    def calculate(self, operation, payload):
        if not self.slots.acquire(blocking=False):
            raise APIError(
                503, "Two calculations are already running; try again shortly"
            )
        try:
            archive = Path(sys.argv[0])
            if archive.is_file() and zipfile.is_zipfile(archive):
                command = [
                    sys.executable,
                    str(archive.resolve()),
                    "--worker",
                    operation,
                ]
            else:
                command = [
                    sys.executable,
                    "-m",
                    "workbench.legacy.prototype",
                    "--worker",
                    operation,
                ]
            completed = subprocess.run(
                command,
                input=canonical({**payload, "source_db": self.source_db}).encode(
                    "utf-8"
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            if len(completed.stdout) > 2000000:
                raise ValueError("Calculation response exceeds 2 MB")
            try:
                response = json.loads(completed.stdout)
            except ValueError:
                raise ValueError(
                    "Calculation process stopped without a complete result"
                ) from None
            if completed.returncode:
                raise ValueError(response.get("error", "Calculation failed"))
            return response
        except subprocess.TimeoutExpired:
            raise APIError(
                408, "Calculation exceeded five seconds; no result was committed"
            ) from None
        finally:
            self.slots.release()

    def save(self, document, expected_head):
        r.validate(document)
        result = deepcopy(document)
        if expected_head is not None:
            r.check_hash(expected_head)
            prior = history.load(self.database, expected_head)
            if prior["reconstruction_id"] != result["reconstruction_id"]:
                raise ValueError("Expected head belongs to a different reconstruction")
            a = {k: v for k, v in r.core(prior).items() if k != "parent_revision"}
            b = {k: v for k, v in r.core(result).items() if k != "parent_revision"}
            result["parent_revision"] = (
                r.revision_hash(prior) if a != b else prior["parent_revision"]
            )
        saved = history.save(result, self.database, expected_head)
        return {**saved, "document": result}

    def heads(self):
        from contextlib import closing

        with closing(sqlite3.connect(self.database)) as db:
            return [
                {"id": row[0], "snapshot_hash": row[1], "revision_hash": row[2]}
                for row in db.execute(
                    "SELECT h.reconstruction_id,h.snapshot_hash,s.revision_hash FROM heads h JOIN snapshots s ON s.hash=h.snapshot_hash ORDER BY h.reconstruction_id LIMIT 100"
                )
            ]

    def measure(self, payload):
        if self.trajectory_store is None:
            raise APIError(
                503, "Start the prototype with a configured trajectory store"
            )
        from falsifylab.records import read_measurement
        from falsifylab.result_store import StoreError

        if not self.slots.acquire(blocking=False):
            raise APIError(503, "Two calculations are already running")
        try:
            return read_measurement(
                self.trajectory_store,
                payload["control_key"],
                payload["treated_key"],
                payload["window"],
            )
        except StoreError:
            raise APIError(
                503,
                "The configured trajectory store is unavailable; no measurement was produced",
            ) from None
        finally:
            self.slots.release()


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, value, content_type="application/json; charset=utf-8"):
            data = (
                value if isinstance(value, bytes) else canonical(value).encode("utf-8")
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def dispatch(self):
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                raise APIError(403, "Only this local host is allowed")
            path = urlsplit(self.path).path
            if self.command == "GET":
                assets = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                    "/style.css": ("style.css", "text/css; charset=utf-8"),
                }
                if path in assets:
                    name, mime = assets[path]
                    return self.send(
                        200,
                        files("workbench.legacy").joinpath("web", name).read_bytes(),
                        mime,
                    )
                if path == "/favicon.ico":
                    return self.send(204, b"", "image/x-icon")
                if path == "/api/health":
                    return self.send(
                        200,
                        {
                            "status": "ok",
                            "prototype": "0.3.0",
                            "backends": ["exhaustive", "dpll-v1"],
                            "trajectory_store": app.trajectory_store.backend
                            if app.trajectory_store
                            else None,
                        },
                    )
                if path == "/api/examples":
                    return self.send(200, {"examples": example_documents()})
                if path == "/api/history":
                    return self.send(200, {"heads": app.heads()})
                if path.startswith("/api/history/"):
                    snapshot = path.rsplit("/", 1)[1]
                    r.check_hash(snapshot)
                    return self.send(
                        200,
                        {
                            "document": history.load(app.database, snapshot),
                            "snapshot_hash": snapshot,
                        },
                    )
                raise APIError(404, "Unknown local resource")
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {"http://" + h for h in hosts}:
                raise APIError(403, "Cross-origin writes are not allowed")
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                raise APIError(411, "One Content-Length is required")
            size = int(self.headers["Content-Length"])
            if not 0 <= size <= MAX_BYTES + 1000:
                raise APIError(413, "Request exceeds 101 KB")
            if (
                self.headers.get("Content-Type", "").split(";", 1)[0]
                != "application/json"
            ):
                raise APIError(415, "Use application/json")
            self.connection.settimeout(5)
            body = self.rfile.read(size)
            if len(body) != size:
                raise ValueError("Incomplete request body")
            payload = decode(body)
            if path == "/api/import":
                fields(payload, ("record",))
                return self.send(200, {"document": import_draft(payload["record"])})
            if path == "/api/measure":
                fields(payload, ("control_key", "treated_key", "window"))
                return self.send(200, app.measure(payload))
            if path == "/api/inspect":
                fields(payload, ("document",))
                return self.send(200, inspect_document(payload["document"]))
            if path == "/api/review":
                fields(
                    payload,
                    (
                        "document",
                        "reading_id",
                        "aspect",
                        "decision",
                        "reviewer",
                        "rationale",
                    ),
                )
                document = r.review(
                    payload["document"],
                    payload["reading_id"],
                    payload["aspect"],
                    payload["decision"],
                    payload["reviewer"],
                    payload["rationale"],
                )
                return self.send(200, {"document": document})
            if path == "/api/analyze":
                fields(payload, ("document", "backend"))
                r.validate(payload["document"])
                if payload["backend"] not in ("dpll", "exhaustive"):
                    raise ValueError("Unknown backend")
                return self.send(200, app.calculate("analyze", payload))
            if path in ("/api/replay", "/api/report"):
                fields(payload, ("bundle",))
                return self.send(200, app.calculate(path.rsplit("/", 1)[1], payload))
            if path == "/api/save":
                fields(payload, ("document", "expected_head"))
                return self.send(
                    200, app.save(payload["document"], payload["expected_head"])
                )
            raise APIError(404, "Unknown operation")

        def respond(self):
            try:
                self.dispatch()
            except APIError as error:
                self.send(error.status, {"error": error.message})
            except (ValueError, KeyError, TypeError, RecursionError) as error:
                status = 409 if "History head conflict" in str(error) else 400
                self.send(status, {"error": str(error)})
            except TimeoutError:
                self.send(408, {"error": "Local request timed out"})
            except sqlite3.Error:
                self.send(
                    503,
                    {
                        "error": "Local history or configured source database is unavailable"
                    },
                )
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = respond
        do_POST = respond

    return Handler


def create_server(data_dir, port=0, source_db=None, trajectory_store=None):
    return ThreadingHTTPServer(
        ("127.0.0.1", port), handler_for(App(data_dir, source_db, trajectory_store))
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "trajectory-read":
        from falsifylab.reader import main as reader_cli

        return reader_cli(argv[1:])
    if argv and argv[0] == "--worker":
        try:
            value = decode(sys.stdin.buffer.read(MAX_BYTES + 1001))
            result = worker(argv[1], value)
            sys.stdout.write(canonical(result))
            return 0
        except Exception as error:
            sys.stdout.write(canonical({"error": str(error)}))
            return 2
    if argv and argv[0] in (
        "run",
        "bind",
        "replay",
        "upgrade",
        "revise",
        "review",
        "analyze",
        "save",
        "load",
        "annotate",
        "diff",
        "report",
    ):
        from .cli import main as cli

        return cli(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=Path("prototype-data"))
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--trajectory-store", choices=("file", "sqlite", "kv"))
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--kv-port", type=int, default=6379)
    parser.add_argument("--namespace", default="research")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use a port in [1024,65535]")
    trajectory_store = None
    if args.trajectory_store:
        from falsifylab.result_store import open_store

        trajectory_store = open_store(
            args.trajectory_store,
            path=args.store_path,
            port=args.kv_port,
            namespace=args.namespace,
        )
    elif args.store_path is not None:
        parser.error("--store-path requires --trajectory-store")
    with create_server(
        args.data_dir, args.port, args.source_db, trajectory_store
    ) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        print("ArgScope Prototype: " + url, flush=True)
        print(
            "Press Ctrl+C to stop. History stays in the selected data directory.",
            flush=True,
        )
        if args.open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
