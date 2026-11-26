"""Run the local ArgScope review MVP with a real custom KV and explicit demo mode."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import sys
import threading
import time
import uuid

from fastapi import HTTPException, Request as HTTPRequest
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sourcelens.api import create_app as source_app

from .review.graph import Workflow
from .review.providers import DemoProvider, OllamaProvider, examples
from .review.bailian import BailianProvider
from .review.codex_provider import CodexProvider
from .review.paper_examples import PaperExamplesProvider
from .review.research_examples import research_examples
from .review.schemas import Request, HumanReview, RevisionFeedback, validate_draft
from .review.versions import compare
from .review.shared import Shared
from .review.store import Store
from .review import render
from .version import __version__

WEB = Path(__file__).resolve().parent / "review_web"


class Service:
    def __init__(self, data_dir, shared, provider):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "reviews.sqlite3")
        self.flow = Workflow(
            self.store, shared, provider, self.data_dir / "graph.sqlite3"
        )
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="argument-review"
        )
        self.busy = set()
        self.lock = threading.Lock()

    def view(self, run_id):
        # A worker may finish between reading its previous checkpoint and status.
        # Keep this observation running; the next read sees the final checkpoint.
        with self.lock:
            was_busy = run_id in self.busy
        request = self.store.request(run_id)
        snapshot = self.flow.snapshot(run_id)
        state = snapshot.values or {}
        events = self.store.events(run_id)
        last = self.store.latest_event(
            run_id, ("execution_started", "execution_failed", "execution_finished")
        )
        status = "queued"
        if state.get("report_ref") and not snapshot.next:
            status = "succeeded"
        elif was_busy or run_id in self.busy:
            status = "running"
        elif last and last["node"] == "execution_failed":
            status = last["category"]
        elif self.store.has_event(run_id, "cancel_requested"):
            status = "cancelled"
        elif snapshot.interrupts:
            status = "awaiting_review"
        elif state:
            status = "interrupted"
        return {
            "id": run_id,
            "status": status,
            "request": request,
            "draft": self.flow.payload(state["draft_ref"])
            if state.get("draft_ref")
            else None,
            "draft_ref": state.get("draft_ref"),
            "issues": state.get("issues", []),
            "issue_history": state.get("issue_log", []),
            "candidate_checks": self.flow.payload(state["audit_ref"])["result"].get(
                "candidate_checks", []
            )
            if state.get("audit_ref")
            and state.get("audit_draft_ref") == state.get("draft_ref")
            else [],
            "revision": state.get("revision", 0),
            "feedback_round": state.get("feedback_round", 0),
            "feedback_available": state.get("workflow_version") == "review-workflow-v2" and not state.get("feedback_round", 0),
            "revision_feedback": self.flow.payload(state["feedback_ref"]) if state.get("feedback_ref") else None,
            "versions": state.get("versions") or ([{"ref": state["draft_ref"], "origin": "legacy_current", "parent": None}] if state.get("draft_ref") else []),
            "next": list(snapshot.next),
            "report": self.flow.payload(state["report_ref"])
            if state.get("report_ref")
            else None,
            "events": events,
            "provider": {
                "name": state.get("provider_name", self.flow.provider.name),
                "live_model": state.get("live_model", self.flow.provider.live),
            },
            "error": last.get("error")
            if last and last["node"] == "execution_failed"
            else None,
        }

    def queue(self, run_id, human=None):
        with self.lock:
            if run_id in self.busy:
                raise HTTPException(409, "This review is already queued or running")
            self.busy.add(run_id)

        def execute():
            try:
                self.flow.run(run_id, human)
            finally:
                with self.lock:
                    self.busy.discard(run_id)

        self.executor.submit(execute)

    def recover(self):
        for run in self.store.runs():
            view = self.view(run["id"])
            if view["status"] in ("queued", "interrupted"):
                self.queue(run["id"])

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=False)
        self.flow.close()


def create_app(
    data_dir,
    kv_port,
    namespace="review",
    provider=None,
    ttl=3600,
    source_database=None,
    run_source_worker=True,
):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    service = Service(
        data_dir, Shared(kv_port, namespace, ttl), provider or DemoProvider()
    )
    app = source_app(
        source_database or data_dir / "sources.sqlite3", run_worker=run_source_worker
    )
    app.title = "ArgScope Review MVP"
    app.version = __version__
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            service.recover()
            try:
                yield
            finally:
                service.close()

    app.router.lifespan_context = lifespan
    app.state.review = service
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "path", None) != "/"
    ]

    @app.middleware("http")
    async def strict_review_json(request, call_next):
        if request.method == "POST" and request.url.path.startswith("/api/argument-"):
            if (
                request.headers.get("content-type", "").split(";", 1)[0]
                != "application/json"
            ):
                return HTMLResponse(
                    '{"detail":"Use application/json"}',
                    status_code=415,
                    media_type="application/json",
                )
            raw = await request.body()
            cap = 2_000_000 if request.url.path == "/api/argument-verify" else 200_000
            if len(raw) > cap:
                return HTMLResponse(
                    '{"detail":"Review request exceeds its size limit"}',
                    status_code=413,
                    media_type="application/json",
                )

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON key")
                    result[key] = value
                return result

            def reject(value):
                raise ValueError("Nonfinite JSON value")

            try:
                json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
            except (ValueError, UnicodeError):
                return HTMLResponse(
                    '{"detail":"Malformed or duplicate-key JSON"}',
                    status_code=400,
                    media_type="application/json",
                )
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return HTMLResponse(
            json.dumps({"detail": str(error)}),
            status_code=400,
            media_type="application/json",
        )

    def valid_run(run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise HTTPException(404, "Unknown review run")
        try:
            return service.view(run_id)
        except KeyError:
            raise HTTPException(404, "Unknown review run") from None

    @app.get("/api/argument-health")
    def health():
        try:
            available = service.flow.shared.client.command(b"PING") == b"PONG"
        except OSError:
            available = False
        return {
            "status": "ok",
            "version": __version__,
            "framework": "LangGraph",
            "provider": {
                "name": service.flow.provider.name,
                "live_model": service.flow.provider.live,
            },
            "kv_available": available,
            "ttl_seconds": ttl,
            "live_model_tested": False if not service.flow.provider.live else None,
        }

    @app.get("/api/argument-examples")
    def controls():
        return examples() + (
            research_examples()
            if service.flow.provider.live
            or getattr(service.flow.provider, "supports_research_examples", False)
            else []
        )

    @app.post("/api/argument-validate")
    def validate_editor(body: dict):
        if set(body) != {"draft", "request"}:
            raise ValueError("Expected draft and frozen request")
        draft, issues = validate_draft(body["draft"], body["request"])
        return {"draft": draft, "issues": issues}

    @app.post("/api/argument-verify")
    def verify_packet(body: dict):
        return render.verify(body)

    @app.get("/api/argument-sources")
    def library():
        chunks = app.state.store.chunks(app.state.store.snapshot())[:100]
        return [
            {
                "chunk_id": c["id"],
                "title": c["title"],
                "version": c["label"],
                "text": c["body"],
                "reference_url": c["source_url"],
                "binding": {
                    "document_id": c["document_id"],
                    "version_id": c["version_id"],
                    "chunk_id": c["id"],
                    "upload_sha256": c["digest"],
                },
            }
            for c in chunks
        ]

    @app.post("/api/argument-runs", status_code=202)
    def create(body: Request):
        request = body.model_dump(mode="json")
        for source in request["sources"]:
            if source["binding"] is not None:
                binding = source["binding"]
                if set(binding) != {
                    "document_id",
                    "version_id",
                    "chunk_id",
                    "upload_sha256",
                }:
                    raise HTTPException(400, "Unexpected source binding")
                chunk = app.state.store.chunk(binding["chunk_id"])
                if source["text"] != chunk["body"] or binding != {
                    "document_id": chunk["document_id"],
                    "version_id": chunk["version_id"],
                    "chunk_id": chunk["id"],
                    "upload_sha256": chunk["digest"],
                }:
                    raise HTTPException(400, "Library source version or text mismatch")
                source.update(
                    title=chunk["title"],
                    version=chunk["label"],
                    reference_url=chunk["source_url"],
                )
        # Verify atomic EX capability without touching any external application key.
        key = service.store.archive("kv-capability-probe", {"nonce": uuid.uuid4().hex})
        try:
            service.flow.shared.ensure(service.store, key)
        except (OSError, ValueError):
            raise HTTPException(
                503,
                "The shared KV profile is unavailable or lacks atomic EX/NX support",
            ) from None
        run_id = service.store.create(request)
        service.queue(run_id)
        return {"id": run_id}

    @app.get("/api/argument-runs")
    def runs():
        return [
            {**run, "status": service.view(run["id"])["status"]}
            for run in service.store.runs()
        ]

    @app.get("/api/argument-runs/{run_id}")
    def get(run_id: str):
        return valid_run(run_id)

    @app.post("/api/argument-runs/{run_id}/resume", status_code=202)
    def resume(run_id: str, body: HumanReview):
        view = valid_run(run_id)
        if view["status"] != "awaiting_review":
            raise HTTPException(409, "This run is not awaiting human review")
        if body.expected_draft != view["draft_ref"]:
            raise HTTPException(409, "Stale draft review; reopen the current content")
        if body.edited_draft is not None:
            validate_draft(body.edited_draft.model_dump(mode="json"), view["request"])
        service.queue(run_id, body.model_dump(mode="json"))
        return {"id": run_id}

    @app.post("/api/argument-runs/{run_id}/retry", status_code=202)
    def retry(run_id: str):
        view = valid_run(run_id)
        if view["status"] not in ("failed", "interrupted"):
            raise HTTPException(409, "Only a failed or interrupted run can be retried")
        service.queue(run_id)
        return {"id": run_id}

    @app.post("/api/argument-runs/{run_id}/feedback", status_code=202)
    def feedback(run_id: str, body: RevisionFeedback):
        view = valid_run(run_id)
        if view["status"] != "awaiting_review" or not view["feedback_available"]:
            raise HTTPException(409, "This run cannot accept another feedback revision")
        if body.expected_draft != view["draft_ref"]:
            raise HTTPException(409, "Stale draft feedback; reopen the current content")
        if body.edited_draft is not None:
            validate_draft(body.edited_draft.model_dump(mode="json"), view["request"])
        service.queue(run_id, body.model_dump(mode="json"))
        return {"id": run_id}

    def saved_draft(view, ref):
        if ref not in {v["ref"] for v in view["versions"]}:
            raise HTTPException(404, "Version does not belong to this run's saved history")
        return service.flow.payload(ref)

    @app.get("/api/argument-runs/{run_id}/compare")
    def compare_versions(run_id: str, before: str, after: str):
        view = valid_run(run_id)
        left, right = saved_draft(view, before), saved_draft(view, after)
        return {"run_id": run_id, "before_ref": before, "after_ref": after,
                "before": left, "after": right, "changes": compare(left, right),
                "scope": "Saved content differences only; no automatic judgment that a revision is better."}

    @app.post("/api/argument-runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        view = valid_run(run_id)
        if view["status"] in ("succeeded", "cancelled"):
            raise HTTPException(409, "This run has already stopped")
        service.store.event(
            run_id, "cancel_requested", {"reason": "User requested cancellation"}
        )
        return {"id": run_id}

    @app.get("/api/argument-runs/{run_id}/report.json")
    def report_json(run_id: str):
        view = valid_run(run_id)
        if not view["report"]:
            raise HTTPException(
                409, "Complete the human review before exporting the final packet"
            )
        return render.export(view["report"], view["events"])

    @app.get("/api/argument-runs/{run_id}/report.html")
    def report_html(run_id: str):
        return HTMLResponse(
            render.html(report_json(run_id)),
            headers={
                "Content-Disposition": "attachment; filename=argument-review.html"
            },
        )

    @app.get("/api/argument-runs/{run_id}/events")
    async def events(run_id: str, request: HTTPRequest):
        valid_run(run_id)
        cursor = request.headers.get("last-event-id", "")

        async def stream():
            nonlocal cursor
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline and not await request.is_disconnected():
                view = await asyncio.to_thread(valid_run, run_id)
                rows = view["events"]
                ids = [r["id"] for r in rows]
                start = ids.index(cursor) + 1 if cursor in ids else 0
                for event in rows[start:]:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                if view["status"] not in ("queued", "running"):
                    yield f"event: settled\ndata: {json.dumps({'status':view['status']})}\n\n"
                    return
                if start == len(rows):
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    if (WEB / "review-assets").is_dir():
        app.mount(
            "/review-assets",
            StaticFiles(directory=WEB / "review-assets"),
            name="review-assets",
        )

    @app.get("/")
    def index():
        if not (WEB / "index.html").is_file():
            raise HTTPException(503, "Build the ArgScope review frontend first")
        return FileResponse(WEB / "index.html")

    @app.get("/library")
    def source_index():
        page = Path(__file__).resolve().parents[2] / "sourcelens/web/dist/index.html"
        if not page.is_file():
            raise HTTPException(503, "Build the SourceLens frontend first")
        return FileResponse(page)

    return app


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "verify-report":
        data = Path(argv[1]).read_bytes()
        if len(data) > 2_000_000:
            raise ValueError("Report exceeds 2 MB")
        print(json.dumps(render.verify(json.loads(data))))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("review-data"))
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--kv-port", type=int, default=6379)
    parser.add_argument("--namespace", default="review")
    parser.add_argument("--kv-root", type=Path)
    parser.add_argument(
        "--provider",
        choices=("demo", "papers", "ollama", "bailian", "codex"),
        default="codex",
        help="Inference provider (default: codex); demo/papers are explicit offline modes",
    )
    parser.add_argument("--model")
    parser.add_argument("--codex-bin", type=Path)
    parser.add_argument(
        "--model-config",
        type=Path,
        help="Local Bailian env file; never copied to artifacts",
    )
    parser.add_argument("--model-url", default="http://127.0.0.1:11434")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Use an HTTP port in [1024,65535]")
    if args.provider == "ollama" and not args.model:
        parser.error("--provider ollama requires --model")
    session = None
    try:
        if args.kv_root:
            import select, subprocess

            args.data_dir.mkdir(parents=True, exist_ok=True)
            launcher = (
                Path(__file__).resolve().parents[2]
                / "falsifylab/tools/serve_research_kv.py"
            )
            session = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(launcher),
                    "--kv-root",
                    str(args.kv_root.resolve()),
                    "--aof",
                    str((args.data_dir / "shared-session.aof").resolve()),
                    "--port",
                    "0",
                ],
                stdout=subprocess.PIPE,
            )
            if not select.select([session.stdout], [], [], 5)[0]:
                raise ValueError("KV session did not become ready")
            line = session.stdout.readline()
            if not line:
                raise ValueError("KV session stopped during startup")
            args.kv_port = json.loads(line)["port"]
        provider = (
            DemoProvider()
            if args.provider == "demo"
            else (
                BailianProvider(args.model_config, args.model)
                if args.provider == "bailian"
                else (
                    PaperExamplesProvider()
                    if args.provider == "papers"
                    else (
                        CodexProvider(args.codex_bin, args.model)
                        if args.provider == "codex"
                        else OllamaProvider(args.model, args.model_url)
                    )
                )
            )
        )
        app = create_app(args.data_dir, args.kv_port, args.namespace, provider)
        print(
            json.dumps(
                {
                    "status": "starting",
                    "http_port": args.port,
                    "kv_port": args.kv_port,
                    "managed_kv": session is not None,
                    "provider": provider.name,
                }
            ),
            flush=True,
        )
        if args.open_browser:
            import webbrowser

            def open_when_ready():
                from urllib.request import urlopen

                url = f"http://127.0.0.1:{args.port}"
                for _ in range(120):
                    try:
                        with urlopen(
                            url + "/api/argument-health", timeout=0.3
                        ) as response:
                            health = json.load(response)
                        if health.get("provider", {}).get("name") == provider.name:
                            webbrowser.open(url)
                            return
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.25)

            threading.Thread(target=open_when_ready, daemon=True).start()
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if session is not None:
            import signal

            session.send_signal(signal.SIGINT)
            try:
                session.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.kill()
                session.wait()
            session.stdout.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
