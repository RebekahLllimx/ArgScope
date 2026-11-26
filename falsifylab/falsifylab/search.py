"""Typed formal counterexample search using the existing bounded kernel.

Conceptual claims remain unsupported. A free knowledge atom is not a
conceptual counterexample, and source fidelity is never solver-certified.
"""
from argscope.logic import check_argument, parse


def validate_assignment(premises, conclusion, assignment):
    formulas = [parse(p) for p in premises]
    target = parse(conclusion)
    variables = set().union(target.variables(), *(p.variables() for p in formulas))
    if not isinstance(assignment, dict) or set(assignment) != variables:
        raise ValueError("Witness must assign exactly the original variables")
    if any(type(value) is not bool for value in assignment.values()):
        raise ValueError("Witness values must be Boolean")
    values = [p.evaluate(assignment) for p in formulas]
    result = target.evaluate(assignment)
    if not all(values) or result:
        raise ValueError("Witness does not falsify the original inference")
    return {
        "validator": "original-ast-v1",
        "premise_values": values,
        "conclusion_value": result,
        "status": "formal_witness_verified",
    }


def search_claim(claim, *, backend="exhaustive"):
    if backend not in ("exhaustive", "dpll"):
        raise ValueError("Unknown formal backend")
    if claim["semantics"] != "classical-propositional-v1":
        return {
            "outcome": "unsupported_semantics",
            "reason": "Conceptual judgments require a typed case backend and human review",
        }
    if backend == "dpll":
        from argscope.sat import check_argument_dpll

        checked = check_argument_dpll(claim["premises"], claim["conclusion"])
    else:
        checked = check_argument(claim["premises"], claim["conclusion"])
    status = checked["status"]
    if status == "invalid":
        assignment = checked["counterexample"]
        validation = validate_assignment(
            claim["premises"], claim["conclusion"], assignment
        )
        return {
            "outcome": "witness_found",
            "assignment": assignment,
            "validation": validation,
            "kernel_result": checked,
        }
    return {
        "outcome": {
            "valid": "no_counterexample_complete",
            "unknown": "budget_exhausted",
            "inconsistent_premises": "inconsistent_assumptions",
        }[status],
        "kernel_result": checked,
    }
