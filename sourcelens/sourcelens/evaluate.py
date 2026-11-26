"""Run the authored example-corpus retrieval and citation checks."""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import statistics
import tempfile
import time

from .documents import chunk_markdown
from .retrieval import Retriever
from .store import Store
from .workflow import ResearchWorkflow

ROOT = Path(__file__).resolve().parents[1]


def evaluate():
    cases = json.loads((ROOT / "evaluation" / "cases.json").read_text())
    with tempfile.TemporaryDirectory(prefix="sourcelens-eval-") as directory:
        store = Store(Path(directory) / "evaluation.sqlite3")
        documents, keys = [], {}
        for path in sorted((ROOT / "examples").glob("*.md")):
            data = path.read_bytes()
            text = data.decode()
            saved = store.ingest(
                source_key=path.stem,
                title=text.splitlines()[0].lstrip("# "),
                label="example-v1",
                source_url=None,
                data=data,
                chunks=chunk_markdown(text),
            )
            keys[saved["document_id"]] = path.stem
            documents.append(
                {"file": path.name, "sha256": hashlib.sha256(data).hexdigest()}
            )
        retriever = Retriever(store)
        workflow = ResearchWorkflow(store, retriever)
        rows = []
        for method in ("bm25", "tfidf", "hybrid"):
            for case in cases:
                start = time.perf_counter()
                results = retriever.search(case["question"], method=method, limit=6)
                elapsed = (time.perf_counter() - start) * 1000
                relevant = {tuple(item) for item in case["relevant"]}
                ranks = [
                    (keys[chunk["document_id"]], chunk["heading"]) for chunk in results
                ]
                hits = relevant & set(ranks)
                first = next(
                    (index for index, key in enumerate(ranks, 1) if key in relevant),
                    None,
                )
                job_id, _ = store.create_job(
                    case["question"], method, method + "-" + case["id"]
                )
                store.claim()
                workflow.run(job_id)
                report = store.job(job_id)["state"]["report"]
                quotes = [
                    quote
                    for section in report["sections"]
                    for quote in section["quotes"]
                ]
                supported = sum(
                    quote["text"] in store.chunk(quote["chunk_id"])["body"]
                    for quote in quotes
                )
                rows.append(
                    {
                        "method": method,
                        "case_id": case["id"],
                        "split": case["split"],
                        "answerable": bool(relevant),
                        "recall_at_6": len(hits) / len(relevant) if relevant else None,
                        "reciprocal_rank_at_6": 1 / first if first else 0,
                        "retrieval_ms": elapsed,
                        "excerpt_count": len(quotes),
                        "verified_excerpt_count": supported,
                        "abstained": report["status"] == "insufficient_evidence",
                        "ranking": ranks,
                    }
                )
        summary = []
        for method in ("bm25", "tfidf", "hybrid"):
            selected = [
                row
                for row in rows
                if row["method"] == method and row["split"] == "test"
            ]
            answerable = [row for row in selected if row["answerable"]]
            negatives = [row for row in selected if not row["answerable"]]
            latencies = sorted(row["retrieval_ms"] for row in selected)
            summary.append(
                {
                    "method": method,
                    "answerable_test_cases": len(answerable),
                    "unanswerable_test_cases": len(negatives),
                    "recall_at_6": statistics.mean(
                        row["recall_at_6"] for row in answerable
                    ),
                    "mrr_at_6": statistics.mean(
                        row["reciprocal_rank_at_6"] for row in answerable
                    ),
                    "unanswerable_abstention_rate": statistics.mean(
                        row["abstained"] for row in negatives
                    ),
                    "verified_excerpts": sum(
                        row["verified_excerpt_count"] for row in selected
                    ),
                    "total_excerpts": sum(row["excerpt_count"] for row in selected),
                    "retrieval_p95_ms": latencies[
                        max(0, int(len(latencies) * 0.95 + 0.999) - 1)
                    ],
                }
            )
        return {
            "scope": "authored_example_corpus_sanity_check",
            "k": 6,
            "corpus": documents,
            "cases_sha256": hashlib.sha256(
                (ROOT / "evaluation" / "cases.json").read_bytes()
            ).hexdigest(),
            "runtime": {
                name: version(name) for name in ("scikit-learn", "numpy", "langgraph")
            },
            "summary": summary,
            "cases": rows,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/evaluation.json"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error(
            "Choose a new output file; evaluation artifacts are not overwritten"
        )
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(report, destination, indent=2, allow_nan=False)
        destination.write("\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
