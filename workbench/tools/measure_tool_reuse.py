"""Record actual authored-paper KV audit reuse in an isolated server; no LLM/review."""
import argparse
import json
from pathlib import Path
import sys
import uuid

from workbench.review.graph import Workflow
from workbench.review.paper_examples import PaperExamplesProvider
from workbench.review.provenance import environment, object_hash, source_snapshot
from workbench.review.research_examples import research_examples
from workbench.review.schemas import Request
from workbench.review.shared import Shared
from workbench.review.store import Store

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "falsifylab/tools"))
from evaluate_kv import SessionServer, snapshot_source as kv_snapshot


def run(kv_root, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    before, kv_before = source_snapshot(ROOT), kv_snapshot(kv_root)
    namespace = "lifecycle-" + uuid.uuid4().hex[:12]
    session = SessionServer(kv_root, output / "isolated.aof")
    config = {"namespace": namespace, "ttl_seconds": 3600, "repeats_per_case": 2,
              "mode": "authored-paper-examples", "human_review": "not_submitted", "model_calls": 0}
    provenance = {"source": before, "kv_source": kv_before, "kv_source_sha256": object_hash(kv_before),
                  "environment": environment(), "config": config}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2))
    flow, rows, status, error = None, [], "passed", None
    try:
        session.start()
        store = Store(output / "archive.sqlite3")
        flow = Workflow(store, Shared(session.port, namespace, 3600), PaperExamplesProvider(), output / "graph.sqlite3")
        for case in research_examples():
            if case["id"] not in ("quine-description-first-order", "chalmers-singularity-original"):
                continue
            request = Request.model_validate(case["request"]).model_dump(mode="json")
            for repeat in range(2):
                run_id = store.create(request)
                flow.run(run_id)
                state = flow.snapshot(run_id)
                events = store.events(run_id)
                if not state.interrupts or any(e["node"] == "execution_failed" for e in events):
                    raise ValueError("Authored task did not reach the human interrupt")
                for e in events:
                    if e["node"] == "kv_tool_result":
                        rows.append({"case": case["id"], "repeat": repeat + 1,
                                     "request_sha256": object_hash(request),
                                     **{k: e[k] for k in ["operation", "input", "output", "cache_hit", "worker_pid", "ttl_seconds", "tool_total_ms", "input_json_bytes", "output_json_bytes"]}})
        if len(rows) != 4 or [r["cache_hit"] for r in rows] != [False, True, False, True]:
            raise ValueError("Unexpected reuse pattern; retain the actual observations")
    except Exception as exc:
        status, error = "failed", str(exc)
    finally:
        if flow is not None:
            flow.close()
        session.stop()
    after = source_snapshot(ROOT)
    result = {"status": status, "error": error, "provenance_sha256": object_hash(provenance),
              "source_unchanged": before["sha256"] == after["sha256"], "kv_source_unchanged": kv_snapshot(kv_root) == kv_before,
              "config": config, "observations": rows, "completed_results": len(rows),
              "hits": sum(r["cache_hit"] for r in rows),
              "scope": "Four authored audit tasks, intentionally repeated; not a natural hit rate, speedup benchmark, paid generation or human approval"}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = run(args.kv_root, args.output)
    print(json.dumps(value, indent=2))
    raise SystemExit(0 if value["status"] == "passed" and value["source_unchanged"] and value["kv_source_unchanged"] else 1)
