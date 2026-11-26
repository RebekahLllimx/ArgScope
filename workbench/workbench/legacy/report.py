"""Legacy standalone, escaped, script-free reconstruction reports."""
from html import escape
import json

from .reconstruction import replay_reconstruction


def e(value):
    return escape(str(value), quote=True)


def table(headers, rows):
    if not rows:
        label = headers[0].split(" / ")[0].lower()
        return f"<p class='empty'>No {e(label)} records declared.</p>"
    return (
        "<div class='table-scroll' tabindex='0' role='region' aria-label='Table: "
        + e(headers[0])
        + "'><table><thead><tr>"
        + "".join(f"<th scope='col'>{e(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>" for row in rows
        )
        + "</tbody></table></div>"
    )


def badge(value):
    tone = {
        "witness_found": "teal",
        "no_counterexample_complete": "teal",
        "self_reviewed": "teal",
        "review_required": "amber",
        "unsupported_semantics": "amber",
        "disputed": "red",
        "blocked": "red",
    }.get(value, "neutral")
    return f"<span class='badge {tone}'>{e(value.replace('_', ' '))}</span>"


def render(bundle, source_db=None):
    verification = replay_reconstruction(bundle, source_db)
    doc = bundle["document"]
    source = doc["source"]
    source_check = verification["source_checks"]
    if source_check:
        source_status = "Stored source verified · " + (
            "current version" if source_check[0]["is_current"] else "historical version"
        )
    else:
        source_status = "Frozen snapshot · library not checked"
    ref = source["reference_url"]
    reference = (
        f"<a href='{e(ref)}' rel='noreferrer' referrerpolicy='no-referrer'>Original reference ↗</a>"
        if ref
        else "No external reference"
    )
    passage = source.get("passage")
    position = ""
    if passage:
        position = table(
            ("Field", "Value"),
            [
                (k, passage[k])
                for k in (
                    "document_id",
                    "version_id",
                    "chunk_id",
                    "start",
                    "end",
                    "page",
                    "line_start",
                    "line_end",
                    "parser_version",
                )
            ],
        )
    navigation = "".join(
        f"<a href='#reading-{i}'>{e(r['reading_id'])}</a>"
        for i, r in enumerate(bundle["results"], 1)
    )
    cards = []
    for i, result in enumerate(bundle["results"], 1):
        reading = doc["readings"][i - 1]
        outcome = result["analysis"]["result"]
        semantic = result.get("semantic_scope")
        review = "".join(
            f"<div><span>{e(aspect)}</span>{badge(decision)}</div>"
            for aspect, decision in result["review_status"].items()
        )
        nodes = table(
            ("Node / role", "Formula", "Speaker / commitment", "Scope", "Uses"),
            [
                (
                    n["id"] + " / " + n["role"],
                    n["formula"],
                    n["speaker"] + " / " + n["commitment"],
                    n["scope"],
                    ", ".join(n["depends_on"]) or "—",
                )
                for n in reading["nodes"]
            ],
        )
        annotations = ""
        if semantic:
            annotations += (
                "<div class='scope-note'><strong>"
                + e(semantic["level"].replace("_", " "))
                + "</strong><p>"
                + e(semantic["scope"])
                + "</p></div>"
            )
            if semantic["unencoded_qualifications"]:
                annotations += (
                    "<p class='notice'>Not executable: "
                    + e(", ".join(semantic["unencoded_qualifications"]))
                    + ". The missing semantics were not flattened into free atoms.</p>"
                )
            if semantic["unresolved_occurrences"]:
                annotations += (
                    "<p class='notice'>Unresolved occurrences: "
                    + e(", ".join(semantic["unresolved_occurrences"]))
                    + ". Choose and justify a reading before execution.</p>"
                )
            annotations += "<h3>Terms and proposed senses</h3>"
            annotations += table(
                (
                    "Occurrence / node",
                    "Selected text",
                    "Range",
                    "Context",
                    "Proposed sense",
                ),
                [
                    (
                        o["id"] + " / " + o["node_id"],
                        o["surface"],
                        f"[{o['start']}, {o['end']})",
                        o["context"],
                        o["sense_id"] or "UNRESOLVED",
                    )
                    for o in reading["occurrences"]
                ],
            )
            annotations += table(
                ("Sense", "Description", "Grounds"),
                [
                    (s["id"] + " / " + s["label"], s["description"], s["grounds"])
                    for s in reading["senses"]
                ],
            )
            annotations += "<h3>Qualifications and bridge assumptions</h3>"
            annotations += table(
                (
                    "Qualification / node",
                    "Kind",
                    "Representation",
                    "Description / rationale",
                ),
                [
                    (
                        q["id"] + " / " + q["node_id"],
                        q["kind"],
                        q["representation"],
                        q["description"]
                        + (
                            " · " + q["abstraction_rationale"]
                            if q["abstraction_rationale"]
                            else ""
                        ),
                    )
                    for q in reading["qualifications"]
                ],
            )
            annotations += table(
                ("Bridge / node", "Rationale", "Source basis"),
                [
                    (b["id"] + " / " + b["node_id"], b["rationale"], b["source_basis"])
                    for b in reading["bridges"]
                ],
            )
        witness = ""
        if outcome.get("assignment") is not None:
            witness = "<h3>Verified formal witness</h3>" + table(
                ("Atom", "Assignment"),
                [
                    (key, str(value).lower())
                    for key, value in outcome["assignment"].items()
                ],
            )
            witness += "<p>Every supplied premise evaluates to true; the conclusion evaluates to false. This concerns the supplied formulas.</p>"
        if outcome.get("blocked_by"):
            witness += (
                "<p class='notice'>Blocked by: "
                + e(", ".join(outcome["blocked_by"]))
                + "</p>"
            )
        relevant_reviews = [
            r for r in doc["reviews"] if r["reading_id"] == reading["id"]
        ]
        review_records = table(
            ("Aspect", "Decision", "Reviewer", "Applies to this content", "Rationale"),
            [
                (
                    r["aspect"],
                    r["decision"],
                    r["reviewer"],
                    str(r["target_hash"] == result["reading_hash"]).lower(),
                    r["rationale"],
                )
                for r in relevant_reviews
            ],
        )
        cards.append(
            f"""<section class='reading' id='reading-{i}'>
<div class='reading-head'><p class='eyebrow'>READING {i:02d} · {e(reading['origin'])}</p>{badge(outcome['outcome'])}</div>
<h2>{e(reading['id'])}</h2><p class='summary'>{e(reading['summary'])}</p>
<div class='reviews' aria-label='Review status'>{review}</div>
<h3>Argument reconstruction</h3><p class='table-hint'>Scroll within tables to inspect all columns.</p>{nodes}{annotations}{witness}
<details><summary>Review history and applicability</summary>{review_records}</details>
<details><summary>Complete result and verification trace</summary><pre>{e(json.dumps(result, indent=2, ensure_ascii=False))}</pre></details>
</section>"""
        )
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<meta http-equiv='Content-Security-Policy' content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<meta name='referrer' content='no-referrer'><title>{e(doc['reconstruction_id'])} · ArgScope review</title>
<style>{CSS}</style></head><body>
<a class='skip' href='#readings'>Skip to readings</a>
<header><a class='brand' href='#top'>A<span>↗</span> ArgScope</a><span>RESEARCH WORKBENCH / REVIEW REPORT</span></header>
<main id='top'><div class='hero'><p class='eyebrow'>FROM PASSAGE TO CLAIM</p><h1>Read the source.<br>Inspect the reasoning.</h1>
<p>A frozen research record with explicit interpretations, review judgments and reproducible checks.</p></div>
<div class='record-bar'><strong>{e(doc['reconstruction_id'])}</strong><span>{len(cards)} explicit reading(s)</span><span>Snapshot replay verified</span></div>
<div class='workspace'><aside class='source'><p class='eyebrow'>SOURCE SNAPSHOT</p><h2>{e(source['key'])}</h2>
<p class='metadata'>{e(source['kind'])} · {e(source['version'])}</p><div class='source-text'>{e(source['text'])}</div>
<p>{e(source_status)}</p><p class='metadata'>{e(source['locator'])}</p><p>{reference}</p>
<details><summary>Passage identity</summary>{position}<p class='hash'>{e(bundle['revision_hash'])}</p></details>
<nav aria-label='Readings'>{navigation}</nav></aside><div id='readings'>{''.join(cards)}</div></div>
<footer><strong>Scope of this report</strong><p>{e(bundle['scope'])}. Matching outcome labels do not establish equivalent meanings.</p>
<p>This report is read-only. Review records are supplied judgments, not authenticated expert approval. No remote assets or scripts are loaded.</p></footer></main></body></html>"""


CSS = """
:root{color-scheme:light;--ink:#173331;--muted:#657773;--line:#d8e2de;--teal:#14695f;--paper:#f4f5ef}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
a{color:var(--teal);text-underline-offset:3px}header{border-bottom:1px solid var(--line);padding:20px 4vw;display:flex;align-items:center;justify-content:space-between;gap:20px;background:#fafbf7}
header>span{font-size:10px;letter-spacing:1.5px;color:var(--muted)}.brand{font-size:20px;font-weight:750;text-decoration:none;color:var(--ink)}.brand span{color:var(--teal);margin:0 8px}
main{max-width:1560px;margin:auto;padding:0 4vw 40px}.hero{padding:45px 0 30px;max-width:850px}.eyebrow{font-size:10px;letter-spacing:1.7px;font-weight:750;color:var(--muted);margin:0 0 12px;text-transform:uppercase}
h1{font-size:clamp(34px,4vw,52px);line-height:1.1;letter-spacing:-1.8px;font-weight:650;margin:0 0 20px}h2{font-size:22px;letter-spacing:-.5px;line-height:1.3;margin:10px 0;overflow-wrap:anywhere}h3{font-size:14px;margin:26px 0 10px}
.hero>p:last-child{color:var(--muted);max-width:610px}.record-bar{border-block:1px solid var(--line);display:flex;flex-wrap:wrap;gap:12px 30px;padding:15px 0;margin-bottom:26px;font-size:12px}.record-bar>span{color:var(--muted)}
.workspace{display:grid;grid-template-columns:minmax(240px,340px) minmax(0,1fr);gap:26px;align-items:start}.workspace>*{min-width:0}.source{position:sticky;top:20px;padding:25px;background:#e8eee5;border:1px solid #d5dfd1;border-radius:4px;overflow-wrap:anywhere}.source-text{white-space:pre-wrap;font:19px/1.65 Georgia,serif;margin:25px 0;padding-left:17px;border-left:2px solid #7f9f8f}.metadata,.hash{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.source nav{display:grid;gap:8px;border-top:1px solid #cdd8c9;padding-top:17px;margin-top:20px;font-size:12px}
.reading{background:#fff;padding:28px;border:1px solid var(--line);border-radius:4px;margin-bottom:24px;scroll-margin-top:20px;min-width:0}.reading-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}.reading-head>.eyebrow{margin:0}.summary{color:var(--muted)}
.badge{display:inline-block;border:1px solid #d5ddda;border-radius:30px;font-size:10px;padding:4px 10px;color:var(--muted);background:#f4f6f4;white-space:normal}.teal{background:#e6f3ec;color:#21674e;border-color:#c4e1d0}.amber{background:#fff3da;color:#805c20;border-color:#ead9b3}.red{background:#f9e9e5;color:#8b4437;border-color:#ebcfc7}.empty{font-size:11px;color:var(--muted);padding:8px 0}.table-hint{display:none}
.reviews{display:flex;flex-wrap:wrap;gap:10px 22px;padding:15px 0;border-block:1px solid #edf0ed;font-size:11px}.reviews>div{display:flex;align-items:center;gap:8px}.table-scroll{max-width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:11px;text-align:left}th{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.8px;background:#f5f7f3}th,td{vertical-align:top;padding:11px 9px;border-bottom:1px solid #e7ece7;overflow-wrap:anywhere}td{min-width:70px;max-width:300px}.scope-note{background:#eff4ef;border-left:3px solid #8aab99;padding:12px 16px;margin-top:24px;font-size:11px}.scope-note p{margin:4px 0}.notice{background:#fff6e6;padding:12px 16px;font-size:12px;border-left:3px solid #d8b465}
details{border-top:1px solid var(--line);padding-top:14px;margin-top:18px}summary{cursor:pointer;font-size:11px;font-weight:650}pre{font:10px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8f4;padding:16px;max-height:600px;overflow:auto}footer{border-top:1px solid var(--line);padding:22px 0;font-size:11px;color:var(--muted);max-width:900px}.skip{position:absolute;left:-9999px}.skip:focus{left:12px;top:12px;background:white;padding:10px;z-index:2}
@media(max-width:850px){.workspace{grid-template-columns:minmax(0,1fr)}.source{position:static}.hero{padding-top:30px}.reading{padding:20px}header>span{max-width:150px;text-align:right}main{padding-inline:20px}.record-bar{gap:10px}.source-text{font-size:18px}table{min-width:560px;font-size:12px}.table-hint{display:block;font-size:11px;color:var(--muted)}}
@media print{header{padding:12px 0}.workspace{display:block}.source{position:static}.reading{break-inside:avoid}.hero{padding:20px 0}details:not([open]){display:none}main{padding:0}body{background:white}}
"""
