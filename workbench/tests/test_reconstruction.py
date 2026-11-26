"""Alternative-reading, stale-review, graph and immutable-history integration."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from workbench.legacy import reconstruction as r, history
from workbench.__main__ import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "invalid_control.json"


def document():
    return r.upgrade(json.loads(EXAMPLE.read_text()), "control-reconstruction")


def reviewed(doc, reading=None):
    for aspect in r.ASPECTS:
        doc = r.review(
            doc,
            reading or doc["readings"][0]["id"],
            aspect,
            "self_reviewed",
            "test author",
            "Explicit authored control",
        )
    return doc


class ReconstructionTests(unittest.TestCase):
    def test_upgrade_requires_new_reviews_and_roundtrips(self):
        doc = document()
        self.assertEqual(
            r.execute(doc)["results"][0]["analysis"]["result"]["outcome"],
            "review_required",
        )
        bundle = r.execute(reviewed(doc))
        self.assertEqual(
            r.replay_reconstruction(bundle)["outcomes"],
            {"control-reading": "witness_found"},
        )
        bundle["document"]["readings"][0]["nodes"][0]["speaker"] = "Different speaker"
        with self.assertRaises(ValueError):
            r.replay_reconstruction(bundle)

    def test_alternatives_run_separately_and_review_does_not_transfer(self):
        doc = reviewed(document())
        alternative = deepcopy(doc["readings"][0])
        alternative["id"] = "alternative"
        alternative["nodes"][1]["formula"] = "p"
        alternative["nodes"][-1]["formula"] = "q"
        doc["readings"].append(alternative)
        result = r.execute(doc)
        self.assertEqual(
            [x["analysis"]["result"]["outcome"] for x in result["results"]],
            ["witness_found", "review_required"],
        )
        doc = reviewed(doc, "alternative")
        self.assertEqual(
            r.execute(doc)["results"][1]["analysis"]["result"]["outcome"],
            "no_counterexample_complete",
        )

    def test_each_semantic_change_invalidates_review(self):
        base = reviewed(document())
        for mutate in (
            lambda d: d["source"].update(text="Changed text"),
            lambda d: d["readings"][0]["nodes"][0].update(
                commitment="opponent_position"
            ),
            lambda d: d["readings"][0]["nodes"][0].update(scope="Hypothetical scope"),
            lambda d: d["readings"][0]["nodes"][0].update(formula="p"),
            lambda d: d["readings"][0].update(summary="Changed interpretation"),
        ):
            doc = deepcopy(base)
            mutate(doc)
            self.assertEqual(
                r.execute(doc)["results"][0]["analysis"]["result"]["outcome"],
                "review_required",
            )

    def test_dispute_overrides_one_review_aspect(self):
        doc = reviewed(document())
        doc = r.review(
            doc,
            "control-reading",
            "fidelity",
            "disputed",
            "other reviewer",
            "Disputed reading",
        )
        self.assertEqual(
            r.execute(doc)["results"][0]["analysis"]["result"]["outcome"],
            "review_required",
        )
        self.assertEqual(
            r.review_status(doc, doc["readings"][0])["fidelity"], "disputed"
        )

    def test_failed_prerequisite_blocks_selected_target(self):
        doc = document()
        nodes = doc["readings"][0]["nodes"]
        nodes.append(
            {
                "id": "S2",
                "formula": "q",
                "role": "inference",
                "speaker": "control author",
                "commitment": "user_assumption",
                "scope": "Dependent inference",
                "depends_on": ["S1"],
            }
        )
        doc["readings"][0]["target"] = "S2"
        doc["readings"][0]["origin"] = "user_defined"
        result = r.execute(reviewed(doc))["results"][0]["analysis"]["result"]
        self.assertEqual(result, {"outcome": "blocked", "blocked_by": ["S1"]})

    def test_cycles_missing_refs_duplicates_and_limits_rejected(self):
        for mutate in (
            lambda d: d["readings"][0]["nodes"][-1].update(depends_on=["S1"]),
            lambda d: d["readings"][0]["nodes"][-1].update(depends_on=["absent"]),
            lambda d: d["readings"].append(deepcopy(d["readings"][0])),
            lambda d: d["readings"][0]["nodes"][0].update(role="definition"),
        ):
            doc = document()
            mutate(doc)
            with self.assertRaises(ValueError):
                r.validate(doc)

    def test_history_immutable_revisions_and_append_only_reviews(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "history.sqlite3"
            first = document()
            saved = history.save(first, db)
            approved = reviewed(first)
            second = history.save(approved, db, saved["snapshot_hash"])
            proposed = deepcopy(approved)
            proposed["readings"][0]["nodes"][0]["speaker"] = "New attribution"
            revised = r.revise(approved, proposed)
            third = history.save(revised, db, second["snapshot_hash"])
            self.assertEqual(history.load(db, saved["snapshot_hash"]), first)
            self.assertEqual(history.load(db, third["snapshot_hash"]), revised)
            self.assertEqual(
                r.execute(revised)["results"][0]["analysis"]["result"]["outcome"],
                "review_required",
            )
            with self.assertRaises(ValueError):
                history.save(revised, db, second["snapshot_hash"])
            erased = deepcopy(revised)
            erased["reviews"] = []
            with self.assertRaises(ValueError):
                history.save(erased, db, third["snapshot_hash"])

    def test_competing_saves_have_one_winner(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "history.sqlite3"
            doc = document()
            first = history.save(doc, db)

            def attempt(aspect):
                try:
                    history.save(
                        r.review(
                            doc,
                            "control-reading",
                            aspect,
                            "self_reviewed",
                            "author",
                            "Control",
                        ),
                        db,
                        first["snapshot_hash"],
                    )
                    return True
                except ValueError:
                    return False

            with ThreadPoolExecutor(2) as executor:
                self.assertEqual(
                    sorted(executor.map(attempt, ("fidelity", "attribution"))),
                    [False, True],
                )

    def test_cli_upgrade_review_analyze_save_load_and_replay(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(
            io.StringIO()
        ):
            root = Path(folder)
            doc = root / "doc.json"
            self.assertEqual(
                main(
                    [
                        "upgrade",
                        str(EXAMPLE),
                        "--id",
                        "cli-control",
                        "--output",
                        str(doc),
                    ]
                ),
                0,
            )
            for i, aspect in enumerate(r.ASPECTS):
                output = root / ("review" + str(i) + ".json")
                self.assertEqual(
                    main(
                        [
                            "review",
                            str(doc),
                            "--reading",
                            "control-reading",
                            "--aspect",
                            aspect,
                            "--decision",
                            "self_reviewed",
                            "--reviewer",
                            "author",
                            "--rationale",
                            "Control",
                            "--output",
                            str(output),
                        ]
                    ),
                    0,
                )
                doc = output
            bundle = root / "bundle.json"
            self.assertEqual(main(["analyze", str(doc), "--output", str(bundle)]), 0)
            self.assertEqual(main(["replay", str(bundle)]), 0)
            db = root / "history.sqlite3"
            self.assertEqual(main(["save", str(doc), "--history-db", str(db)]), 0)
            from workbench.contracts import digest

            self.assertEqual(
                main(
                    [
                        "load",
                        "--history-db",
                        str(db),
                        "--snapshot",
                        digest(json.loads(doc.read_text())),
                        "--output",
                        str(root / "loaded.json"),
                    ]
                ),
                0,
            )
