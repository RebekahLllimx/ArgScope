"""Supervised deterministic tool process: receives keys, never a history DB path."""
import argparse
import json
import os
from .shared import Shared
from .schemas import validate_draft

TOOL_VERSION = "review-tools-v3"


def calculate(payload):
    if (
        set(payload)
        != {"operation", "request", "draft", "human_review", "tool_version"}
        or payload["tool_version"] not in ("review-tools-v2", TOOL_VERSION)
    ):
        raise ValueError("Unsupported tool request")
    draft, issues = validate_draft(payload["draft"], payload["request"])
    operation = payload["operation"]
    if operation not in ("audit", "formal"):
        raise ValueError("Unknown deterministic tool")
    review = payload["human_review"]
    approved = (
        isinstance(review, dict)
        and all(
            review.get(k) == "accepted"
            for k in ("fidelity", "attribution", "interpretation")
        )
        and review.get("approve_formal_mapping") is True
    )
    results = []
    for reading in draft["readings"]:
        item = {"reading_id": reading["id"]}
        mapping = reading["formal_mapping"]
        scope = reading.get("formal_scope")
        if scope and payload["tool_version"] == "review-tools-v2":
            raise ValueError("Declared formula scope requires review-tools-v3")
        scope_approved = (
            scope is not None and isinstance(review, dict)
            and review.get("approve_formula_scope") is True
            and review.get("approve_formal_mapping") is True
        )
        if operation == "formal" and scope and not scope_approved:
            item.update(outcome="review_required",
                        reason="Confirm the declared formula scope and mapping explicitly")
        elif operation == "formal" and not (approved or scope_approved):
            item.update(
                outcome="review_required",
                reason="Three current human judgments and explicit mapping approval are required",
            )
        elif scope is None and any(q["representation"] == "unencoded" for q in reading["qualifications"]):
            item.update(
                outcome="unsupported_semantics",
                reason="Declared semantics are not encoded",
            )
        elif mapping is None:
            item.update(
                outcome="no_formal_mapping",
                reason="This prose reading has no executable formal mapping",
            )
        else:
            nodes = {n["id"]: n for n in reading["nodes"]}
            target = nodes[reading["target"]]
            needed = set()

            def visit(key):
                if key in needed:
                    return
                needed.add(key)
                for ref in nodes[key]["depends_on"]:
                    visit(ref)

            visit(target["id"])
            if not needed <= set(mapping["formulas"]):
                item.update(
                    outcome="incomplete_mapping",
                    reason="Map every ancestor of the selected inference explicitly",
                )
            elif mapping.get("semantics") == "classical-first-order-v1":
                from .first_order import analyze

                checked = analyze(reading)
                last = next(s for s in checked["steps"] if s["id"] == target["id"])
                if last["status"] == "blocked":
                    item.update(
                        outcome="blocked", blocked_by=last["blocked_by"], graph=checked
                    )
                else:
                    item.update(last["result"])
                    item["graph"] = checked
            else:
                from argscope.graph import analyze_document
                from falsifylab.search import search_claim

                graph = {"premises": [], "steps": []}
                for node in reading["nodes"]:
                    if node["id"] in needed:
                        graph[
                            "premises" if node["role"] == "premise" else "steps"
                        ].append(
                            {
                                "id": node["id"],
                                "formula": mapping["formulas"][node["id"]],
                                "depends_on": node["depends_on"],
                            }
                        )
                checked = analyze_document(graph, backend="dpll", max_variables=64)
                last = next(s for s in checked["steps"] if s["id"] == target["id"])
                if last["status"] == "blocked":
                    item.update(
                        outcome="blocked", blocked_by=last["blocked_by"], graph=checked
                    )
                else:
                    claim = {
                        "semantics": "classical-propositional-v1",
                        "premises": [
                            mapping["formulas"][k] for k in target["depends_on"]
                        ],
                        "conclusion": mapping["formulas"][target["id"]],
                    }
                    item.update(search_claim(claim, backend="dpll"))
                    item["graph"] = checked
        item["scope"] = (
            "Unapproved candidate mapping only; source fidelity and premise truth are pending human review."
            if operation == "audit"
            else (
                "Only the supplied and explicitly approved first-order mapping; trusted solver UNSAT evidence or independently evaluated finite countermodel, not a philosophical verdict."
                if mapping and mapping.get("semantics") == "classical-first-order-v1"
                else "Only the supplied and explicitly approved propositional mapping; no automatic philosophical verdict."
            )
        )
        if scope is not None:
            item["formula_scope"] = scope
            item["excluded_qualifications"] = [
                {"index": index, **q} for index, q in enumerate(reading["qualifications"])
                if q["representation"] == "unencoded"
            ]
            item["scope"] = (
                "Only the declared formulas and dependencies, not validity of the whole passage. "
                "Unencoded qualifications remain unresolved and are listed separately. "
                + ("Candidate only; no reader approval." if operation == "audit"
                   else "Explicit formula-scope confirmation is recorded separately from text judgments.")
            )
        if operation == "audit":
            item["human_approved"] = False
        results.append(item)
    if operation == "audit":
        return {
            "operation": "audit",
            "issues": issues,
            "references_verified": True,
            "candidate_checks": results,
            "scope": "Reference identity, record structure and conditional candidate checks; no approval or source-support certification.",
        }
    return {"operation": "formal", "results": results}


def execute(shared, input_key):
    request = shared.fetch(input_key)
    if request["kind"] != "tool-request":
        raise ValueError("Expected a tool-request artifact")
    result = calculate(request["payload"])
    output = {
        "schema": "review-artifact-v1",
        "kind": "tool-result",
        "inputs": [input_key],
        "payload": {"tool_version": TOOL_VERSION, "result": result},
    }
    key = shared.publish(output)
    return {
        "output_key": key,
        "worker_pid": os.getpid(),
        "input_key": input_key,
        "tool_version": TOOL_VERSION,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv-port", type=int, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--ttl", type=int, default=3600)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                execute(Shared(args.kv_port, args.namespace, args.ttl), args.input)
            )
        )
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
