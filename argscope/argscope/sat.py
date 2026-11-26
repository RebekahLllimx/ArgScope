"""Tseitin CNF and bounded, deterministic DPLL over the existing immutable AST.

The exhaustive evaluator remains an independent small-instance oracle. Counters
are deterministic work observations, not timing or polynomial-time guarantees.
"""
from dataclasses import dataclass, field

from .logic import Formula, FormulaError, parse


@dataclass(frozen=True)
class Limits:
    variables: int = 64
    nodes: int = 2048
    clauses: int = 8192
    decisions: int = 100000
    work: int = 1000000

    def validate(self):
        for name, upper in (
            ("variables", 64),
            ("nodes", 2048),
            ("clauses", 8192),
            ("decisions", 100000),
            ("work", 1000000),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= upper:
                raise FormulaError(f"Invalid DPLL {name} limit")


class LimitReached(Exception):
    pass


@dataclass
class Budget:
    limits: Limits
    stats: dict = field(
        default_factory=lambda: dict(
            solves=0, decisions=0, propagations=0, conflicts=0, backtracks=0, work=0
        )
    )

    def tick(self):
        if self.stats["work"] >= self.limits.work:
            raise LimitReached("work_limit")
        self.stats["work"] += 1


@dataclass(frozen=True)
class CNF:
    clauses: tuple
    original_ids: tuple
    variable_count: int


def ast_data(formula):
    result = {"op": formula.op}
    if formula.op == "var":
        result["name"] = formula.name
    if formula.args:
        result["args"] = [ast_data(a) for a in formula.args]
    return result


def tseitin(formulas, variables=None):
    """Encode all formula roots as true; auxiliary IDs cannot shadow atoms."""
    names = sorted(
        set().union(*(f.variables() for f in formulas))
        if variables is None
        else variables
    )
    original = {name: index + 1 for index, name in enumerate(names)}
    clauses, memo = [], {}
    next_id = len(original) + 1

    def encode(formula):
        nonlocal next_id
        key = id(formula)
        if key in memo:
            return memo[key]
        if formula.op == "var":
            return original[formula.name]
        children = [encode(a) for a in formula.args]
        v = next_id
        next_id += 1
        memo[key] = v
        op = formula.op
        if op == "true":
            clauses.append((v,))
        elif op == "false":
            clauses.append((-v,))
        elif op == "not":
            (a,) = children
            clauses.extend(((-v, -a), (v, a)))
        else:
            a, b = children
            if op == "and":
                clauses.extend(((-v, a), (-v, b), (v, -a, -b)))
            elif op == "or":
                clauses.extend(((v, -a), (v, -b), (-v, a, b)))
            elif op == "implies":
                clauses.extend(((v, a), (v, -b), (-v, -a, b)))
            elif op == "iff":
                clauses.extend(((-v, -a, b), (-v, a, -b), (v, a, b), (v, -a, -b)))
            else:
                raise FormulaError("Unknown AST operator")
        return v

    for formula in formulas:
        clauses.append((encode(formula),))
    return CNF(tuple(clauses), tuple(original.items()), next_id - 1)


def solve(cnf, budget):
    """Return a partial satisfying assignment, None for complete UNSAT, or raise a bound."""
    if len(cnf.clauses) > budget.limits.clauses:
        raise LimitReached("clause_limit")
    budget.stats["solves"] += 1
    stack = [({}, False)]
    while stack:
        assignment, alternative = stack.pop()
        if alternative:
            budget.stats["backtracks"] += 1
        conflict = False
        while True:
            changed = False
            unresolved = set()
            for clause in cnf.clauses:
                pending = []
                satisfied = False
                for literal in clause:
                    budget.tick()
                    value = assignment.get(abs(literal))
                    if value is None:
                        pending.append(literal)
                    elif value == (literal > 0):
                        satisfied = True
                        break
                if satisfied:
                    continue
                if not pending:
                    budget.stats["conflicts"] += 1
                    conflict = True
                    break
                if len(pending) == 1:
                    literal = pending[0]
                    assignment[abs(literal)] = literal > 0
                    budget.stats["propagations"] += 1
                    changed = True
                else:
                    unresolved.update(abs(literal) for literal in pending)
            if conflict:
                break
            if changed:
                continue
            if not unresolved:
                return assignment
            break
        if conflict:
            continue
        if budget.stats["decisions"] >= budget.limits.decisions:
            raise LimitReached("decision_limit")
        variable = min(unresolved)
        budget.stats["decisions"] += 1
        # LIFO: visit false first; keep the unexplored branch explicitly on the stack.
        stack.append(({**assignment, variable: True}, True))
        stack.append(({**assignment, variable: False}, False))
    return None


def check_argument_dpll(premises, conclusion, *, limits=None):
    limits = limits or Limits()
    limits.validate()
    if not isinstance(premises, list) or len(premises) > 32:
        raise FormulaError("Premises must be a list of at most 32 formulas")
    parsed, target = [parse(p) for p in premises], parse(conclusion)
    variables = sorted(
        set().union(target.variables(), *(p.variables() for p in parsed))
    )
    budget = Budget(limits)
    encoding = {"max_clauses": 0, "max_variables": 0}
    base = {
        "backend": "dpll-v1",
        "variables": variables,
        "formalization": "user_supplied",
    }

    def result(**values):
        return {
            **base,
            **values,
            "stats": dict(budget.stats),
            "encoding": dict(encoding),
        }

    def count(formula):
        return 1 + sum(count(a) for a in formula.args)

    if len(variables) > limits.variables:
        return result(status="unknown", reason="variable_limit", limit=limits.variables)
    if sum(count(f) for f in [*parsed, target]) > limits.nodes:
        return result(status="unknown", reason="ast_node_limit", limit=limits.nodes)

    def query(formulas):
        cnf = tseitin(formulas, variables)
        encoding["max_clauses"] = max(encoding["max_clauses"], len(cnf.clauses))
        encoding["max_variables"] = max(encoding["max_variables"], cnf.variable_count)
        assignment = solve(cnf, budget)
        if assignment is None:
            return None
        projected = {name: assignment.get(i, False) for name, i in cnf.original_ids}
        if not all(formula.evaluate(projected) for formula in formulas):
            raise FormulaError("Solver assignment failed original-AST verification")
        return projected

    try:
        witness = query(parsed)
    except LimitReached as error:
        return result(status="unknown", reason=str(error), stage="premise_consistency")
    if witness is None:
        core = list(range(len(parsed)))
        try:
            for index in list(core):
                candidate = [i for i in core if i != index]
                if query([parsed[i] for i in candidate]) is None:
                    core = candidate
        except LimitReached as error:
            return result(
                status="inconsistent_premises",
                conflicting_indices=core,
                conflicting_premises=[premises[i] for i in core],
                core_kind="not_minimized",
                reduction_reason=str(error),
            )
        return result(
            status="inconsistent_premises",
            conflicting_indices=core,
            conflicting_premises=[premises[i] for i in core],
            core_kind="subset_minimal",
        )
    try:
        counterexample = query([*parsed, Formula("not", (target,))])
    except LimitReached as error:
        return result(status="unknown", reason=str(error), stage="countermodel_search")
    if counterexample is not None:
        return result(
            status="invalid",
            counterexample=counterexample,
            premise_values=[p.evaluate(counterexample) for p in parsed],
            conclusion_value=False,
        )
    return result(status="valid", satisfying_premises_example=witness)
