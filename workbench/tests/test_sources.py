"""SourceLens integration using real temporary versioned SQLite stores."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sourcelens.store import Store
from sourcelens.documents import chunk_markdown
from workbench.legacy.sources import bind, verify_source
from workbench.legacy.runner import run, replay
from workbench.__main__ import main

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "invalid_control.json"


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.sqlite3"
        self.store = Store(self.path)
        self.body = "Assume p implies q. Then consider q. 🦇"
        self.version = self.ingest(self.body)
        self.chunk = self.store.chunks([self.version])[0]
        self.case = json.loads(EXAMPLE.read_text())

    def ingest(self, body):
        return self.store.ingest(
            source_key="example",
            title="Control",
            label="v1",
            source_url=None,
            data=body.encode(),
            chunks=chunk_markdown(body),
        )["version_id"]

    def bound(self):
        return bind(
            self.case,
            self.path,
            self.version,
            self.chunk["id"],
            0,
            len(self.body),
            self.body,
        )

    def test_binding_resets_review_and_requires_database(self):
        case = self.bound()
        self.assertEqual(case["reading"]["review"]["status"], "draft")
        with self.assertRaises(ValueError):
            run(case)
        self.assertEqual(run(case, self.path)["result"]["outcome"], "review_required")
        case["reading"]["review"]["status"] = "self_reviewed"
        bundle = run(case, self.path)
        self.assertEqual(bundle["result"]["outcome"], "witness_found")
        self.assertTrue(replay(bundle, self.path)["source_check"]["is_current"])
        self.assertNotIn("source_check", replay(bundle))
        self.assertNotIn(str(self.path), json.dumps(bundle))

    def test_new_import_marks_old_source_without_changing_bundle(self):
        case = self.bound()
        bundle = run(case, self.path)
        before = deepcopy(bundle)
        self.ingest("A different current version.")
        self.assertFalse(replay(bundle, self.path)["source_check"]["is_current"])
        self.assertEqual(bundle, before)
        self.assertEqual(verify_source(case, self.path)["version_id"], self.version)

    def test_wrong_version_text_offsets_and_boolean_indices(self):
        other = self.ingest("New version.")
        for version, start, end, expected in (
            (other, 0, len(self.body), self.body),
            (self.version, -1, 2, "As"),
            (self.version, 0, 2, "XX"),
            (self.version, False, 2, "As"),
            (self.version, 0, len(self.body) + 1, self.body),
        ):
            with self.subTest(version=version, start=start):
                with self.assertRaises(ValueError):
                    bind(
                        self.case,
                        self.path,
                        version,
                        self.chunk["id"],
                        start,
                        end,
                        expected,
                    )

    def test_unicode_selection_and_parsed_provenance(self):
        start = self.body.index("🦇")
        case = bind(
            self.case, self.path, self.version, self.chunk["id"], start, start + 1, "🦇"
        )
        self.assertEqual(case["source"]["text"], "🦇")
        self.assertEqual(case["source"]["passage"]["parser_version"], "paragraph-v1")
        self.assertEqual(verify_source(case, self.path)["status"], "source_verified")

    def test_modified_chunk_or_provenance_is_rejected(self):
        case = self.bound()
        changed = deepcopy(case)
        changed["source"]["passage"]["upload_digest"] = "0" * 64
        with self.assertRaises(ValueError):
            verify_source(changed, self.path)
        with self.store.connection(write=True) as db:
            db.execute(
                "UPDATE chunks SET body=? WHERE id=?",
                (self.body + " Changed.", self.chunk["id"]),
            )
        with self.assertRaises(ValueError):
            verify_source(case, self.path)

    def test_read_only_adapter_does_not_initialize_or_change_schema(self):
        missing = Path(self.temp.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.Error):
            bind(self.case, missing, self.version, self.chunk["id"], 0, 2, "As")
        self.assertFalse(missing.exists())
        before = self.path.read_bytes()
        self.bound()
        self.assertEqual(self.path.read_bytes(), before)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
        with self.assertRaises(ValueError):
            self.bound()

    def test_cli_binding_execution_and_source_checked_replay(self):
        case_file = Path(self.temp.name) / "case.json"
        bundle_file = Path(self.temp.name) / "bundle.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "bind",
                        str(EXAMPLE),
                        "--source-db",
                        str(self.path),
                        "--version",
                        self.version,
                        "--chunk",
                        self.chunk["id"],
                        "--start",
                        "0",
                        "--end",
                        str(len(self.body)),
                        "--expect-text",
                        self.body,
                        "--output",
                        str(case_file),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "run",
                        str(case_file),
                        "--source-db",
                        str(self.path),
                        "--output",
                        str(bundle_file),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(["replay", str(bundle_file), "--source-db", str(self.path)]), 0
            )

    def test_linked_multi_reading_review_and_source_replay(self):
        from workbench.legacy import reconstruction as r

        document = r.upgrade(self.bound(), "linked-reconstruction")
        for aspect in r.ASPECTS:
            document = r.review(
                document,
                "control-reading",
                aspect,
                "self_reviewed",
                "fixture author",
                "Authored test control",
            )
        bundle = r.execute(document, self.path)
        self.assertEqual(
            bundle["results"][0]["analysis"]["result"]["outcome"], "witness_found"
        )
        self.ingest("New current source.")
        self.assertFalse(
            r.replay_reconstruction(bundle, self.path)["source_checks"][0]["is_current"]
        )

    def test_annotated_report_checks_actual_source_version(self):
        from workbench.legacy import reconstruction as r
        from workbench.legacy.semantic_ir import annotate
        from workbench.legacy.report import render

        doc = annotate(r.upgrade(self.bound(), "annotated-source"))
        for aspect in r.ASPECTS:
            doc = r.review(
                doc,
                "control-reading",
                aspect,
                "self_reviewed",
                "fixture author",
                "Authored source control",
            )
        bundle = r.execute(doc, self.path)
        self.assertIn(
            "Stored source verified · current version", render(bundle, self.path)
        )
        self.ingest("A new version for the same source key.")
        self.assertIn(
            "Stored source verified · historical version", render(bundle, self.path)
        )
        self.assertIn("Frozen snapshot · library not checked", render(bundle))
