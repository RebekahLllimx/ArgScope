"""A bounded, exhaustive propositional checker with explicit explanations.

Truth assignments are enumerated, so the implementation is also a transparent
reference oracle for a future SAT/SMT backend. No input is executed as Python.
"""

from dataclasses import dataclass
from itertools import product
import re
from typing import Mapping


class FormulaError(ValueError):
    """The input is outside the supported propositional language."""


@dataclass(frozen=True)
class Formula:
    op: str
    args: tuple = ()
    name: str = ""

    def variables(self) -> set[str]:
        if self.op == "var":
            return {self.name}
        return set().union(*(arg.variables() for arg in self.args))

    def evaluate(self, assignment: Mapping[str, bool]) -> bool:
        if self.op == "var":
            return bool(assignment[self.name])
        if self.op == "true":
            return True
        if self.op == "false":
            return False
        first = self.args[0].evaluate(assignment)
        if self.op == "not":
            return not first
        second = self.args[1].evaluate(assignment)
        if self.op == "and":
            return first and second
        if self.op == "or":
            return first or second
        if self.op == "implies":
            return (not first) or second
        if self.op == "iff":
            return first == second
        raise FormulaError("Unknown operator")


_TOKEN = re.compile(r"\s*(<->|->|[~!&|()]|[A-Za-z][A-Za-z0-9_]*)")
_UNICODE = str.maketrans({"¬": "~", "∧": "&", "∨": "|", "→": "->", "↔": "<->", "⊤": "TRUE", "⊥": "FALSE"})


class _Parser:
    def __init__(self, source: str):
        if not isinstance(source, str) or not source.strip():
            raise FormulaError("A nonempty formula string is required")
        if len(source) > 1000:
            raise FormulaError("Formula exceeds the 1000-character input limit")
        source = source.translate(_UNICODE).strip()
        self.tokens = []
        cursor = 0
        while cursor < len(source):
            match = _TOKEN.match(source, cursor)
            if match is None:
                raise FormulaError(f"Unexpected character at offset {cursor}")
            self.tokens.append(match.group(1))
            cursor = match.end()
        if len(self.tokens) > 160:
            raise FormulaError("Formula exceeds the 160-token input limit")
        self.cursor = 0

    def accept(self, token: str) -> bool:
        if self.cursor < len(self.tokens) and self.tokens[self.cursor] == token:
            self.cursor += 1
            return True
        return False

    def expression(self) -> Formula:
        left = self.implication()
        if self.accept("<->"):
            left = Formula("iff", (left, self.implication()))
            if self.cursor < len(self.tokens) and self.tokens[self.cursor] == "<->":
                raise FormulaError("Parenthesize a chain of biconditionals")
        return left

    def implication(self) -> Formula:
        left = self.disjunction()
        if self.accept("->"):
            return Formula("implies", (left, self.implication()))
        return left

    def disjunction(self) -> Formula:
        left = self.conjunction()
        while self.accept("|"):
            left = Formula("or", (left, self.conjunction()))
        return left

    def conjunction(self) -> Formula:
        left = self.atom()
        while self.accept("&"):
            left = Formula("and", (left, self.atom()))
        return left

    def atom(self) -> Formula:
        if self.accept("~") or self.accept("!"):
            return Formula("not", (self.atom(),))
        if self.accept("("):
            formula = self.expression()
            if not self.accept(")"):
                raise FormulaError("Missing closing parenthesis")
            return formula
        if self.cursor == len(self.tokens):
            raise FormulaError("Expected a variable or subexpression")
        token = self.tokens[self.cursor]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", token):
            raise FormulaError(f"Expected an atom, received {token!r}")
        self.cursor += 1
        if token in ("TRUE", "FALSE"):
            return Formula(token.lower())
        return Formula("var", name=token)


def parse(source: str) -> Formula:
    parser = _Parser(source)
    formula = parser.expression()
    if parser.cursor != len(parser.tokens):
        raise FormulaError("Unexpected trailing token; use an explicit connective")
    return formula


def _assignments(variables):
    for bits in product((False, True), repeat=len(variables)):
        yield dict(zip(variables, bits))


def _satisfying_assignment(formulas, variables):
    return next((row for row in _assignments(variables) if all(f.evaluate(row) for f in formulas)), None)


def check_argument(premises: list[str], conclusion: str, *, max_variables: int = 12) -> dict:
    """Classify an argument without treating inconsistent premises as evidence.

    An inconsistency explanation is subset-minimal, not necessarily the
    smallest possible core. UNKNOWN means a resource bound, never invalidity.
    """
    if not isinstance(premises, list) or len(premises) > 32:
        raise FormulaError("Premises must be a list of at most 32 formulas")
    if type(max_variables) is not int or not 0 <= max_variables <= 16:
        raise FormulaError("max_variables must be an integer between 0 and 16")
    parsed = [parse(p) for p in premises]
    target = parse(conclusion)
    variables = sorted(set().union(target.variables(), *(p.variables() for p in parsed)))
    result = {"backend": "exhaustive", "variables": variables, "formalization": "user_supplied"}
    if len(variables) > max_variables:
        return {**result, "status": "unknown", "reason": "variable_limit", "limit": max_variables}

    witness = _satisfying_assignment(parsed, variables)
    if witness is None:
        core = list(range(len(parsed)))
        for index in list(core):
            candidate = [i for i in core if i != index]
            if _satisfying_assignment([parsed[i] for i in candidate], variables) is None:
                core = candidate
        return {**result, "status": "inconsistent_premises", "conflicting_indices": core,
                "conflicting_premises": [premises[i] for i in core], "core_kind": "subset_minimal"}

    for row in _assignments(variables):
        if all(p.evaluate(row) for p in parsed) and not target.evaluate(row):
            return {**result, "status": "invalid", "counterexample": row,
                    "premise_values": [p.evaluate(row) for p in parsed], "conclusion_value": False}
    return {**result, "status": "valid", "satisfying_premises_example": witness}
