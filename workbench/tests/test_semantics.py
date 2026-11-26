"""Semantic annotations must preserve source identity and never invent semantics."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench.legacy import reconstruction as r, semantic_ir as ir, history
from workbench.__main__ import main, read_json
from workbench.legacy.report import render

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def document():
    case = json.loads((EXAMPLES / "invalid_control.json").read_text())
    doc = ir.annotate(r.upgrade(case, "semantic-control"))
    doc["source"]["text"] = "The bank is closed. The bank borders the river. 🦇"
    reading = doc["readings"][0]
    reading["senses"] = [
        {
            "id": "finance",
            "label": "Financial institution",
            "description": "A proposed reading of bank",
            "grounds": "Context supplied by the reviewer",
        },
        {
            "id": "river",
            "label": "River edge",
            "description": "A separate proposed reading",
            "grounds": "The explicit river context",
        },
    ]
    reading["occurrences"] = [
        {
            "id": "bank-1",
            "node_id": "P1",
            "start": 4,
            "end": 8,
            "surface": "bank",
            "sense_id": "finance",
            "context": "object_language",
        },
        {
            "id": "bank-2",
            "node_id": "P2",
            "start": 24,
            "end": 28,
            "surface": "bank",
            "sense_id": "river",
            "context": "object_language",
        },
    ]
    return doc


def reviewed(doc):
    for reading in list(doc["readings"]):
        for aspect in r.ASPECTS:
            doc = r.review(
                doc,
                reading["id"],
                aspect,
                "self_reviewed",
                "test fixture author",
                "Authored control; not expert review",
            )
    return doc


def qualifier(representation="unencoded"):
    return {
        "id": "belief-1",
        "node_id": "P1",
        "kind": "belief",
        "description": "The claim is under a belief operator",
        "representation": representation,
        "abstraction_rationale": "Check only the expressly supplied Boolean skeleton"
        if representation == "propositional_abstraction"
        else None,
    }


class SemanticTests(unittest.TestCase):
    def test_same_word_can_have_distinct_senses_without_formula_rewriting(self):
        doc = document()
        r.validate(doc)
        bundle = r.execute(reviewed(doc))
        self.assertEqual(bundle["schema_version"], "reconstruction-bundle-v2")
        self.assertEqual(
            bundle["results"][0]["analysis"]["result"]["outcome"], "witness_found"
        )
        merged = deepcopy(doc)
        merged["readings"][0]["occurrences"][1]["sense_id"] = "finance"
        result = r.execute(reviewed(merged))
        self.assertEqual(
            bundle["results"][0]["analysis"]["result"],
            result["results"][0]["analysis"]["result"],
        )
        self.assertNotEqual(
            bundle["results"][0]["reading_hash"], result["results"][0]["reading_hash"]
        )

    def test_unresolved_occurrence_blocks_even_after_all_review_annotations(self):
        doc = document()
        doc["readings"][0]["occurrences"][0]["sense_id"] = None
        bundle = r.execute(reviewed(doc))
        self.assertEqual(
            bundle["results"][0]["analysis"]["result"]["outcome"], "review_required"
        )
        self.assertEqual(
            bundle["results"][0]["semantic_scope"]["unresolved_occurrences"], ["bank-1"]
        )

    def test_unencoded_qualifications_never_call_search(self):
        doc = document()
        doc["readings"][0]["qualifications"] = [qualifier()]
        with patch(
            "workbench.legacy.runner.search_claim",
            side_effect=AssertionError("Must not search"),
        ):
            bundle = r.execute(reviewed(doc))
        self.assertEqual(
            bundle["results"][0]["analysis"]["result"]["outcome"],
            "unsupported_semantics",
        )
        self.assertIsNone(bundle["results"][0]["graph"])
        self.assertEqual(r.replay_reconstruction(bundle)["status"], "replay_verified")

    def test_quotation_and_belief_context_cannot_omit_qualification(self):
        for context in ("quotation", "belief_report"):
            doc = document()
            doc["readings"][0]["occurrences"][0]["context"] = context
            bundle = r.execute(reviewed(doc))
            self.assertEqual(
                bundle["results"][0]["analysis"]["result"]["outcome"],
                "unsupported_semantics",
            )

    def test_abstraction_requires_rationale_and_is_reported_as_conditional(self):
        doc = document()
        doc["readings"][0]["qualifications"] = [qualifier("propositional_abstraction")]
        result = r.execute(reviewed(doc))["results"][0]
        self.assertEqual(
            result["semantic_scope"]["level"], "propositional_abstraction_only"
        )
        self.assertEqual(result["analysis"]["result"]["outcome"], "witness_found")
        doc["readings"][0]["qualifications"][0]["abstraction_rationale"] = ""
        with self.assertRaises(ValueError):
            r.validate(doc)

    def test_annotations_and_grounds_are_bound_to_review(self):
        base = reviewed(document())
        for field in ("description", "grounds", "label"):
            doc = deepcopy(base)
            doc["readings"][0]["senses"][0][field] = "Changed interpretation"
            self.assertEqual(
                r.execute(doc)["results"][0]["analysis"]["result"]["outcome"],
                "review_required",
            )

    def test_invalid_annotation_ids_ranges_references_and_types(self):
        mutations = (
            lambda x: x["occurrences"][0].update(start=True),
            lambda x: x["occurrences"][0].update(end=999),
            lambda x: x["occurrences"][0].update(surface="other"),
            lambda x: x["occurrences"][0].update(sense_id="missing"),
            lambda x: x["occurrences"][0].update(node_id="missing"),
            lambda x: x["occurrences"][0].update(context="arbitrary"),
            lambda x: x["senses"].append(deepcopy(x["senses"][0])),
            lambda x: x["occurrences"].append(deepcopy(x["occurrences"][0])),
            lambda x: x.update(
                qualifications=[{**qualifier(), "representation": "magic"}]
            ),
            lambda x: x.update(
                qualifications=[{**qualifier(), "abstraction_rationale": "implicit"}]
            ),
            lambda x: x.update(qualifications=[{**qualifier(), "kind": "magic"}]),
            lambda x: x.update(qualifications=[{**qualifier(), "node_id": "missing"}]),
        )
        for mutate in mutations:
            doc = document()
            mutate(doc["readings"][0])
            with self.subTest(reading=doc["readings"][0]):
                with self.assertRaises(ValueError):
                    r.validate(doc)

    def test_unicode_offsets_and_empty_annotations(self):
        doc = document()
        index = doc["source"]["text"].index("🦇")
        doc["readings"][0]["occurrences"] = [
            {
                "id": "bat",
                "node_id": "P1",
                "start": index,
                "end": index + 1,
                "surface": "🦇",
                "sense_id": None,
                "context": "object_language",
            }
        ]
        r.validate(doc)
        doc["readings"][0]["occurrences"] = []
        self.assertEqual(ir.assess(doc["readings"][0])["status"], "executable")

    def test_bridge_is_an_explicit_assumption_and_does_not_add_formulas(self):
        doc = document()
        doc["readings"][0]["bridges"] = [
            {
                "id": "B1",
                "node_id": "P1",
                "rationale": "Declared bridge for this control",
                "source_basis": "Reader-added assumption, not an author quotation",
            }
        ]
        result = r.execute(reviewed(doc))["results"][0]["analysis"]["result"]
        self.assertEqual(result["kernel_result"]["status"], "invalid")
        doc["readings"][0]["nodes"][0]["commitment"] = "author_position"
        with self.assertRaises(ValueError):
            r.validate(doc)
        doc["readings"][0]["nodes"][0]["commitment"] = "user_assumption"
        doc["readings"][0]["bridges"][0]["node_id"] = "S1"
        with self.assertRaises(ValueError):
            r.validate(doc)

    def test_annotation_upgrade_preserves_old_bundle_and_invalidates_approval(self):
        legacy = r.upgrade(
            json.loads((EXAMPLES / "invalid_control.json").read_text()),
            "legacy-control",
        )
        legacy = reviewed(legacy)
        old = r.execute(legacy)
        upgraded = ir.annotate(legacy)
        self.assertEqual(
            r.execute(upgraded)["results"][0]["analysis"]["result"]["outcome"],
            "review_required",
        )
        self.assertEqual(r.replay_reconstruction(old)["status"], "replay_verified")
        self.assertEqual(ir.annotate(upgraded), upgraded)

    def test_diff_identifies_changed_fields_and_transitive_impact(self):
        before = document()
        reading = before["readings"][0]
        reading["nodes"].append(
            {
                "id": "S2",
                "formula": "p | q",
                "role": "inference",
                "speaker": "control author",
                "commitment": "user_assumption",
                "scope": "Dependent inference",
                "depends_on": ["S1"],
            }
        )
        after = deepcopy(before)
        after["readings"][0]["nodes"][0]["speaker"] = "Changed speaker"
        result = ir.changes(before, after)["readings"][0]
        self.assertEqual(
            result["node_changes"], [{"node_id": "P1", "fields": ["speaker"]}]
        )
        self.assertEqual(result["affected_inferences"], ["S1", "S2"])
        self.assertFalse(result["review_content_unchanged"])
        after["readings"][0]["senses"][0]["grounds"] = "New grounds"
        self.assertIn(
            "senses", ir.changes(before, after)["readings"][0]["reading_fields"]
        )

    def test_v2_history_preserves_reviews_and_old_snapshots(self):
        doc = document()
        doc["parent_revision"] = None
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.sqlite3"
            first = history.save(doc, path)
            approved = reviewed(doc)
            second = history.save(approved, path, first["snapshot_hash"])
            proposed = deepcopy(approved)
            proposed["readings"][0]["senses"][0]["grounds"] = "New grounds"
            revised = r.revise(approved, proposed)
            history.save(revised, path, second["snapshot_hash"])
            self.assertEqual(history.load(path, first["snapshot_hash"]), doc)
            self.assertEqual(
                r.execute(revised)["results"][0]["analysis"]["result"]["outcome"],
                "review_required",
            )

    def test_report_is_escaped_script_free_and_checks_bundle(self):
        doc = document()
        doc["source"]["key"] = "<script>alert(1)</script>"
        doc["readings"][0]["senses"][0]["grounds"] = "<img src=x onerror=alert(2)>"
        bundle = r.execute(reviewed(doc))
        html = render(bundle)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Frozen snapshot · library not checked", html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("Verified formal witness", html)
        bundle["results"][0]["analysis"]["result"]["assignment"]["p"] = True
        with self.assertRaises(ValueError):
            render(bundle)

    def test_annotate_diff_report_cli_and_non_object_input(self):
        legacy = r.upgrade(
            json.loads((EXAMPLES / "invalid_control.json").read_text()), "cli-control"
        )
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(
            io.StringIO()
        ):
            root = Path(folder)
            first, annotated, bundle, report = (
                root / n
                for n in ("first.json", "annotated.json", "bundle.json", "report.html")
            )
            first.write_text(json.dumps(legacy))
            self.assertEqual(
                main(["annotate", str(first), "--output", str(annotated)]), 0
            )
            self.assertEqual(
                main(["diff", str(annotated), "--previous", str(first)]), 0
            )
            self.assertEqual(
                main(["analyze", str(annotated), "--output", str(bundle)]), 0
            )
            self.assertEqual(main(["replay", str(bundle)]), 0)
            self.assertEqual(main(["report", str(bundle), "--output", str(report)]), 0)
            saved = report.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    main(["report", str(bundle), "--output", str(report)]), 2
                )
            self.assertEqual(report.read_bytes(), saved)
            bad = root / "list.json"
            bad.write_text("[]")
            with self.assertRaises(ValueError):
                read_json(bad)
