"""Validate argument dependencies before checking inference steps."""

from collections import deque
from .logic import FormulaError, check_argument, parse


class DocumentError(ValueError):
    """An argument graph has an invalid structure or reference."""


def _index(document):
    if not isinstance(document, dict) or set(document) - {
        "title",
        "premises",
        "steps",
        "symbols",
    }:
        raise DocumentError("Expected title, premises, steps, and optional symbols")
    premises = document.get("premises", [])
    steps = document.get("steps", [])
    if (
        not isinstance(premises, list)
        or not isinstance(steps, list)
        or len(premises) + len(steps) > 80
    ):
        raise DocumentError("Expected lists with at most 80 total nodes")
    nodes = {}
    for kind, rows in (("premise", premises), ("step", steps)):
        for row in rows:
            if not isinstance(row, dict) or set(row) - {
                "id",
                "formula",
                "label",
                "depends_on",
            }:
                raise DocumentError("Unknown node field")
            key = row.get("id")
            if not isinstance(key, str) or not key or len(key) > 80 or key in nodes:
                raise DocumentError("Node identifiers must be nonempty and unique")
            try:
                parse(row.get("formula"))
            except FormulaError as error:
                raise DocumentError(f"Node {key}: {error}") from error
            refs = row.get("depends_on", [])
            if (
                not isinstance(refs, list)
                or any(not isinstance(ref, str) for ref in refs)
                or len(refs) != len(set(refs))
            ):
                raise DocumentError("Dependencies must be a list of unique identifiers")
            if kind == "premise" and refs:
                raise DocumentError("Premises cannot depend on inference steps")
            if len(refs) > 32:
                raise DocumentError(
                    "An inference step can use at most 32 direct premises"
                )
            nodes[key] = {**row, "kind": kind, "depends_on": refs}
    outgoing = {key: [] for key in nodes}
    indegree = {key: len(row["depends_on"]) for key, row in nodes.items()}
    for key, row in nodes.items():
        for ref in row["depends_on"]:
            if ref not in nodes:
                raise DocumentError(f"Node {key} refers to unknown node {ref}")
            outgoing[ref].append(key)
    queue = deque(key for key in nodes if indegree[key] == 0)
    order = []
    while queue:
        key = queue.popleft()
        order.append(key)
        for child in outgoing[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(nodes):
        raise DocumentError("The dependency graph contains a cycle")
    return nodes, outgoing, order


def analyze_document(
    document: dict, *, max_variables: int = 12, backend="exhaustive"
) -> dict:
    if backend not in ("exhaustive", "dpll"):
        raise DocumentError("Unknown formal backend")
    nodes, _, order = _index(document)
    results = {}
    for key in order:
        row = nodes[key]
        if row["kind"] == "premise":
            results[key] = {"id": key, "formula": row["formula"], "status": "assumed"}
            continue
        refs = row["depends_on"]
        blocked = [
            ref for ref in refs if results[ref]["status"] not in ("assumed", "valid")
        ]
        if blocked:
            result = {"status": "blocked", "blocked_by": blocked}
        else:
            if backend == "dpll":
                from .sat import Limits, check_argument_dpll

                result = check_argument_dpll(
                    [nodes[ref]["formula"] for ref in refs],
                    row["formula"],
                    limits=Limits(variables=max_variables),
                )
            else:
                result = check_argument(
                    [nodes[ref]["formula"] for ref in refs],
                    row["formula"],
                    max_variables=max_variables,
                )
            if "conflicting_indices" in result:
                result["conflicting_nodes"] = [
                    refs[i] for i in result["conflicting_indices"]
                ]
        results[key] = {
            **result,
            "id": key,
            "formula": row["formula"],
            "depends_on": refs,
        }
    return {
        "title": document.get("title", "Argument"),
        "semantics": "classical_propositional",
        "steps": [results[row["id"]] for row in document.get("steps", [])],
        "topological_order": order,
        "all_steps_valid": bool(document.get("steps"))
        and all(
            results[row["id"]]["status"] == "valid" for row in document.get("steps", [])
        ),
    }


def affected_steps(document: dict, changed_id: str) -> list[str]:
    """Return only descendants requiring revalidation, in dependency order."""
    nodes, outgoing, order = _index(document)
    if changed_id not in nodes:
        raise DocumentError("Unknown changed node")
    seen = {changed_id}
    queue = deque([changed_id])
    while queue:
        for child in outgoing[queue.popleft()]:
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return [key for key in order if key in seen and nodes[key]["kind"] == "step"]
