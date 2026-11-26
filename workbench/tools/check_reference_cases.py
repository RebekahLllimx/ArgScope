"""Reproduce the documentation's six formula checks; no model or KV required."""
import argparse
import json
from pathlib import Path

from falsifylab.search import search_claim
from workbench.review.first_order import check

NEG_UNIQUE = "~(exists x. (W(x) & forall y. (W(y) -> y = x)))"
ZERO_OR_TWO = "(forall x. ~W(x)) | (exists x. (W(x) & exists y. (W(y) & ~(x = y))))"
AT_MOST_ONE = "forall x. forall y. ((W(x) & W(y)) -> x = y)"


def run():
    cases = [
        (
            "Q1",
            "Quine local translation, forward",
            [NEG_UNIQUE],
            ZERO_OR_TWO,
            "first_order",
            "no_counterexample_complete",
        ),
        (
            "Q2",
            "Quine local translation, reverse",
            [ZERO_OR_TWO],
            NEG_UNIQUE,
            "first_order",
            "no_counterexample_complete",
        ),
        (
            "Q3",
            "Altered reconstruction: replace zero-or-many with zero",
            [NEG_UNIQUE],
            "forall x. ~W(x)",
            "first_order",
            "witness_found",
        ),
        (
            "Q4",
            "Conditional repair: explicitly add at-most-one",
            [NEG_UNIQUE, AT_MOST_ONE],
            "forall x. ~W(x)",
            "first_order",
            "no_counterexample_complete",
        ),
        (
            "S1",
            "Chalmers displayed modus ponens",
            ["p", "p -> q"],
            "q",
            "propositional",
            "no_counterexample_complete",
        ),
        (
            "S2",
            "Altered reconstruction: omit conditional premise",
            ["p"],
            "q",
            "propositional",
            "witness_found",
        ),
    ]
    records = []
    for case_id, label, premises, conclusion, logic, expected in cases:
        result = (
            check(premises, conclusion)
            if logic == "first_order"
            else search_claim(
                {
                    "semantics": "classical-propositional-v1",
                    "premises": premises,
                    "conclusion": conclusion,
                },
                backend="dpll",
            )
        )
        if result["outcome"] != expected:
            raise RuntimeError(
                f"{case_id}: expected {expected}, received {result['outcome']}"
            )
        # The public record retains bounded evidence metadata, not a large proof dump.
        evidence = dict(result.get("evidence", {}))
        evidence.pop("proof_preview", None)
        records.append(
            {
                "id": case_id,
                "label": label,
                "logic": logic,
                "semantics": result.get("semantics", "classical-propositional-v1"),
                "domain": result.get("domain"),
                "budget": result.get("budget"),
                "premises": premises,
                "conclusion": conclusion,
                "outcome": result["outcome"],
                "backend": result.get(
                    "backend", result.get("kernel_result", {}).get("backend")
                ),
                "finite_model": result.get("finite_model"),
                "assignment": result.get("assignment"),
                "validation": result.get("validation"),
                "solver_evidence": evidence or None,
            }
        )
    return {
        "schema_version": "argscope-reference-checks-v1",
        "scope": "Guided formula checks and deliberately altered controls; no model accuracy or human approval claim",
        "cases": records,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="Create a new evidence file; existing files are preserved",
    )
    args = parser.parse_args()
    result = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    else:
        print(json.dumps(result, indent=2))
