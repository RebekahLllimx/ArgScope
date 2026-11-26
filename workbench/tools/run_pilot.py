"""Freeze, plan, run and score the paired pilot. Model execution is opt-in."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import time

from workbench.review.provenance import encoded, environment, object_hash, sha, source_snapshot
from workbench.review.providers import Critique, DemoProvider
from workbench.review.schemas import Request, validate_draft, issue
from workbench.review.tool_worker import calculate, TOOL_VERSION

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "workbench/studies/pilot-v1.json"
CATEGORIES = {"attribution", "unlabelled_assumption", "qualification", "source_support", "formal_mapping", "structure"}


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def freeze(output, quine, chalmers):
    from pypdf import PdfReader
    config = read(CONFIG)
    sources = {"quine": Path(quine), "chalmers": Path(chalmers)}
    readers = {k: PdfReader(v) for k, v in sources.items()}
    cases = []
    for spec in config["cases"]:
        text = " ".join(readers[spec["source"]].pages[spec["page"] - 1].extract_text().split())
        start = text.index(spec["start"])
        end = text.index(spec["end"], start) + len(spec["end"])
        excerpt = text[start:end]
        request = Request.model_validate({"question": config["question"], "notes": "", "sources": [{
            "id": "main", "title": "Quine — On What There Is" if spec["source"] == "quine" else "Chalmers — The Singularity",
            "kind": "original_excerpt", "version": "pilot-v1", "text": excerpt,
        }]}).model_dump(mode="json")
        cases.append({"id": spec["id"], "request": request, "request_sha256": object_hash(request),
                      "text_sha256": sha(excerpt.encode()), "pdf_sha256": sha(sources[spec["source"]].read_bytes()),
                      "pdf_page": spec["page"], "selection_sha256": object_hash(spec)})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    corpus = {"protocol": config["protocol"], "config_sha256": object_hash(config),
              "extractor": "pypdf whitespace-collapse only; no spelling/dehyphenation correction",
              "extraction_environment": environment(), "source_transcription_review": "pending human comparison with PDF",
              "cases": cases}
    write(output / "corpus.json", corpus)
    # Public manifest omits source text, local paths and answer hints.
    write(output / "corpus-manifest.json", {k: v for k, v in corpus.items() if k != "cases"} | {
        "cases": [{k: v for k, v in c.items() if k != "request"} for c in cases]})
    return corpus


def load_corpus(path):
    corpus = read(path)
    config = read(CONFIG)
    if corpus["config_sha256"] != object_hash(config) or [c["id"] for c in corpus["cases"]] != [c["id"] for c in config["cases"]]:
        raise ValueError("Corpus/config mismatch; freeze a new protocol rather than silently replacing inputs")
    for c in corpus["cases"]:
        Request.model_validate(c["request"])
        if c["request_sha256"] != object_hash(c["request"]) or c["text_sha256"] != sha(c["request"]["sources"][0]["text"].encode()):
            raise ValueError("Frozen input hash mismatch")
    return corpus, config


def plan(corpus_path):
    corpus, config = load_corpus(corpus_path)
    order = [c["id"] for c in corpus["cases"]]
    random.Random(config["seed"]).shuffle(order)
    return {"protocol": config["protocol"], "cases": order, "arms": ["A1", "A2", "A3", "A4-human-pending"],
            "maximum_model_calls": len(order) * 5, "model_calls_made": 0,
            "corpus_sha256": object_hash(corpus), "status": "plan_only"}


def tool_feedback(request, draft):
    audit = calculate({"operation": "audit", "request": request, "draft": draft,
                       "human_review": None, "tool_version": TOOL_VERSION})
    feedback = deepcopy(audit["issues"])
    for candidate in audit["candidate_checks"]:
        small = {k: candidate[k] for k in ["outcome", "reason", "assignment", "finite_model", "scope"] if k in candidate}
        # A countermodel can indicate a bad reconstruction OR a substantive gap.
        # The feedback requests inspection, never automatic validity repair.
        feedback.append(issue("candidate_check", candidate["reading_id"], "", json.dumps(small), "human"))
    return audit, feedback


def run(corpus_path, output, provider, model, fixture=False):
    corpus, config = load_corpus(corpus_path)
    if not model or model == "client-default":
        raise ValueError("Use an explicit model identifier (aliases remain mutable; see protocol)")
    schedule = plan(corpus_path)
    if schedule["maximum_model_calls"] > config["maximum_model_calls"]:
        raise ValueError("Protocol call budget exceeded")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    before = source_snapshot(ROOT)
    provenance = {"schema": "argscope-experiment-v1", "started_utc": datetime.now(timezone.utc).isoformat(),
                  "source": before, "environment": environment(), "prompt_version": config["prompt_version"],
                  "prompt_source": {k: v for k, v in before["files"].items() if k.endswith(("review/bailian.py", "review/codex_provider.py", "review/providers.py", "tools/run_pilot.py"))},
                  "provider": provider.name, "model_requested": model, "model_snapshot_returned": None,
                  "model_snapshot_limit": "Adapters do not persist an immutable server model revision; explicit aliases can change",
                  "config": config, "config_sha256": object_hash(config), "corpus_sha256": object_hash(corpus),
                  "fixture_only": fixture, "schedule": schedule, "evidence_context": [],
                  "human_review": "pending; no application approval issued"}
    write(output / "provenance.json", provenance)
    artifacts, calls = [], []
    status, error = "completed_awaiting_human", None
    start = time.monotonic()

    def call(case, arm, operation, request, draft=None, issues=None):
        if len(calls) >= config["maximum_model_calls"]:
            raise ValueError("Call budget exhausted")
        payload = {"operation": operation, "request": request, "draft": draft, "issues": issues, "evidence": []}
        base = f"{len(calls)+1:02d}-{case}-{arm}-{operation}"
        write(output / (base + ".input.json"), payload)
        entry = {"id": base, "input_sha256": object_hash(payload), "status": "attempted"}
        calls.append(entry)
        usage_start = len(getattr(provider, "usage", []))
        tick = time.monotonic()
        try:
            result = provider.generate(**deepcopy(payload))
            write(output / (base + ".raw.json"), result)
            entry["raw_sha256"] = object_hash(result)
            if operation == "critique":
                result = Critique.model_validate(result).model_dump(mode="json")
                required = {"id", "kind", "reading_id", "node_id", "message", "action", "status"}
                if any(not isinstance(i, dict) or set(i) != required or i["action"] not in ("revise", "human") or i["status"] != "open" for i in result["issues"]):
                    raise ValueError("Malformed critique issue")
            else:
                result, _ = validate_draft(result, request)
            entry["status"] = "validated"
            return result
        except Exception as exc:
            entry.update(status="failed", error=str(exc))
            raise
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - tick, 6)
            entry["usage"] = deepcopy(getattr(provider, "usage", [])[usage_start:])
            write(output / (base + ".receipt.json"), entry)

    def save(case, arm, value):
        filename = f"{case}-{arm}.json"
        write(output / filename, value)
        artifacts.append({"case": case, "arm": arm, "file": filename, "sha256": object_hash(value)})

    try:
        by_id = {c["id"]: c for c in corpus["cases"]}
        for index, case_id in enumerate(schedule["cases"]):
            request = by_id[case_id]["request"]
            initial = call(case_id, "A1", "draft", request)
            save(case_id, "A1", initial)
            # Alternate order to reduce systematic warm-cache/time-order effects.
            for arm in (["A2", "A3"] if index % 2 == 0 else ["A3", "A2"]):
                feedback = []
                if arm == "A3":
                    audit, feedback = tool_feedback(request, initial)
                    write(output / f"{case_id}-{arm}-audit.json", audit)
                critique = call(case_id, arm, "critique", request, initial, feedback)
                merged = {i["id"]: i for i in feedback}
                for i in critique["issues"]:
                    merged.setdefault(i["id"], i)
                revised = call(case_id, arm, "revise", request, initial, list(merged.values()))
                save(case_id, arm, revised)
    except Exception as exc:
        status, error = "failed_no_retry", str(exc)
    after = source_snapshot(ROOT)
    result = {"status": status, "error": error, "fixture_only": fixture, "provenance_sha256": object_hash(provenance),
              "source_after_sha256": after["sha256"], "source_unchanged": before["sha256"] == after["sha256"],
              "calls": calls, "model_calls": 0 if fixture else len(calls), "fixture_calls": len(calls) if fixture else 0,
              "elapsed_seconds": round(time.monotonic() - start, 6), "artifacts": artifacts}
    write(output / "result.json", result)
    write(output / "ratings-template.json", {"reviewer": None, "reviewer_role": None, "independent_expert": False,
          "transcription_checked_against_pdf": False, "status": "pending", "run_result_sha256": object_hash(result),
          "ratings": [{**a, "issues": None} for a in artifacts],
          "human_edits": [{"case": c, "active_seconds": None, "reviewed_a3_sha256": next((a["sha256"] for a in artifacts if a["case"] == c and a["arm"] == "A3"), None),
                           "decision": None, "edited_draft_file": None, "residual_issues": None} for c in schedule["cases"]],
          "reviewed_on": None, "rubric_version": "pilot-v1", "built_by_reviewer": None,
          "blinding": "unblinded unless explicitly documented", "tool_observations": []})
    return result


def score(run_dir, ratings_path, corpus_path):
    run_dir = Path(run_dir)
    result, ratings = read(run_dir / "result.json"), read(ratings_path)
    corpus, _ = load_corpus(corpus_path)
    provenance = read(run_dir / "provenance.json")
    if object_hash(provenance) != result["provenance_sha256"] or provenance["corpus_sha256"] != object_hash(corpus):
        raise ValueError("Run provenance/corpus mismatch")
    if result["fixture_only"] or result["status"] != "completed_awaiting_human" or not result["source_unchanged"]:
        raise ValueError("Score only complete real runs with unchanged source; fixtures are not evidence")
    if ratings["status"] != "completed" or not ratings["reviewer"] or not ratings["reviewer_role"] or ratings["transcription_checked_against_pdf"] is not True or not ratings.get("reviewed_on") or ratings.get("rubric_version") != "pilot-v1" or type(ratings.get("built_by_reviewer")) is not bool:
        raise ValueError("Complete reviewer identity, transcription check and ratings first")
    if ratings["independent_expert"] is not False:
        raise ValueError("This single-reviewer pilot does not certify independent expert review")
    if ratings["run_result_sha256"] != object_hash(result):
        raise ValueError("Ratings belong to another result")
    expected = {(a["case"], a["arm"]): a for a in result["artifacts"]}
    rows = ratings["ratings"]
    if len(rows) != len(expected) or {(r["case"], r["arm"]) for r in rows} != set(expected):
        raise ValueError("Rate each artifact exactly once")
    summary, ids = [], {}
    for r in rows:
        a = expected[(r["case"], r["arm"])]
        if r["file"] != a["file"] or r["sha256"] != a["sha256"] or object_hash(read(run_dir / a["file"])) != a["sha256"]:
            raise ValueError("Rated artifact hash mismatch")
        if not isinstance(r["issues"], list):
            raise ValueError("Missing ratings are not zero errors")
        found = set()
        for i in r["issues"]:
            if not i.get("id") or i["id"] in found or i.get("category") not in CATEGORIES or i.get("severity") not in ("minor", "major") or not i.get("rationale") or not i.get("anchor"):
                raise ValueError("Every issue needs unique ID, category, severity, source/node anchor and reason")
            found.add(i["id"])
        ids[(r["case"], r["arm"])] = found
        summary.append({"case": r["case"], "arm": r["arm"], "errors": len(found),
                        "major": sum(i["severity"] == "major" for i in r["issues"]),
                        "minor": sum(i["severity"] == "minor" for i in r["issues"]),
                        "by_category": {c: sum(i["category"] == c for i in r["issues"]) for c in sorted(CATEGORIES)}})
    requests = {c["id"]: c["request"] for c in corpus["cases"]}
    edits = ratings["human_edits"]
    if len(edits) != len(requests) or {e["case"] for e in edits} != set(requests):
        raise ValueError("Record human review of each A3 exactly once")
    human = []
    for e in edits:
        a = expected[(e["case"], "A3")]
        if e["reviewed_a3_sha256"] != a["sha256"] or type(e["active_seconds"]) not in (int, float) or not 0 <= e["active_seconds"] < float("inf") or e["decision"] not in ("edited", "no_changes"):
            raise ValueError("Human edit needs exact A3 hash, measured active time and explicit decision")
        original = read(run_dir / a["file"])
        edited = read(Path(ratings_path).parent / e["edited_draft_file"]) if e["decision"] == "edited" else original
        edited, _ = validate_draft(edited, requests[e["case"]])
        residual = e.get("residual_issues")
        if not isinstance(residual, list):
            raise ValueError("A4 residual issues must be explicitly rated; missing is not zero")
        residual_ids = set()
        for i in residual:
            if not i.get("id") or i["id"] in residual_ids or i.get("category") not in CATEGORIES or i.get("severity") not in ("minor", "major") or not i.get("rationale") or not i.get("anchor"):
                raise ValueError("Malformed A4 residual issue")
            residual_ids.add(i["id"])
        # Mechanical leaf differences describe edit volume, not semantic quality.
        def leaves(v, path=""):
            if isinstance(v, dict):
                return {p: x for k, item in v.items() for p, x in leaves(item, path + "/" + k).items()}
            if isinstance(v, list):
                return {p: x for k, item in enumerate(v) for p, x in leaves(item, path + "/" + str(k)).items()}
            return {path: v}
        old, new = leaves(original), leaves(edited)
        human.append({"case": e["case"], "active_seconds": e["active_seconds"], "edited_sha256": object_hash(edited),
                      "residual_issues": residual, "residual_errors": len(residual),
                      "changed_leaf_fields": sum(k not in old or k not in new or old[k] != new[k] for k in old.keys() | new.keys())})
    transitions = [{"case": c, "arm": arm, "repaired_ids": sorted(ids[(c, "A1")] - ids[(c, arm)]),
                    "new_ids": sorted(ids[(c, arm)] - ids[(c, "A1")])} for c in requests for arm in ["A2", "A3"]]
    return {"reviewer": ratings["reviewer"], "reviewer_role": ratings["reviewer_role"], "independent_expert": False,
            "reviewed_on": ratings["reviewed_on"], "rubric_version": ratings["rubric_version"],
            "built_by_reviewer": ratings["built_by_reviewer"], "blinding": ratings.get("blinding"),
            "tool_observations": ratings.get("tool_observations", []),
            "result_sha256": object_hash(result), "ratings_sha256": object_hash(ratings), "scores": summary,
            "transitions": transitions, "human_edits": human, "scope": "Four selected passages; descriptive single-reviewer pilot, no general accuracy or framework-necessity claim"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    f = sub.add_parser("freeze")
    for name in ["quine-pdf", "chalmers-pdf", "output"]:
        f.add_argument("--" + name, required=True, type=Path)
    q = sub.add_parser("plan"); q.add_argument("--corpus", required=True, type=Path)
    r = sub.add_parser("run")
    r.add_argument("--corpus", required=True, type=Path); r.add_argument("--output", required=True, type=Path)
    r.add_argument("--provider", choices=["fixture", "codex", "bailian"], default="fixture")
    r.add_argument("--model"); r.add_argument("--config-file", type=Path); r.add_argument("--allow-model-calls", action="store_true")
    s = sub.add_parser("score")
    for name in ["run-dir", "ratings", "corpus", "output"]:
        s.add_argument("--" + name, required=True, type=Path)
    a = p.parse_args()
    if a.command == "freeze":
        value = freeze(a.output, a.quine_pdf, a.chalmers_pdf)
        print(json.dumps({"frozen_cases": len(value["cases"]), "output": str(a.output)}))
    elif a.command == "plan":
        print(json.dumps(plan(a.corpus), indent=2))
    elif a.command == "run":
        if a.provider != "fixture" and (not a.allow_model_calls or not a.model):
            p.error("Real runs require --allow-model-calls and an explicit --model")
        if a.provider == "codex":
            from workbench.review.codex_provider import CodexProvider
            provider = CodexProvider(model=a.model)
        elif a.provider == "bailian":
            from workbench.review.bailian import BailianProvider
            provider = BailianProvider(a.config_file, model=a.model)
        else:
            provider = DemoProvider()
        value = run(a.corpus, a.output, provider, a.model or "authored-fixture-v1", fixture=a.provider == "fixture")
        print(json.dumps({k: v for k, v in value.items() if k not in ("calls", "artifacts")}, indent=2))
        return 0 if value["status"] == "completed_awaiting_human" and value["source_unchanged"] else 1
    else:
        write(a.output, score(a.run_dir, a.ratings, a.corpus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
