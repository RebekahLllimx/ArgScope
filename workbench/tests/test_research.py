"""Contract and adversarial replay tests for the first research slice."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from falsifylab.search import validate_assignment
from workbench.__main__ import main, read_json
from workbench.contracts import validate_case
from workbench.legacy.runner import run, replay

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def sample():
    return json.loads((EXAMPLES / "invalid_control.json").read_text())


class ResearchTests(unittest.TestCase):
    def test_counterexample_and_replay(self):
        bundle = run(sample())
        self.assertEqual(bundle["result"]["assignment"], {"p": False, "q": True})
        self.assertEqual(
            bundle["result"]["validation"]["status"], "formal_witness_verified"
        )
        self.assertEqual(replay(bundle)["status"], "replay_verified")

    def test_complete_unknown_and_inconsistent(self):
        case = sample()
        case["claim"]["premises"] = ["p -> q", "p"]
        case["claim"]["conclusion"] = "q"
        self.assertEqual(run(case)["result"]["outcome"], "no_counterexample_complete")
        case["claim"]["premises"] = ["p", "~p"]
        self.assertEqual(run(case)["result"]["outcome"], "inconsistent_assumptions")
        case["claim"]["premises"] = []
        case["claim"]["conclusion"] = " | ".join("p" + str(i) for i in range(13))
        self.assertEqual(run(case)["result"]["outcome"], "budget_exhausted")

    def test_draft_and_conceptual_cases_do_not_produce_witnesses(self):
        case = sample()
        case["reading"]["review"]["status"] = "draft"
        self.assertEqual(run(case)["result"]["outcome"], "review_required")
        case["reading"]["review"]["status"] = "self_reviewed"
        case["claim"]["semantics"] = "conceptual-case-review-v1"
        case["claim"]["conclusion"] = "Knowledge"
        self.assertEqual(run(case)["result"]["outcome"], "unsupported_semantics")

    def test_tampering_is_rejected(self):
        original = run(sample())
        for mutate in (
            lambda b: b["case"]["source"].update(text="Changed source"),
            lambda b: b["case"]["reading"].update(speaker="Another speaker"),
            lambda b: b["result"]["assignment"].update(p=True),
            lambda b: b["result"]["assignment"].update(p=0),
            lambda b: b.update(scope="Automatically verified philosophy"),
        ):
            bundle = copy.deepcopy(original)
            mutate(bundle)
            with self.assertRaises(ValueError):
                replay(bundle)

    def test_independent_validator_rejects_incomplete_and_false_witnesses(self):
        for assignment in ({"p": False}, {"p": 0, "q": True}, {"p": True, "q": True}):
            with self.assertRaises(ValueError):
                validate_assignment(["p -> q", "q"], "p", assignment)

    def test_schema_and_mutation_attribution(self):
        case = sample()
        case["surprise"] = True
        with self.assertRaises(ValueError):
            validate_case(case)
        case = sample()
        case["reading"]["commitment"] = "author_position"
        with self.assertRaises(ValueError):
            validate_case(case)

    def test_cli_does_not_overwrite_and_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "bundle.json"
            self.assertEqual(
                main(
                    [
                        "run",
                        str(EXAMPLES / "invalid_control.json"),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            saved = output.read_bytes()
            self.assertEqual(main(["replay", str(output)]), 0)
            self.assertEqual(
                main(
                    [
                        "run",
                        str(EXAMPLES / "invalid_control.json"),
                        "--output",
                        str(output),
                    ]
                ),
                2,
            )
            self.assertEqual(output.read_bytes(), saved)
            bad = Path(folder) / "bad.json"
            bad.write_text('{"x":1,"x":2}')
            with self.assertRaises(ValueError):
                read_json(bad)

    def test_classic_examples_are_review_records_not_semantic_benchmarks(self):
        for path in EXAMPLES.glob("classic_*.json"):
            with self.subTest(path=path.name):
                case = json.loads(path.read_text())
                validate_case(case)
                self.assertEqual(case["source"]["kind"], "authored_paraphrase")
                self.assertEqual(run(case)["result"]["outcome"], "review_required")
