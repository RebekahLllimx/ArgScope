"""Paid Bailian checks of Quine/FOL and Chalmers to human pause, never approval."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from workbench.review.bailian import BailianProvider
from workbench.review.research_examples import research_examples
from workbench.review.schemas import Request
from workbench.review.graph import Workflow
from workbench.review.store import Store
from workbench.review.shared import Shared

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "falsifylab/tools"))
from evaluate_kv import SessionServer, snapshot_source


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True,
                        help='Explicit operator-selected Bailian configuration file')
    parser.add_argument('--output', type=Path, required=True,
                        help='New output directory; existing runs are never overwritten')
    parser.add_argument('--kv-root', type=Path, default=ROOT / 'vendor/kv_server')
    return parser.parse_args(argv)


def main():
    args = parse_args()
    config = args.config.resolve(strict=True)
    kv_root = args.kv_root.resolve(strict=True)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    data = out / 'state'
    data.mkdir()
    before = snapshot_source(kv_root)
    results = []
    with tempfile.TemporaryDirectory() as temp:
        server = SessionServer(kv_root, Path(temp) / "session.aof")
        server.start()
        try:
            for example in research_examples():
                if example["id"] not in (
                    "quine-description-first-order",
                    "chalmers-singularity-original",
                ):
                    continue
                provider = BailianProvider(config)
                flow = Workflow(
                    Store(data / "reviews.sqlite3"),
                    Shared(server.port, "review-mvp"),
                    provider,
                    data / "graph.sqlite3",
                )
                req = Request.model_validate(example["request"]).model_dump(mode="json")
                run_id = flow.store.create(req)
                started = time.monotonic()
                item = {
                    "case": example["id"],
                    "run_id": run_id,
                    "provider": provider.name,
                    "request": req,
                    "human_review": "pending",
                }
                try:
                    flow.run(run_id)
                    snap = flow.snapshot(run_id)
                    failures = [
                        e
                        for e in flow.store.events(run_id)
                        if e["node"] == "execution_failed"
                    ]
                    if failures:
                        raise ValueError(failures[-1]["error"])
                    if not snap.interrupts:
                        raise ValueError("Expected an actual human-review interrupt")
                    item.update(
                        status="awaiting_human_review",
                        draft=flow.payload(snap.values["draft_ref"]),
                        draft_ref=snap.values["draft_ref"],
                        issues=snap.values.get("issues", []),
                        candidate_checks=flow.payload(snap.values["audit_ref"])[
                            "result"
                        ]["candidate_checks"],
                    )
                except Exception as exc:
                    item.update(status="failed", error=str(exc)[:500])
                finally:
                    item.update(
                        usage=provider.usage,
                        elapsed_seconds=round(time.monotonic() - started, 2),
                        events=flow.store.events(run_id),
                    )
                    flow.close()
                path = out / (example["id"] + "-" + run_id + ".json")
                path.write_text(json.dumps(item, ensure_ascii=False, indent=2))
                summary = {
                    k: item[k] for k in ("case", "run_id", "status", "elapsed_seconds")
                }
                summary.update(
                    calls=len(provider.usage),
                    candidate_outcomes=[
                        c["outcome"] for c in item.get("candidate_checks", [])
                    ],
                    error=item.get("error"),
                )
                results.append(summary)
                print(json.dumps(summary), flush=True)
        finally:
            server.stop()
    assert snapshot_source(kv_root) == before
    (out / "summary.json").write_text(
        json.dumps(
            {"runs": results, "kv_source_unchanged": True, "services_stopped": True},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
