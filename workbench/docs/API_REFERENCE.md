# Review API reference

This is the local review API implemented by `workbench/review_app.py`. Examples
assume the application is running on `http://127.0.0.1:8767`. Existing SourceLens
library routes remain available, but this document covers the argument-review
boundary. It is not a public authenticated service contract.

## Request handling

Review POSTs require `Content-Type: application/json`. Duplicate JSON keys,
nonfinite numbers and malformed JSON are rejected. Ordinary review POST bodies
are capped at 200,000 bytes; packet verification permits 2,000,000 bytes. The
SourceLens Host/Origin guards apply. Clients cannot select an executable, provider
URL, KV endpoint or database path through these payloads; those are operator
startup settings.

## Routes

| Method and path | Purpose / typical successful response |
| --- | --- |
| `GET /api/argument-health` | Framework, provider/live flag, KV PING and TTL status |
| `GET /api/argument-examples` | Mode-dependent authored controls and original-paper requests |
| `GET /api/argument-sources` | Up to 100 stored source/chunk choices with immutable binding metadata |
| `POST /api/argument-runs` | Freeze a validated request, probe shared KV and queue a run; 202 with `id` |
| `GET /api/argument-runs` | Up to 100 retained runs with derived status |
| `GET /api/argument-runs/{id}` | Frozen request, draft, digest, current/history issues, candidate checks, provider and events |
| `GET /api/argument-runs/{id}/events` | Persisted SSE progress with reconnect cursor |
| `POST /api/argument-runs/{id}/resume` | Submit content-bound reader judgments/edits; 202 |
| `POST /api/argument-runs/{id}/feedback` | Request the one extra revision on a paused v2 run; 202 |
| `GET /api/argument-runs/{id}/compare?before=HASH&after=HASH` | Two saved drafts and ID-aligned field differences; both references must belong to this run's committed history |
| `POST /api/argument-runs/{id}/retry` | Retry a failed/interrupted run within its existing limits; 202 |
| `POST /api/argument-runs/{id}/cancel` | Record cooperative cancellation; 202 |
| `POST /api/argument-validate` | Validate `{request, draft}` without accepting it as the run's human review |
| `POST /api/argument-verify` | Verify a complete exported packet and recompute its formal results |
| `GET /api/argument-runs/{id}/report.json` | Completed review packet with digest and trace |
| `GET /api/argument-runs/{id}/report.html` | Completed review as escaped script-free HTML attachment |

Health's `live_model` says which provider is configured. It does not prove a
particular call succeeded. Health PING is not a full KV-capability test; new-run
creation additionally exercises atomic artifact publication.

## Create a request

A minimal request is stored in [examples/request.json](examples/request.json):

```json
{
  "question": "Does the conclusion follow from these two premises?",
  "notes": "Project-authored API control, not an original-paper excerpt.",
  "sources": [{
    "id": "main",
    "title": "Authored implication control",
    "version": "v1",
    "kind": "authored_control",
    "text": "If p then q. p holds. Therefore q holds."
  }]
}
```

From the project root:

```bash
curl -sS http://127.0.0.1:8767/api/argument-runs \
  -H 'Content-Type: application/json' \
  --data-binary @workbench/docs/examples/request.json
```

`workflow_mode` accepts `single_draft` or `bounded_review`. The browser sends
`single_draft` by default; omitted API values retain `bounded_review` for older
clients. The single mode routes draft → deterministic audit → human interrupt,
without model critique/revision before that pause.

The 202 response contains a 32-character hexadecimal run ID. Poll its GET route
or open the corresponding browser task. A library-bound source additionally
contains `document_id`, `version_id`, `chunk_id` and `upload_sha256` under `binding`;
the server checks exact stored text/identities and uses authoritative metadata.

## Run states

| Status | Meaning |
| --- | --- |
| `queued` / `running` | Waiting for or executing the one review worker |
| `awaiting_review` | A genuine LangGraph human interrupt is paused |
| `interrupted` | Committed work exists without a complete report or current execution |
| `failed` | Execution failed; inspect error/events and retry only where permitted |
| `budget_exhausted` | Current limits were exhausted; retry is not a fresh budget |
| `cancelled` | A cancellation was requested/observed; later approval cannot continue it |
| `succeeded` | A report was assembled; inspect its review status and check outcomes |

`Succeeded` does not imply valid logic, accepted interpretation or premise truth.
A completed report may contain disputed judgments or unsupported checks.
Cancellation is cooperative; an in-flight provider/tool call need not stop at
that instant. Resume and duplicate-queue guards may return 409.

## Draft and mapping shape

[examples/draft.json](examples/draft.json) is the complete compatible authored
control. Nodes have `content`, `role`, `speaker`, `commitment`, exact source spans,
`depends_on` and `rationale`. A reading names an inference target. The schema
rejects extra fields, duplicate IDs, missing dependencies, cycles and incorrect
quote offsets. Formula dictionaries use node IDs:

```json
{
  "formulas": {"P1": "p -> q", "P2": "p", "C": "q"},
  "rationale": "p and q denote the two fixture propositions."
}
```

First-order mappings additionally require `semantics: classical-first-order-v1`.
Use `first_order` qualification representation with a scope rationale. An
`unencoded` qualification prevents an executable verdict for that reading.
See the [logic guide](FIRST_ORDER_GUIDE.md) for grammar and outcomes.

Candidate results have `human_approved: false`. They are returned only while the
saved audited draft matches the saved current draft. The UI separately hides
them while local edits differ. Validating JSON does not itself persist an edit
as human acceptance; edited content is submitted with the review.

## Resume with a reader judgment

Source 0.6.0 also supports a separate feedback body: `action: request_revision`,
`expected_draft`, `reviewer`, `feedback` (5–2000 characters, nonblank) and optional
`edited_draft`. Approval fields are rejected. Stale, busy, completed, legacy or
already-used feedback requests return 409; malformed input returns 422/400.
The run response includes `feedback_available`, `feedback_round`, `revision_feedback`
and `versions` (reference/origin/parent, plus feedback reference when applicable).
Comparison returns 404 for a version not in that run's history. Local unsaved
edits are excluded; comparison is read-only. See the full
[feedback/version contract](ENGINEERING_DESIGN.md#reader-feedback-and-version-history).

A resume body contains:

| Field | Requirement |
| --- | --- |
| `expected_draft` | Current 64-character digest from the run response |
| `reviewer` | Reader-supplied label; no identity authentication |
| `rationale` | Public reason for the judgments, at least five characters |
| `fidelity` | `accepted` or `disputed` |
| `attribution` | `accepted` or `disputed` |
| `interpretation` | `accepted` or `disputed` |
| `approve_formal_mapping` | Explicit Boolean; default false |
| `approve_formula_scope` | Separate Boolean; default false; required for declared-formula scope |
| `edited_draft` | Optional complete validated draft |

The reader must make these choices. Do not auto-fill acceptance from a model's
critique or a solver result. Stale content is rejected. Edited content is archived
under its actual digest and the judgments attach to that version. Final formal
execution under the legacy full-reading gate requires all three judgments
accepted and mapping approval true. A reading may instead supply
`formal_scope: {mode: "declared_formulas", rationale: "..."}` with at least 20
nonblank characters. Both approval Booleans are then required; interpretation
may remain disputed. The result preserves `excluded_qualifications` and labels
its limited scope. No scope bypasses structural or formula validation.
New reports carry `tool_version: "review-tools-v3"`; legacy packets without it
recompute using v2, which does not accept the new scope.

## Progress and export

The events endpoint emits `progress` records with persisted IDs, supports the
`Last-Event-ID` reconnect header and emits `settled` for a non-running state.
Connections are bounded to roughly 50 seconds and may be reopened. The current
view reads at most 200 events for a run; it is not an unbounded log export API.

Report export before completed review returns 409. JSON uses
`argument-review-export-v1` with `report`, `trace` and `export_hash`. Verification
returns `snapshot_verified` only after content and formal recomputation checks;
`live_source_checked` and `reviewer_identity_authenticated` remain false.

## Error categories

| Code | Typical cause |
| --- | --- |
| 400 | Duplicate/malformed JSON, invalid draft/reference or verification failure |
| 404 | Invalid or unknown run identity |
| 409 | Wrong lifecycle state, stale review, concurrent queue or unavailable final report |
| 413 | Body exceeds the route's cap |
| 415 | Review POST did not use JSON content type |
| 422 | Request-model validation failure |
| 503 | New-run shared KV unavailable/incompatible, or required built page absent |

Model execution failures are also retained in the run's error/events after an
accepted asynchronous creation response. Inspect the run rather than assuming
HTTP 202 means generation completed. Implementation-specific exceptions outside
these guarded paths may still be server errors.
