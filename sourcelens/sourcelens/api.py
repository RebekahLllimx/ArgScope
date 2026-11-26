"""Loopback-only HTTP application for a local document library."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import uuid

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from .documents import DocumentError, MAX_BYTES, chunk_markdown, parse_file
from .retrieval import METHODS, RetrievalMethod, Retriever
from .store import Conflict, NotFound, Store
from .workflow import ResearchWorkflow, Worker, markdown_report

ROOT = Path(__file__).resolve().parents[1]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Source(StrictModel):
    source_key: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$"
    )
    title: str = Field(min_length=1, max_length=180)
    version_label: str = Field(default="v1", min_length=1, max_length=80)
    source_url: str | None = Field(default=None, max_length=1000)

    @field_validator("source_url")
    @classmethod
    def public_link(cls, value):
        if value:
            parts = urlparse(value)
            if (
                parts.scheme not in {"https", "http"}
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ValueError(
                    "Source URL must be an HTTP(S) link without embedded credentials"
                )
        return value or None


class TextSource(Source):
    text: str = Field(min_length=1, max_length=MAX_BYTES)


class Query(StrictModel):
    query: str = Field(min_length=2, max_length=600)
    method: RetrievalMethod = "hybrid"
    limit: int = Field(default=6, ge=1, le=12)


class Research(StrictModel):
    question: str = Field(min_length=5, max_length=600)
    method: RetrievalMethod = "hybrid"


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Limit streaming request bodies, including multipart and chunked uploads."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        count = 0
        started = False

        async def bounded_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > MAX_BYTES + 100_000:
                raise BodyTooLarge()
            return message

        async def track_send(message):
            nonlocal started
            started |= message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, bounded_receive, track_send)
        except BodyTooLarge:
            if not started:
                await JSONResponse(
                    {"detail": "Request exceeds the 5 MB upload limit"}, status_code=413
                )(scope, receive, send)


def create_app(database: Path | None = None, *, run_worker=True):
    store = Store(
        database or Path(os.environ.get("SOURCELENS_DB", "var/sourcelens.sqlite3"))
    )
    retriever = Retriever(store)
    workflow = ResearchWorkflow(store, retriever)
    worker = Worker(store, workflow)

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.stop()

    app = FastAPI(
        title="SourceLens",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.store, app.state.workflow = store, workflow
    app.add_middleware(BodyLimit)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def local_security(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse(
                    {"detail": "Cross-origin writes are not permitted"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers[
            "Content-Security-Policy"
        ] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DocumentError)
    async def document_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(NotFound)
    async def not_found(request, error):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "answer_mode": "extractive",
            "retrieval": sorted(METHODS),
            "worker_status": "running"
            if worker.thread and worker.thread.is_alive()
            else "disabled"
            if not run_worker
            else "stopped",
        }

    @app.get("/api/documents")
    def documents():
        return store.documents()

    @app.post("/api/documents", status_code=201)
    def ingest(source: TextSource):
        return store.ingest(
            source_key=source.source_key,
            title=source.title,
            label=source.version_label,
            source_url=source.source_url,
            data=source.text.encode(),
            chunks=chunk_markdown(source.text),
        )

    @app.post("/api/documents/upload", status_code=201)
    async def upload(
        file: UploadFile = File(...),
        source_key: str = Form(...),
        title: str = Form(...),
        version_label: str = Form("v1"),
        source_url: str = Form(""),
    ):
        from pydantic import ValidationError

        try:
            source = Source(
                source_key=source_key,
                title=title,
                version_label=version_label,
                source_url=source_url or None,
            )
        except ValidationError as error:
            raise HTTPException(
                422, "Check the document key, title, version and source URL"
            ) from error
        data = await file.read(MAX_BYTES + 1)
        chunks = await run_in_threadpool(parse_file, file.filename or "", data)
        return await run_in_threadpool(
            store.ingest,
            source_key=source.source_key,
            title=source.title,
            label=source.version_label,
            source_url=source.source_url,
            data=data,
            chunks=chunks,
        )

    @app.get("/api/documents/{document_id}/versions")
    def versions(document_id: str):
        return store.versions(document_id)

    @app.get("/api/chunks/{chunk_id}")
    def chunk(chunk_id: str):
        return store.chunk(chunk_id)

    @app.post("/api/examples", status_code=201)
    def examples():
        result = []
        for path in sorted((ROOT / "examples").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            result.append(
                store.ingest(
                    source_key="example-" + path.stem,
                    title=text.splitlines()[0].lstrip("# "),
                    label="Example notes v1",
                    source_url=None,
                    data=text.encode(),
                    chunks=chunk_markdown(text),
                )
            )
        return result

    @app.post("/api/search")
    def search(query: Query):
        return retriever.search(query.query, method=query.method, limit=query.limit)

    @app.post("/api/runs", status_code=202)
    def research(query: Research, idempotency_key: str | None = Header(default=None)):
        key = idempotency_key or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", key):
            raise HTTPException(
                422, "Use an alphanumeric idempotency key of at most 100 characters"
            )
        job_id, created = store.create_job(query.question, query.method, key)
        return {"id": job_id, "created": created}

    @app.get("/api/runs")
    def runs():
        return store.jobs()

    @app.get("/api/runs/{job_id}")
    def run(job_id: str):
        job = store.job(job_id)
        current = set(store.snapshot())
        report = job["state"].get("report")
        if report:
            for citation in report["citations"]:
                citation["is_current"] = citation["version_id"] in current
            report["outdated_sources"] = len(
                {c["version_id"] for c in report["citations"] if not c["is_current"]}
            )
        return job

    @app.post("/api/runs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        store.job(job_id)
        store.retry(job_id)
        return {"id": job_id}

    @app.get("/api/runs/{job_id}/report.md")
    def report(job_id: str):
        job = store.job(job_id)
        value = job["state"].get("report")
        if job["status"] != "succeeded" or not value:
            raise HTTPException(409, "The report is not complete")
        return PlainTextResponse(
            markdown_report(value),
            headers={
                "Content-Disposition": 'attachment; filename="sourcelens-report.md"'
            },
        )

    @app.get("/api/runs/{job_id}/events")
    async def events(job_id: str, request: Request, after: int = 0):
        store.job(job_id)
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "Invalid event cursor")

        async def stream():
            nonlocal cursor
            deadline = time.monotonic() + 55
            while time.monotonic() < deadline and not await request.is_disconnected():
                rows = await run_in_threadpool(store.events, job_id, cursor)
                for event in rows:
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(event)}\n\n"
                job = await run_in_threadpool(store.job, job_id)
                if job["status"] in {"succeeded", "failed"}:
                    yield "event: done\ndata: {}\n\n"
                    return
                if not rows:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    web = ROOT / "web" / "dist"
    if web.exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        if not (web / "favicon.svg").exists():
            raise HTTPException(404, "Frontend has not been built")
        return FileResponse(web / "favicon.svg", media_type="image/svg+xml")

    @app.get("/")
    def index():
        if not (web / "index.html").exists():
            return PlainTextResponse(
                "Build the frontend: cd web && npm ci && npm run build", status_code=503
            )
        return FileResponse(web / "index.html")

    return app
