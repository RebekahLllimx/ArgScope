"""Self-contained, escaped review export and deterministic snapshot verification."""
from html import escape
import json
from workbench.contracts import canonical, digest
from .schemas import validate_draft
from .tool_worker import calculate, TOOL_VERSION


def export(report, trace):
    def portable(value):
        if isinstance(value, dict):
            return {k: portable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [portable(v) for v in value]
        if type(value) is float and value.is_integer():
            return int(value)
        return value

    body = portable(
        {
            "schema_version": "argument-review-export-v1",
            "report": report,
            "trace": trace,
        }
    )
    if len(canonical(body).encode()) > 2_000_000:
        raise ValueError("Review export exceeds 2 MB")
    return {**body, "export_hash": digest(body)}


def verify(bundle):
    if (
        set(bundle) != {"schema_version", "report", "trace", "export_hash"}
        or bundle["schema_version"] != "argument-review-export-v1"
    ):
        raise ValueError("Unknown review export")
    if (
        digest({k: v for k, v in bundle.items() if k != "export_hash"})
        != bundle["export_hash"]
    ):
        raise ValueError("Review export content mismatch")
    report = bundle["report"]
    validate_draft(report["draft"], report["request"])
    expected = calculate(
        {
            "operation": "formal",
            "request": report["request"],
            "draft": report["draft"],
            "human_review": report["human_review"],
            "tool_version": report.get("tool_version", "review-tools-v2"),
        }
    )["results"]
    if expected != report["formal_checks"]:
        raise ValueError("Formal results differ from independent recomputation")
    return {
        "status": "snapshot_verified",
        "live_source_checked": False,
        "reviewer_identity_authenticated": False,
        "live_model": report["provider"]["live_model"],
        "scope": "Content and deterministic recomputation only; no authentication of human judgments or philosophical truth.",
    }


def html(bundle):
    verify(bundle)
    report = bundle["report"]
    draft = report["draft"]

    def e(value):
        return escape(str(value), quote=True)

    parts = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'\"><title>ArgScope review</title><style>body{font:15px/1.65 system-ui;max-width:1000px;margin:auto;padding:30px;color:#173b35;background:#f7f8f2}article,section{background:white;border:1px solid #d8e3d9;padding:22px;margin:20px 0;border-radius:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere}blockquote{border-left:3px solid #9ab49c;margin:14px 0;padding-left:18px}.notice{background:#fff2d4;padding:16px}small{overflow-wrap:anywhere}h1{line-height:1.15}p{overflow-wrap:anywhere}</style>",
        f"<h1>{e(draft['title'])}</h1><p>{e(report['request']['question'])}</p>",
    ]
    if not report["provider"]["live_model"]:
        parts.append(
            "<p class='notice'><strong>DEMO — no live language model called.</strong> Deterministic authored/extractive rules generated this draft. Human judgments and tool execution are recorded separately.</p>"
        )
    parts.append(
        f"<p>Human review: <strong>{e(report['review_status'])}</strong> · {e(report['human_review']['reviewer'])}</p><p>{e(report['scope'])}</p>"
    )
    for source in report["request"]["sources"]:
        parts.append(
            f"<section><h2>Source: {e(source['title'])}</h2><p>{e(source['kind'])} · {e(source['version'])}</p><blockquote>{e(source['text'])}</blockquote></section>"
        )
    for reading in draft["readings"]:
        parts.append(
            f"<section><h2>{e(reading['id'])}</h2><p>{e(reading['summary'])}</p>"
        )
        for node in reading["nodes"]:
            parts.append(
                f"<article><h3>{e(node['id'])} · {e(node['role'])}</h3><p>{e(node['content'])}</p><p>{e(node['speaker'])} · {e(node['commitment'])}</p><p>Depends on: {e(', '.join(node['depends_on']) or 'none')}</p><p>{e(node['rationale'])}</p>"
            )
            for ref in node["source_refs"]:
                parts.append(
                    f"<blockquote>{e(ref['quote'])}</blockquote><small>{e(ref['source_id'])} · code points {ref['start']}–{ref['end']}</small>"
                )
            parts.append("</article>")
        parts.append(
            "<details><summary>Formal mapping and semantic qualifications</summary><pre>"
            + e(
                json.dumps(
                    {
                        "formal_mapping": reading["formal_mapping"],
                        "qualifications": reading["qualifications"],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            + "</pre></details></section>"
        )
    for title, value in (
        ("Current issues", report["current_issues"]),
        ("Issue history", report["issue_history"]),
        ("Human judgments", report["human_review"]),
        ("Formal checks", report["formal_checks"]),
        ("Execution trace", bundle["trace"]),
    ):
        parts.append(
            f"<section><h2>{e(title)}</h2><pre>{e(json.dumps(value,indent=2,ensure_ascii=False))}</pre></section>"
        )
    parts.append(
        f"<small>Export integrity: {e(bundle['export_hash'])}. Hashes do not authenticate author or reviewer identity.</small></html>"
    )
    return "".join(parts)
