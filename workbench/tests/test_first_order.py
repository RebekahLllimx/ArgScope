"""FOL soundness boundaries, countermodel replay and review/transport integration."""
from copy import deepcopy
import unittest
from workbench.review.first_order import check, parse, validate_model
from workbench.review.research_examples import research_examples
from workbench.review.schemas import validate_draft
from workbench.review.tool_worker import calculate, TOOL_VERSION

NEG_UNIQUE = "~(exists x. (W(x) & forall y. (W(y) -> y = x)))"
ZERO_OR_TWO = "(forall x. ~W(x)) | (exists x. (W(x) & exists y. (W(y) & ~(x = y))))"


def fixture():
    req = next(
        c["request"]
        for c in research_examples()
        if c["id"] == "quine-description-first-order"
    )
    text = req["sources"][0]["text"]

    def ref(quote):
        start = text.index(quote)
        return {
            "source_id": "main",
            "start": start,
            "end": start + len(quote),
            "quote": quote,
        }

    draft = {
        "schema_version": "argument-draft-v1",
        "title": "Quine: negative unique description",
        "summary": "A guided conditional reconstruction; no claim that the historical denial is true.",
        "provider": "authored-fol-control-v1",
        "provenance": "Authored test reconstruction of the selected original phrases, not a live model or human endorsement.",
        "readings": [
            {
                "id": "description",
                "summary": "If there is not exactly one W author, there are zero or at least two.",
                "target": "C",
                "nodes": [
                    {
                        "id": "P1",
                        "content": "Suppose there is not exactly one author of Waverley.",
                        "role": "premise",
                        "speaker": "Conditional reconstruction",
                        "commitment": "hypothetical",
                        "source_refs": [ref("The author of Waverley is not")],
                        "depends_on": [],
                        "rationale": "Conditional supposition, not an assertion about actual authorship.",
                    },
                    {
                        "id": "C",
                        "content": "Under that supposition, either no object wrote Waverley or at least two distinct objects did.",
                        "role": "inference",
                        "speaker": "Quine explaining Russell",
                        "commitment": "hypothetical",
                        "source_refs": [
                            ref(
                                "Either each thing failed to write Waverley or two or more things wrote Waverley"
                            )
                        ],
                        "depends_on": ["P1"],
                        "rationale": "Checks the local translation, not the philosophical thesis of meaningfulness.",
                    },
                ],
                "qualifications": [
                    {
                        "kind": "declared_first_order_semantics",
                        "description": "W(x) means x wrote Waverley. Nonempty classical domain, identity, no author name constant.",
                        "representation": "first_order",
                        "rationale": "No existential import from a name; translation fidelity still requires review.",
                    }
                ],
                "formal_mapping": {
                    "semantics": "classical-first-order-v1",
                    "formulas": {"P1": NEG_UNIQUE, "C": ZERO_OR_TWO},
                    "rationale": "W is unary; bound x,y range over one nonempty domain. Negated uniqueness allows zero or more than one W.",
                },
            }
        ],
    }
    return req, draft


class FirstOrderTests(unittest.TestCase):
    def test_quine_equivalence_both_directions(self):
        for p, c in [(NEG_UNIQUE, ZERO_OR_TWO), (ZERO_OR_TWO, NEG_UNIQUE)]:
            r = check([p], c)
            self.assertEqual(r["outcome"], "no_counterexample_complete")
            self.assertEqual(r["evidence"]["kind"], "trusted-solver-unsat")
            self.assertFalse(r["evidence"]["independently_proof_checked"])

    def test_missing_uniqueness_shortcut_has_two_object_countermodel(self):
        r = check([NEG_UNIQUE], "forall x. ~W(x)")
        self.assertEqual(r["outcome"], "witness_found")
        self.assertEqual(r["finite_model"]["domain_size"], 2)
        self.assertEqual(
            validate_model([NEG_UNIQUE], "forall x. ~W(x)", r["finite_model"])[
                "status"
            ],
            "formal_witness_verified",
        )
        broken = deepcopy(r["finite_model"])
        broken["predicates"]["W"]["0"] = False
        with self.assertRaises(ValueError):
            validate_model([NEG_UNIQUE], "forall x. ~W(x)", broken)

    def test_adding_explicit_at_most_one_repairs_only_conditional_claim(self):
        unique = "forall x. forall y. ((W(x) & W(y)) -> x = y)"
        self.assertEqual(
            check([NEG_UNIQUE, unique], "forall x. ~W(x)")["outcome"],
            "no_counterexample_complete",
        )

    def test_quantifier_scope_is_not_interchangeable(self):
        self.assertEqual(
            check(["exists x. ~P(x)"], "~(exists x. P(x))")["outcome"], "witness_found"
        )
        self.assertEqual(
            check(["~(exists x. P(x))"], "forall x. ~P(x)")["outcome"],
            "no_counterexample_complete",
        )

    def test_universal_instantiation_and_existential_consequence(self):
        self.assertEqual(
            check(["forall x. (P(x) -> Q(x))", "exists x. P(x)"], "exists x. Q(x)")[
                "outcome"
            ],
            "no_counterexample_complete",
        )

    def test_equality_binary_predicate_and_shadowed_binders(self):
        self.assertEqual(
            check(["forall x. exists y. (x = y)"], "forall x. exists y. (y = x)")[
                "outcome"
            ],
            "no_counterexample_complete",
        )
        self.assertEqual(
            check(["forall x. forall y. R(x,y)"], "forall y. exists x. R(x,y)")[
                "outcome"
            ],
            "no_counterexample_complete",
        )
        self.assertEqual(
            check(
                ["forall x. (P(x) -> exists x. P(x))"],
                "forall x. (P(x) -> exists y. P(y))",
            )["outcome"],
            "no_counterexample_complete",
        )

    def test_nonempty_domain_and_no_predicates(self):
        self.assertEqual(
            check([], "exists x. x = x")["outcome"], "no_counterexample_complete"
        )
        r = check([], "forall x. exists y. ~(x = y)")
        self.assertEqual(r["outcome"], "witness_found")
        self.assertEqual(r["finite_model"]["domain_size"], 1)

    def test_inconsistent_premises_are_not_presented_as_substantive_validity(self):
        self.assertEqual(
            check(["exists x. P(x)", "forall x. ~P(x)"], "exists x. Q(x)")["outcome"],
            "inconsistent_assumptions",
        )

    def test_resource_exhaustion_is_not_validity(self):
        r = check(
            ["forall x. (P(x) -> Q(x))", "exists x. P(x)"], "exists x. Q(x)", rlimit=1
        )
        self.assertEqual(r["outcome"], "budget_exhausted")

    def test_reject_free_names_bad_syntax_arity_and_depth(self):
        for f in [
            "P(a)",
            "forall x. P(y)",
            "forall x. R(x,x,x)",
            "forall x. (P(x) & P(x,x))",
            "exists x. x = a",
            "[]P",
            "forall x. " + "(" * 50 + "P(x)" + ")" * 50,
        ]:
            with self.subTest(formula=f), self.assertRaises(ValueError):
                parse(f)
        with self.assertRaises(ValueError):
            check(["forall x. P(x)"], "forall x. forall y. P(x,y)")

    def test_candidate_audit_does_not_grant_human_approval(self):
        req, draft = fixture()
        validate_draft(draft, req)
        payload = {
            "operation": "audit",
            "request": req,
            "draft": draft,
            "human_review": None,
            "tool_version": TOOL_VERSION,
        }
        result = calculate(payload)["candidate_checks"][0]
        self.assertEqual(result["outcome"], "no_counterexample_complete")
        self.assertFalse(result["human_approved"])
        payload["operation"] = "formal"
        self.assertEqual(calculate(payload)["results"][0]["outcome"], "review_required")

    def test_approved_fixture_formal_replay_and_incomplete_mapping(self):
        req, draft = fixture()
        payload = {
            "operation": "formal",
            "request": req,
            "draft": draft,
            "human_review": {
                "fidelity": "accepted",
                "attribution": "accepted",
                "interpretation": "accepted",
                "approve_formal_mapping": True,
            },
            "tool_version": TOOL_VERSION,
        }
        a = calculate(payload)
        b = calculate(payload)
        self.assertEqual(a, b)
        del draft["readings"][0]["formal_mapping"]["formulas"]["P1"]
        self.assertEqual(
            calculate(payload)["results"][0]["outcome"], "incomplete_mapping"
        )

    def test_failed_first_order_predecessor_blocks_downstream(self):
        req, draft = fixture()
        r = draft["readings"][0]
        r["formal_mapping"]["formulas"]["C"] = "forall x. ~W(x)"
        r["nodes"].append({**r["nodes"][1], "id": "D", "depends_on": ["C"]})
        r["formal_mapping"]["formulas"]["D"] = "forall x. ~W(x)"
        r["target"] = "D"
        payload = {
            "operation": "audit",
            "request": req,
            "draft": draft,
            "human_review": None,
            "tool_version": TOOL_VERSION,
        }
        result = calculate(payload)["candidate_checks"][0]
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["blocked_by"], ["C"])

    def test_finite_model_tables_are_strict_and_complete(self):
        model = {"domain_size": 1, "predicates": {"P": {"0": True}}}
        for bad in [
            {**model, "domain_size": 0},
            {**model, "predicates": {"P": {"0": 1}}},
            {**model, "predicates": {"P": {}}},
        ]:
            with self.assertRaises(ValueError):
                validate_model([], "forall x. ~P(x)", bad)


class PaperExamplesTests(unittest.TestCase):
    def test_authored_paper_examples_are_explicit_and_executable(self):
        from workbench.review.paper_examples import PaperExamplesProvider

        provider = PaperExamplesProvider()
        self.assertFalse(provider.live)
        for case in research_examples():
            if case["id"] == "quine-mcx-original":
                continue
            from workbench.review.schemas import Request

            frozen = Request.model_validate(case["request"]).model_dump(mode="json")
            draft = provider.generate("draft", frozen)
            validate_draft(draft, case["request"])
            self.assertIn("authored", draft["provenance"])
            payload = {
                "operation": "audit",
                "request": case["request"],
                "draft": draft,
                "human_review": None,
                "tool_version": TOOL_VERSION,
            }
            check_result = calculate(payload)["candidate_checks"][0]
            self.assertEqual(check_result["outcome"], "no_counterexample_complete")
            self.assertFalse(check_result["human_approved"])
