# SourceLens — Engineering Design

Implementation reviewed: 23 November 2026. Release scope: a single-user local document-research application with deterministic evidence extraction, versioned citations and resumable jobs.

## Retained 0.3 read-only workbench integration

The sibling workbench can bind a fixed version/chunk selection from this schema-v1
library, verify it during execution/replay and present it in a standalone review
report. This adapter does not initialize, migrate or queue jobs in the SourceLens
database. The SourceLens HTTP API and React application remain unchanged. The independent
workbench prototype supplies its own source-snapshot editor and formal-analysis
interface; it does not bundle or require this retrieval application.
See the maintained [Workbench Engineering Design](../../workbench/docs/ENGINEERING_DESIGN.md)
for the integrated source/reconstruction/review contracts.
The current [application](../../README.md) includes a
bounded reader-controlled argument-review workflow using LangGraph and KV. This
component supplies pinned sources and application infrastructure; its original
deterministic extraction workflow remains a distinct implementation.

## Current workbench composition and storage boundary

The 0.4 ArgScope demo MVP composes this application's existing FastAPI routes
and source store, then adds a separate prose-review router, worker and React
entry page. `/library` retains the source UI. The review form can select and
verify a stored version/chunk; plain supplied snapshots remain supported.
See the current [workbench design](../../workbench/docs/ENGINEERING_DESIGN.md)
and [MVP guide](../../workbench/docs/MVP_GUIDE.md).

SQLite remains the durable source-library owner. The new graph checkpointer,
review artifacts and human records use separate application databases. KV is a
workbench-owned expiring artifact exchange with tool processes; it does not
replace SourceLens FTS, versions, transactions or the original extractive jobs.

The sibling KV now supports atomic SET EX/NX/XX and CAS/CAD. The review MVP uses
GET and atomic EX/NX publication and was tested with the actual implementation
and a separate AOF. It does not use distributed locks or claim KV speed gains.
The previous read-only 0.3 source adapter and numerical sharing path remain
separate, compatible interfaces. Demo review rules are not a live language model,
and this component's extractive workflow remains deterministic as documented below.

## 1. Problem and requirements

Technical research often involves several documents that change independently. A fluent answer is difficult to inspect if it does not reveal which passage, edition or framework version supported it. SourceLens keeps those relationships visible and makes retrieval behavior directly testable.

The primary user imports a small collection of technical notes, reports or papers, asks a concrete question and inspects source passages before writing an interpretation. Example tasks include comparing recovery mechanisms, locating a retry guarantee and checking whether an older report cites the current source.

| Functional requirement | Acceptance condition |
| --- | --- |
| Import documents | Parse Markdown/text or text-based PDF into passages with headings and positions |
| Maintain versions | Same document key plus changed bytes creates a version; unchanged bytes do not duplicate indexing |
| Compare retrieval | BM25, character TF-IDF and fused rankings operate on the same pinned source set |
| Produce inspectable evidence | Every excerpt resolves to its exact passage and immutable source version |
| Expose missing evidence | A bounded search can finish with no matching evidence instead of fabricating an answer |
| Resume work | Restart continues from the last committed node boundary |
| Make progress observable | Persisted progress events stream to the UI and can be replayed after reconnect |
| Remain local | No document fetch, model call, tracing export or CDN is required |

Non-functional requirements are bounded input and corpus size, explicit failure states, transactional updates, source-safe rendering and a usable desktop/mobile interface. Multi-user hosting and arbitrary untrusted file processing are outside the operating model.

## 2. System architecture

```text
React UI
  │ JSON / multipart / server-sent events
FastAPI boundary
  ├── documents.py → paragraph/page chunks → Store transaction
  ├── retrieval.py → FTS5 BM25 + snapshot TF-IDF index → RRF
  └── job creation → SQLite queue → one worker
                                      │
                            LangGraph StateGraph
                          plan → retrieve → assess
                                   ↑          │
                                   └ follow-up┤ at most once
                                             └ compose → verified report
```

The parser owns content and positions. The repository owns persistence and transactions. The retriever owns ranking, not claims. The workflow owns bounded control flow and report construction. HTTP validation and React presentation sit outside those mechanisms.

LangGraph provides graph execution and conditional edges. **Application-owned SQLite checkpoints** store the latest state and progress event together; the implementation does not use a LangGraph checkpointer plugin or promise every intermediate state is retained. On invocation, a conditional entry edge selects `next_node` from the saved state.

### Technology choices

| Choice | Reason and tradeoff |
| --- | --- |
| SQLite + FTS5 | One-file setup and atomic version/job changes; one writer and bounded corpus rather than a distributed search service |
| Character TF-IDF | Inspectable CPU-only vector baseline with morphology matching; no semantic synonym understanding |
| Reciprocal rank fusion | Combine ranks without calibrating incompatible BM25/cosine score scales; improvement must be measured |
| LangGraph | Explicit conditional workflow with finite transitions; deterministic policy rather than an LLM-driven planner |
| React + FastAPI | Separate presentation from validated APIs; build assets once and serve same-origin locally |
| Extractive reports | Verbatim evidence and mechanically checkable citations; no generated synthesis or automated entailment judgment |

PostgreSQL/pgvector, neural embeddings, rerankers and LLM synthesis remain possible extensions. They were not installed or evaluated as part of this release. They should replace a measured limitation, not simply expand the dependency list.

## 3. Interfaces and data design

### Persistent entities

| Entity | Key fields and invariants |
| --- | --- |
| `documents` | Unique public document key, display title and current-version pointer |
| `versions` | Immutable byte digest, version title/label, source link, parser version and creation time |
| `chunks` | Immutable version ID, ordinal, body, heading and page or line range |
| `chunks_fts` | FTS5 body, heading and title with a passage identifier |
| `jobs` | Idempotency key/fingerprint, pinned versions, workflow state, status, attempts and timestamps |
| `events` | Monotonic event sequence, job ID, kind, payload and time |

Version identity is `(document_id, byte_digest, parser_version)`. A repeated import returns the existing version and does not reset the current pointer. This prevents an old file reimport from silently reverting the library. Label/URL-only edits to unchanged bytes also return the existing record; metadata editing is not currently a separate operation.

Each job pins current version IDs in the same write transaction that creates the job. A later document import cannot change that snapshot. Citation records retain version, document, digest, position and title. Viewing an old report calculates an outdated-source indicator against current pointers without rewriting its saved excerpts.

Markdown line ranges locate the source paragraph; a long paragraph split across chunks can share a range. PDF locations are page numbers. Whitespace is normalized during parsing, so excerpts are exact substrings of the stored parsed passage rather than byte-for-byte spans in the original PDF.

### HTTP contracts

| Endpoint | Contract |
| --- | --- |
| `POST /api/documents` | JSON: document key, title, text, version label, optional source URL |
| `POST /api/documents/upload` | Same metadata plus multipart file; filenames never become filesystem paths |
| `GET /api/documents` | Current versions and passage/version counts |
| `GET /api/documents/{id}/versions` | Version history |
| `GET /api/chunks/{id}` | Original passage and provenance, including current-version status |
| `POST /api/search` | Query, retrieval method and limit; current library snapshot |
| `POST /api/runs` | Question/method; optional `Idempotency-Key`; returns job ID with HTTP 202 |
| `GET /api/runs/{id}` | Status, saved state, report and current outdated-source indicators |
| `GET /api/runs/{id}/events` | SSE progress with persisted IDs; accepts cursor or `Last-Event-ID` |
| `POST /api/runs/{id}/retry` | Requeue a failed run from its saved stage |
| `GET /api/runs/{id}/report.md` | Completed report as downloadable Markdown |
| `POST /api/examples` | Idempotently import the included authored notes |

Invalid input is rejected with HTTP 400/422, missing records with 404, conflicting keys/transitions with 409. Unknown JSON fields are rejected. Reusing a request key with different question/method parameters conflicts; reusing it after a source update still returns the original run. A fresh run requires a new key.

## 4. Implementation

### Parsing and ingestion

Imports are capped at 5 MB, 100 PDF pages and 500 passages per document. The library supports 100 documents and 4000 stored passages, including history. Paragraphs retain section headings and are split at word boundaries near 1200 characters. Empty text, NULs, unsupported types and unreadable PDFs are rejected. OCR and encrypted PDFs are not supported.

A write transaction inserts the version, passages and FTS entries, then moves the current pointer. Any failure rolls back the entire import. PDF extraction and storage run off the async HTTP event loop. Uploaded bytes are not written to user-supplied paths and document text is never executed.

### Retrieval

BM25 uses FTS5 with Porter stemming. User queries are tokenized and quoted rather than passed through as FTS expressions or SQL. Character TF-IDF uses 3–5 character word-boundary n-grams, sublinear term frequency, up to 40,000 features and cosine similarity. A small similarity cutoff removes negligible vector matches.

Each channel supplies up to 30 candidates. Fusion sums `1 / (60 + rank)` across channels, then applies a deterministic passage-ID tie-break. The API returns the requested top 1–12 results with channel ranks. A four-entry LRU cache stores vector indexes by immutable version snapshot; updates cannot serve an index under the wrong snapshot key.

The query-term tokenizer currently targets English/Latin technical tokens. Importing text in another script does not establish multilingual retrieval support.

The vector index is fitted in memory and rebuilt after a process restart. FTS is persisted. This is appropriate for the declared corpus bound; a much larger library needs a different indexing/storage design rather than an unrestricted increase to limits.

### Bounded research policy

Planning accepts up to three semicolon-separated subquestions or heuristically splits a marked comparison. Retrieval gathers up to six candidates per query, capped at 24 evidence passages. Assessment looks for sentences sharing informative query terms; missing evidence triggers at most one keyword follow-up. This is a lexical sufficiency heuristic, not a proof of relevance or entailment.

Composition selects up to four exact excerpts per subquestion. It verifies quoted text and all citation metadata against the pinned chunks. Results are `evidence_found`, `partial_evidence` or `insufficient_evidence`. A valid quote establishes provenance, not factual truth or that the source answers every aspect of a question.

### State, concurrency and recovery

Job states are `queued → running → succeeded/failed`. A unique request-key constraint and immediate write transaction make creation and queue claiming atomic. One database lock file prevents two worker processes from recovering or processing the same queue. HTTP readers and writes use separate SQLite connections with WAL, foreign keys and a busy timeout.

Each node computes from its input snapshot, then commits the resulting state and progress event together. A crash before commit reruns that node; a crash after commit resumes at the next node. A completed final checkpoint can be finalized after restart without regenerating the report. This is safe replay of local deterministic operations, not a blanket exactly-once guarantee for future external side effects.

Transient SQLite operation failures receive at most two automatic requeues. Permanent failures become visible failed runs with a manual retry action. Workflow-version mismatch requires an explicit migration; it is not silently resumed with changed semantics. The present process has one job worker and no distributed cancellation or lease system.

## 5. Testing and validation

`python -m pytest -q` tests parsing, successful and unreadable PDFs, line/page positions, idempotent imports, rollback, version-scoped search, query escaping, deterministic ranks, bounded research, exact citations, fabricated/misattributed evidence, concurrent request deduplication, restart replay, transactional checkpoint/event rollback, retry budgets, worker locks, APIs and local-origin checks.

The browser smoke test operates on the real built frontend and API. It exercises research, source drawers, version import and outdated-evidence warnings, keyword search, no-evidence answers, run history and mobile overflow checks. Screenshots are inspected separately from automated assertions.

The 23 November suite passes 44 pytest cases with 83% Python line coverage across the `sourcelens` package. Coverage includes CLI/evaluation modules that are also exercised separately through actual runs; it is not a measure of retrieval relevance or a substitute for browser checks.

`python -m sourcelens.evaluate` compares retrieval methods on the same authored corpus with declared development/test labels. It records corpus/case hashes, runtime versions, Recall@6, MRR@6, abstention, exact-quote verification and measured retrieval latency. See [Evaluation](EVALUATION.md). This small sanity set does not establish general retrieval performance or a hybrid-method advantage.

## 6. Deployment and operations

Install from an editable checkout, build `web/` with `npm ci && npm run build`, then run `python -m sourcelens`. The CLI binds only `127.0.0.1`; `--port` and `--database` select local resources. Do not start multiple Uvicorn workers against the same queue. Frontend development can use Vite's local API proxy.

The worker's `fcntl` process lock targets macOS/Linux. A Windows lock adapter has not been implemented.

`GET /api/health` reports API status, answer mode, retrieval options and worker state. Exceptions are logged locally with job identifiers; question text is not added to the application log. Persisted events expose stage progress. LangSmith tracing is explicitly disabled around graph invocation even if the shell enables global tracing.

The server validates Host, rejects cross-origin browser writes and applies a same-origin content security policy. React escapes imported text; source links only accept HTTP(S) without embedded credentials. Body size is bounded while receiving requests, not only by a declared Content-Length.

Data is local but not encrypted. This release has no authentication and is not intended for network exposure. Text/PDF parsing is bounded but not an operating-system sandbox; use trusted local documents. For backups, stop the process and preserve the database before restarting; do not copy only a live SQLite main file while ignoring its WAL. The repository excludes databases, uploaded-derived passages, caches and local outputs.

## 7. Limitations and next decisions

The next retrieval decision needs a larger, independently labeled corpus containing paraphrases, near-miss passages, multiple versions and genuinely unanswerable questions. Only then can neural embeddings or reranking be justified against BM25 and TF-IDF.

Generative synthesis would require a provider interface, explicit user-controlled model configuration, call/time/cost budgets and claim-level support evaluation. Citation existence must remain separate from support. Live provider behavior has not been tested or claimed here.

Public hosting would require authentication, isolated parsing, user-level data boundaries, backup/restore testing, durable worker leases and a production deployment model. Those are distinct requirements, not features inferred from the local prototype.

## Technical references

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api): graph state and conditional control flow.
- [LangGraph fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance): replay boundaries and idempotency considerations.
- [SQLite FTS5](https://www.sqlite.org/fts5.html): full-text indexing, query syntax and BM25.
- [FastAPI request files](https://fastapi.tiangolo.com/tutorial/request-files/): multipart upload interfaces.
