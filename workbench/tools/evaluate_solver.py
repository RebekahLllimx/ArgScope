"""Small, explicitly favorable/unfavorable in-process backend observations."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter

from argscope.logic import check_argument
from argscope.sat import check_argument_dpll


def evaluate():
    cases = []
    for n in (4, 8, 12, 16, 32):
        cases.append(
            {
                "id": f"implication-chain-{n}",
                "premises": ["p0", *[f"p{i} -> p{i+1}" for i in range(n - 1)]],
                "conclusion": f"p{n-1}",
            }
        )
    cases.extend(
        [
            {"id": "early-countermodel", "premises": [], "conclusion": "p | q | r | s"},
            {
                "id": "branch-conflict",
                "premises": ["p | q", "~p | q", "p | ~q", "~p | ~q"],
                "conclusion": "r",
            },
        ]
    )
    observations = []
    for case in cases:
        rows = []
        for name, check in [
            ("exhaustive", lambda p, c: check_argument(p, c, max_variables=16)),
            ("dpll", check_argument_dpll),
        ]:
            check(case["premises"], case["conclusion"])
            times = []
            for _ in range(5):
                start = perf_counter()
                result = check(case["premises"], case["conclusion"])
                times.append(perf_counter() - start)
            rows.append(
                {
                    "backend": name,
                    "status": result["status"],
                    "seconds": times,
                    "median_seconds": median(times),
                    "stats": result.get("stats"),
                    "reason": result.get("reason"),
                }
            )
        if rows[0]["status"] != "unknown":
            assert rows[0]["status"] == rows[1]["status"]
        observations.append({"case": case, "observations": rows})
    return {
        "schema_version": "solver-observations-v1",
        "python": platform.python_version(),
        "repeats": 5,
        "warmups": 1,
        "reference_variable_limit": 16,
        "input_sha256": hashlib.sha256(
            json.dumps(cases, sort_keys=True).encode()
        ).hexdigest(),
        "cases": observations,
        "scope": "Illustrative authored cases, parse/check included, imports/server/startup excluded. Propagation chains favor DPLL; early countermodels can favor exhaustive evaluation. Unknown is not comparable to a completed check. No general speedup claim.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = evaluate()
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
    for case in value["cases"]:
        print(
            case["case"]["id"],
            [
                (x["backend"], x["status"], round(x["median_seconds"] * 1000, 4))
                for x in case["observations"]
            ],
        )
