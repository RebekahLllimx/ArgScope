"""Original-source tasks and conditional formal controls, without model calls."""
import unittest
from workbench.review.research_examples import research_examples
from workbench.review.schemas import Request
from falsifylab.search import search_claim


class PaperCaseTests(unittest.TestCase):
    def check(self, premises, conclusion):
        return search_claim(
            {
                "semantics": "classical-propositional-v1",
                "premises": premises,
                "conclusion": conclusion,
            },
            backend="dpll",
        )

    def test_original_sources_and_question_contracts(self):
        cases = research_examples()
        self.assertEqual(len({c["id"] for c in cases}), 3)
        for c in cases:
            req = Request.model_validate(c["request"])
            self.assertEqual(req.sources[0].kind, "original_excerpt")
            self.assertNotIn("Li Muxiao", req.sources[0].title)

    def test_chalmers_numbered_inference_is_valid(self):
        self.assertEqual(
            self.check(["p", "p -> q"], "q")["outcome"], "no_counterexample_complete"
        )

    def test_deleting_conditional_exposes_verified_counterexample(self):
        result = self.check(["p"], "q")
        self.assertEqual(result["outcome"], "witness_found")
        self.assertEqual(result["validation"]["status"], "formal_witness_verified")

    def test_mcx_requires_additional_meaningfulness_assumption(self):
        # p=Pegasus is; m=relevant discourse is meaningful. The abstraction and
        # the added m premise require separate interpretation review.
        self.assertEqual(self.check(["~p -> ~m"], "p")["outcome"], "witness_found")
        self.assertEqual(
            self.check(["~p -> ~m", "m"], "p")["outcome"], "no_counterexample_complete"
        )

    def test_validity_does_not_imply_conclusion_true(self):
        from argscope.logic import parse

        assignment = {"p": False, "q": False}
        self.assertFalse(parse("p").evaluate(assignment))
        self.assertTrue(parse("p -> q").evaluate(assignment))
        self.assertFalse(parse("q").evaluate(assignment))
