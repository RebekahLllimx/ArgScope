"""Independent CNF checks and fixed-seed exhaustive/DPLL differential cases."""
from itertools import product
import random
import unittest

from argscope.logic import parse, check_argument
from argscope.sat import Limits, Budget, ast_data, tseitin, solve, check_argument_dpll


def generated_arguments(count=1000):
    rng = random.Random(20261123)

    def formula(depth, names):
        if not depth or rng.random() < 0.25:
            return rng.choice([*names, "TRUE", "FALSE"])
        if rng.random() < 0.2:
            return "~(" + formula(depth - 1, names) + ")"
        return (
            "("
            + formula(depth - 1, names)
            + rng.choice([" & ", " | ", " -> ", " <-> "])
            + formula(depth - 1, names)
            + ")"
        )

    for _ in range(count):
        names = ["p" + str(i) for i in range(rng.randint(1, 8))]
        yield [formula(3, names) for _ in range(rng.randrange(5))], formula(3, names)


class SATTests(unittest.TestCase):
    def test_thousand_generated_arguments_against_exhaustive(self):
        for index, (premises, conclusion) in enumerate(generated_arguments()):
            with self.subTest(index=index):
                expected = check_argument(premises, conclusion)
                actual = check_argument_dpll(premises, conclusion)
                self.assertEqual(actual["status"], expected["status"])
                if actual["status"] == "invalid":
                    witness = actual["counterexample"]
                    self.assertTrue(all(parse(p).evaluate(witness) for p in premises))
                    self.assertFalse(parse(conclusion).evaluate(witness))
                elif actual["status"] == "inconsistent_premises":
                    core = actual["conflicting_premises"]
                    self.assertEqual(
                        check_argument(core, "FALSE")["status"], "inconsistent_premises"
                    )
                    for i in range(len(core)):
                        self.assertNotEqual(
                            check_argument(core[:i] + core[i + 1 :], "FALSE")["status"],
                            "inconsistent_premises",
                        )

    def test_tseitin_extensions_by_independent_cnf_truth_evaluation(self):
        sources = [
            "p",
            "TRUE",
            "FALSE",
            "~p",
            "p & q",
            "p | q",
            "p -> q",
            "p <-> q",
            "~(p <-> q)",
            "(p -> q) & (q -> p)",
            "p | (q & ~p)",
        ]
        for source in sources:
            ast = parse(source)
            cnf = tseitin([ast])
            originals = dict(cnf.original_ids)
            for original_bits in product((False, True), repeat=len(originals)):
                assignment = dict(zip(originals, original_bits))
                fixed = {originals[name]: value for name, value in assignment.items()}
                auxiliaries = [
                    i for i in range(1, cnf.variable_count + 1) if i not in fixed
                ]
                extensions = []
                for bits in product((False, True), repeat=len(auxiliaries)):
                    full = {**fixed, **dict(zip(auxiliaries, bits))}
                    extensions.append(
                        all(
                            any(full[abs(lit)] == (lit > 0) for lit in clause)
                            for clause in cnf.clauses
                        )
                    )
                self.assertEqual(
                    any(extensions), ast.evaluate(assignment), (source, assignment)
                )

    def test_propagation_chain_exceeds_reference_bound_without_decisions(self):
        premises = ["p0", *[f"p{i} -> p{i+1}" for i in range(31)]]
        self.assertEqual(check_argument(premises, "p31")["status"], "unknown")
        result = check_argument_dpll(premises, "p31")
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["stats"]["decisions"], 0)
        self.assertGreater(result["stats"]["propagations"], 0)

    def test_conflict_pruning_and_backtracking_are_exercised(self):
        source = "(p | q) & (~p | q) & (p | ~q) & (~p | ~q)"
        budget = Budget(Limits())
        self.assertIsNone(solve(tseitin([parse(source)]), budget))
        for key in ("decisions", "conflicts", "backtracks", "propagations"):
            self.assertGreater(budget.stats[key], 0)

    def test_limits_never_return_valid_for_unfinished_search(self):
        for limits in (
            Limits(variables=0),
            Limits(nodes=0),
            Limits(clauses=0),
            Limits(work=0),
        ):
            self.assertEqual(
                check_argument_dpll(["p"], "q", limits=limits)["status"], "unknown"
            )
        self.assertEqual(
            check_argument_dpll(["p | q"], "p", limits=Limits(decisions=0))["status"],
            "unknown",
        )
        for limits in (Limits(work=True), Limits(variables=65), Limits(decisions=-1)):
            with self.assertRaises(ValueError):
                check_argument_dpll([], "p", limits=limits)

    def test_partial_core_is_not_labeled_minimal(self):
        results = [
            check_argument_dpll(["p", "~p", "q"], "r", limits=Limits(work=w))
            for w in range(1, 100)
        ]
        partial = [r for r in results if r.get("core_kind") == "not_minimized"]
        self.assertTrue(partial)
        for result in partial:
            self.assertEqual(
                check_argument(result["conflicting_premises"], "FALSE")["status"],
                "inconsistent_premises",
            )

    def test_stats_and_projection_are_deterministic(self):
        first = check_argument_dpll(["a1 | a2", "a2 -> b"], "a1")
        self.assertEqual(first, check_argument_dpll(["a1 | a2", "a2 -> b"], "a1"))
        self.assertEqual(set(first["counterexample"]), {"a1", "a2", "b"})
        self.assertEqual(
            ast_data(parse("p -> ~q")),
            {
                "op": "implies",
                "args": [
                    {"op": "var", "name": "p"},
                    {"op": "not", "args": [{"op": "var", "name": "q"}]},
                ],
            },
        )

    def test_original_cli_selects_dpll_without_changing_reference_default(self):
        import contextlib
        import io
        import json
        from pathlib import Path
        import tempfile
        from argscope.__main__ import main

        document = {
            "premises": [
                {"id": "P1", "formula": " | ".join("p" + str(i) for i in range(20))}
            ],
            "steps": [{"id": "S1", "formula": "TRUE", "depends_on": ["P1"]}],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "argument.json"
            path.write_text(json.dumps(document))
            for backend, expected in [("exhaustive", "unknown"), ("dpll", "valid")]:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main([str(path), "--backend", backend]), 0)
                self.assertEqual(
                    json.loads(output.getvalue())["steps"][0]["status"], expected
                )
