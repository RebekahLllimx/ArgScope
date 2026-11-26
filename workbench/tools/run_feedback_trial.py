"""Explicit, two-stage live feedback trial (Codex by default) through the real API and KV.

Run initial, inspect its frozen draft, then supply a feedback JSON file. This
operator script never submits final human approval or automatically retries.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
from verify_review_mvp import ROOT, SessionServer, snapshot_source
from workbench.review.bailian import BailianProvider
from workbench.review.codex_provider import CodexProvider
from workbench.review.provenance import source_snapshot, environment, object_hash, sha
from workbench.review.research_examples import research_examples
from workbench.review.schemas import Request, RevisionFeedback
from workbench.review_app import create_app


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


class RecordedProvider:
    """Observe the existing adapter; do not change its prompt or response."""
    def __init__(self, inner, output):
        self.inner, self.output = inner, output
        self.name, self.live, self.usage = inner.name, inner.live, inner.usage
        self.calls = []

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        payload = deepcopy(dict(operation=operation, request=request, draft=draft,
                                issues=issues, evidence=evidence))
        stem = f"{len(self.calls) + 1:02d}-{operation}"
        write(self.output / (stem + ".input.json"), payload)
        entry = {"operation": operation, "input_sha256": object_hash(payload),
                 "status": "attempted"}
        self.calls.append(entry)
        start, usage_start = time.monotonic(), len(self.usage)
        try:
            result = self.inner.generate(**payload)
            write(self.output / (stem + ".output.json"), result)
            entry.update(status="returned", output_sha256=object_hash(result))
            return result
        except Exception as exc:
            # The adapter sanitizes transport errors, never emits credentials.
            entry.update(status="failed", error=str(exc))
            raise
        finally:
            entry.update(elapsed_seconds=round(time.monotonic() - start, 6),
                         usage=deepcopy(self.usage[usage_start:]))
            write(self.output / (stem + ".receipt.json"), entry)


def wait(client, run_id):
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        response = client.get(f"/api/argument-runs/{run_id}")
        response.raise_for_status()
        value = response.json()
        if value["status"] not in ("queued", "running"):
            return value
        time.sleep(.1)
    raise RuntimeError("Trial polling deadline exceeded; inspect persisted events")


def selected_provider(args):
    if args.provider == "codex":
        if args.config is not None:
            raise ValueError("--config is only valid with explicit --provider bailian")
        return CodexProvider(model=args.model)
    return BailianProvider(args.config, args.model)


def run(args):
    out = args.output.resolve()
    if args.phase == "initial":
        out.mkdir(parents=True, exist_ok=False)
    phase = out / args.phase
    phase.mkdir(exist_ok=False)
    before, kv_before = source_snapshot(ROOT), snapshot_source(args.kv_root)
    provider = RecordedProvider(selected_provider(args), phase)
    provenance = {
        "schema": "argscope-feedback-trial-v1", "started_utc": datetime.now(timezone.utc).isoformat(),
        "source": before, "kv_source": kv_before, "environment": environment(),
        "provider": provider.name, "model_requested": args.model,
        "immutable_model_revision": None, "workflow": "review-workflow-v2",
        "provider_settings": ({"temperature": 0, "max_tokens": 6000, "enable_thinking": False}
                              if args.provider == "bailian" else {"model": args.model or "client-default"}),
        "prompt_source_sha256": sha((ROOT / "workbench/workbench/review" /
                                    ("bailian.py" if args.provider == "bailian" else "codex_provider.py")).read_bytes()),
        "reviewer_role": "Codex assistant acting as feedback operator; not a human or independent expert",
        "final_human_approval": False, "automatic_retry": False,
    }
    if args.phase == "initial":
        request = Request.model_validate(next(c for c in research_examples() if c["id"] == args.case)["request"]).model_dump(mode="json")
        write(out / "request.json", request)
    else:
        request = read(out / "request.json")
        previous = read(out / "initial/state.json")
        if previous["status"] != "awaiting_review":
            raise ValueError("Initial run did not reach reader review")
        if read(out / "initial/provenance.json")["source"]["sha256"] != before["sha256"]:
            raise ValueError("Source changed between phases; use a new trial")
        feedback = RevisionFeedback.model_validate(read(args.feedback_file)).model_dump(mode="json")
        if feedback["expected_draft"] != previous["draft_ref"] or feedback["edited_draft"] is not None:
            raise ValueError("This trial compares unedited saved model drafts")
        write(phase / "feedback.json", feedback)
        provenance["feedback_sha256"] = object_hash(feedback)
    provenance["request_sha256"] = object_hash(request)
    write(phase / "provenance.json", provenance)
    session = SessionServer(args.kv_root, out / "isolated.aof")
    result = {"phase": args.phase, "status": "failed", "final_human_approval": False}
    tick = time.monotonic()
    try:
        session.start()
        app = create_app(out / "data", session.port, namespace="live-feedback", provider=provider, run_source_worker=False)
        with TestClient(app) as client:
            if args.phase == "initial":
                response = client.post("/api/argument-runs", json=request)
            else:
                response = client.post(f"/api/argument-runs/{previous['id']}/feedback", json=feedback)
            response.raise_for_status()
            run_id = response.json()["id"]
            state = wait(client, run_id)
            write(phase / "state.json", state)
            if state["draft"]:
                write(phase / "draft.json", state["draft"])
            if args.phase == "feedback" and state["status"] == "awaiting_review":
                compared = client.get(f"/api/argument-runs/{run_id}/compare", params={"before": previous["draft_ref"], "after": state["draft_ref"]})
                compared.raise_for_status()
                write(phase / "comparison.json", compared.json())
                assert state["feedback_round"] == 1 and not state["report"]
                assert not app.state.review.store.human_records(run_id)
            result.update(status=state["status"], error=state["error"], run_id=run_id,
                          versions=state["versions"], draft_ref=state["draft_ref"])
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        session.stop()
        result.update(calls=provider.calls, model_attempts=len(provider.calls),
                      elapsed_seconds=round(time.monotonic() - tick, 6),
                      provenance_sha256=object_hash(provenance),
                      source_unchanged=before["sha256"] == source_snapshot(ROOT)["sha256"],
                      kv_source_unchanged=kv_before == snapshot_source(args.kv_root))
        write(phase / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "awaiting_review" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=["initial", "feedback"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provider", choices=["codex", "bailian"], default="codex")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--model")
    parser.add_argument("--case", default="quine-mcx-original")
    parser.add_argument("--feedback-file", type=Path)
    args = parser.parse_args()
    if args.phase == "feedback" and args.feedback_file is None:
        parser.error("--feedback-file required for feedback phase")
    raise SystemExit(run(args))
