import itertools
import json
from pathlib import Path
import unittest

from argscope import parse, check_argument, analyze_document, affected_steps, FormulaError, DocumentError
from argscope.__main__ import render_html


class LogicTests(unittest.TestCase):
    def test_standard_arguments(self):
        cases = [(["p -> q", "p"], "q", "valid"), (["p -> q", "~q"], "~p", "valid"),
                 (["p | q", "~p"], "q", "valid"), (["p -> q", "q"], "p", "invalid"),
                 (["p -> q", "~p"], "~q", "invalid"), ([], "p | ~p", "valid"),
                 ([], "FALSE", "invalid"), (["FALSE"], "p", "inconsistent_premises"),
                 (["p <-> q", "p"], "q", "valid")]
        for premises, conclusion, expected in cases:
            with self.subTest(premises=premises, conclusion=conclusion):
                self.assertEqual(check_argument(premises, conclusion)["status"], expected)

    def test_counterexample_is_a_witness(self):
        result = check_argument(["p -> q", "q"], "p")
        row = result["counterexample"]
        self.assertTrue(parse("p -> q").evaluate(row))
        self.assertTrue(row["q"])
        self.assertFalse(row["p"])

    def test_core_is_subset_minimal(self):
        result = check_argument(["p", "~p", "q", "r"], "s")
        self.assertEqual(result["conflicting_indices"], [0, 1])
        for index in range(2):
            reduced = result["conflicting_premises"][:index] + result["conflicting_premises"][index+1:]
            self.assertNotEqual(check_argument(reduced, "s")["status"], "inconsistent_premises")

    def test_operator_semantics_against_independent_python_truth_functions(self):
        for p, q, r in itertools.product((False, True), repeat=3):
            row = {"p": p, "q": q, "r": r}
            expected = {"p -> q -> r": (not p) or (not q) or r,
                        "p | q & r": p or (q and r), "~p & q": (not p) and q,
                        "p <-> q": p == q, "!(p | q)": not (p or q)}
            for formula, value in expected.items():
                self.assertEqual(parse(formula).evaluate(row), value)

    def test_unicode_and_constants(self):
        self.assertEqual(check_argument(["p → q", "p"], "q")["status"], "valid")
        self.assertEqual(check_argument([], "⊤")["status"], "valid")

    def test_invalid_inputs(self):
        for text in ("", "p q", "p && q", "(p", "p)", "p <-> q <-> r", "__import__('os')", None):
            with self.subTest(text=text), self.assertRaises(FormulaError):
                parse(text)

    def test_limit_is_unknown_not_invalid(self):
        self.assertEqual(check_argument(["p"], "q", max_variables=1)["status"], "unknown")
        with self.assertRaises(FormulaError):
            check_argument([], "p", max_variables=40)


class GraphTests(unittest.TestCase):
    def example(self, name):
        return json.loads((Path(__file__).parent.parent / "examples" / name).read_text())

    def test_valid_chain_and_transitive_invalidation(self):
        document = self.example("valid_chain.json")
        self.assertTrue(analyze_document(document)["all_steps_valid"])
        self.assertEqual(affected_steps(document, "P1"), ["S1", "S2"])
        self.assertEqual(affected_steps(document, "P3"), ["S2"])
        document["premises"][0]["formula"] = "q -> p"
        self.assertEqual([r["status"] for r in analyze_document(document)["steps"]], ["invalid", "blocked"])

    def test_invalid_step_blocks_dependents(self):
        result = analyze_document(self.example("invalid_chain.json"))
        self.assertEqual(result["steps"][1]["blocked_by"], ["S1"])

    def test_inconsistency_explanation_references_nodes(self):
        report = analyze_document(self.example("inconsistent.json"))
        self.assertEqual(report["steps"][0]["conflicting_nodes"], ["P1", "P2"])

    def test_order_independent_input(self):
        document = self.example("valid_chain.json")
        document["steps"].reverse()
        self.assertTrue(analyze_document(document)["all_steps_valid"])

    def test_bad_graphs(self):
        cases = [
            {"premises":[{"id":"p", "formula":"p"},{"id":"p", "formula":"q"}]},
            {"steps":[{"id":"s", "formula":"p", "depends_on":["missing"]}]},
            {"steps":[{"id":"s", "formula":"p", "depends_on":["s"]}]},
            {"steps":[{"id":"s", "formula":"p", "depends_on":["t"]},{"id":"t", "formula":"q", "depends_on":["s"]}]},
            {"premises":[{"id":"p", "formula":"p", "depends_on":["p"]}]},
        ]
        for document in cases:
            with self.subTest(document=document), self.assertRaises(DocumentError):
                analyze_document(document)

    def test_html_escapes_document_text(self):
        report = analyze_document(self.example("valid_chain.json"))
        report["title"] = "<script>alert(1)</script>"
        self.assertNotIn("<script>", render_html(report))
        self.assertIn("&lt;script&gt;", render_html(report))


if __name__ == "__main__":
    unittest.main()
