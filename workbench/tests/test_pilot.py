"""Pilot isolation, paired inputs, failure retention and scoring integrity; no LLM."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from workbench.review.providers import DemoProvider, examples
from workbench.review.provenance import source_snapshot, object_hash, sha
from workbench.version import __version__

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pilot", ROOT / "workbench/tools/run_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class RecordingProvider(DemoProvider):
    name = "test-fixture-only"

    def __init__(self, fail_at=None):
        self.inputs = []
        self.fail_at = fail_at

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        self.inputs.append(deepcopy({"operation": operation, "request": request, "draft": draft, "issues": issues, "evidence": evidence}))
        if len(self.inputs) == self.fail_at:
            raise ValueError("Authored test failure")
        return super().generate(operation, request, draft, issues, evidence)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = pilot.read(pilot.CONFIG)
        request = examples()[1]["request"]
        self.corpus = {"protocol": config["protocol"], "config_sha256": object_hash(config), "cases": [
            {"id": s["id"], "request": deepcopy(request), "request_sha256": object_hash(request),
             "text_sha256": sha(request["sources"][0]["text"].encode())} for s in config["cases"]]}
        self.path = self.root / "corpus.json"
        pilot.write(self.path, self.corpus)

    def run_fixture(self, **kwargs):
        provider = kwargs.pop("provider", RecordingProvider())
        output = self.root / "run"
        result = pilot.run(self.path, output, provider, "test-controlled", **kwargs)
        return provider, output, result

    def completed_synthetic_ratings(self, output):
        ratings = pilot.read(output / "ratings-template.json")
        ratings.update(status="completed", reviewer="Synthetic test author", reviewer_role="Fixture only",
                       transcription_checked_against_pdf=True, reviewed_on="2026-11-24", built_by_reviewer=True)
        for r in ratings["ratings"]:
            r["issues"] = []
        for e in ratings["human_edits"]:
            e.update(active_seconds=0, decision="no_changes", residual_issues=[])
        path = self.root / "ratings.json"
        pilot.write(path, ratings)
        return ratings, path

    def test_plan_is_zero_calls_and_input_tampering_is_rejected(self):
        self.assertEqual(pilot.plan(self.path)["model_calls_made"], 0)
        self.assertEqual(pilot.plan(self.path)["maximum_model_calls"], 20)
        self.corpus["cases"][0]["request"]["notes"] = "changed"
        self.path.write_text(json.dumps(self.corpus))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            pilot.plan(self.path)

    def test_paired_arms_reuse_initial_and_match_call_budget(self):
        provider, output, result = self.run_fixture(fixture=True)
        self.assertEqual(result["status"], "completed_awaiting_human")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["fixture_calls"], 20)
        for i in range(0, 20, 5):
            draft, critique1, revise1, critique2, revise2 = provider.inputs[i:i+5]
            initial = DemoProvider().generate("draft", draft["request"])
            initial["provider"] = provider.name
            for invocation in [critique1, revise1, critique2, revise2]:
                self.assertEqual(invocation["draft"], initial)
                self.assertEqual(invocation["evidence"], [])
            self.assertEqual([c["operation"] for c in provider.inputs[i:i+5]], ["draft", "critique", "revise", "critique", "revise"])
        self.assertTrue(all(c["status"] == "validated" for c in result["calls"]))
        ratings, path = self.completed_synthetic_ratings(output)
        with self.assertRaisesRegex(ValueError, "real runs"):
            pilot.score(output, path, self.path)

    def test_failure_is_recorded_and_not_retried(self):
        provider, output, result = self.run_fixture(provider=RecordingProvider(fail_at=2), fixture=True)
        self.assertEqual(result["status"], "failed_no_retry")
        self.assertEqual(len(provider.inputs), 2)
        self.assertEqual(result["calls"][-1]["status"], "failed")
        self.assertTrue((output / (result["calls"][-1]["id"] + ".receipt.json")).exists())
        with self.assertRaises(FileExistsError):
            pilot.run(self.path, output, provider, "test-controlled", fixture=True)

    def test_client_default_model_is_rejected_before_calls(self):
        provider = RecordingProvider()
        with self.assertRaisesRegex(ValueError, "explicit model"):
            pilot.run(self.path, self.root / "never", provider, "client-default")
        self.assertEqual(provider.inputs, [])

    def test_scoring_tracks_repairs_new_errors_and_requires_a4(self):
        # Exercise the live-run format with a local authored function; no external calls.
        _, output, _ = self.run_fixture(fixture=False)
        ratings, path = self.completed_synthetic_ratings(output)
        case = self.corpus["cases"][0]["id"]
        for r in ratings["ratings"]:
            if r["case"] == case and r["arm"] in ("A1", "A2"):
                r["issues"] = [{"id": "old" if r["arm"] == "A1" else "new", "category": "attribution",
                                "severity": "major", "anchor": "synthetic P1", "rationale": "Authored fixture discrepancy"}]
        path.write_text(json.dumps(ratings))
        value = pilot.score(output, path, self.path)
        transition = next(v for v in value["transitions"] if v["case"] == case and v["arm"] == "A2")
        self.assertEqual(transition["repaired_ids"], ["old"])
        self.assertEqual(transition["new_ids"], ["new"])
        self.assertTrue(all(v["changed_leaf_fields"] == 0 for v in value["human_edits"]))
        ratings["human_edits"][0]["residual_issues"] = None
        path.write_text(json.dumps(ratings))
        with self.assertRaisesRegex(ValueError, "residual issues"):
            pilot.score(output, path, self.path)

    def test_incomplete_or_rebound_ratings_are_rejected(self):
        _, output, _ = self.run_fixture(fixture=False)
        ratings, path = self.completed_synthetic_ratings(output)
        for field, replacement in [("issues", None), ("sha256", "0" * 64)]:
            damaged = deepcopy(ratings)
            damaged["ratings"][0][field] = replacement
            path.write_text(json.dumps(damaged))
            with self.subTest(field=field), self.assertRaises(ValueError):
                pilot.score(output, path, self.path)
        damaged = deepcopy(ratings)
        damaged["human_edits"][0]["reviewed_a3_sha256"] = "0" * 64
        path.write_text(json.dumps(damaged))
        with self.assertRaisesRegex(ValueError, "exact A3 hash"):
            pilot.score(output, path, self.path)

    def test_source_change_blocks_scoring(self):
        actual = source_snapshot(ROOT)
        changed = {**actual, "sha256": "0" * 64}
        with patch.object(pilot, "source_snapshot", side_effect=[actual, changed]):
            _, output, result = self.run_fixture(fixture=False)
        self.assertFalse(result["source_unchanged"])
        _, path = self.completed_synthetic_ratings(output)
        with self.assertRaisesRegex(ValueError, "unchanged source"):
            pilot.score(output, path, self.path)

    def test_manifest_excludes_outputs_credentials_and_changes_with_code(self):
        root = self.root / "source"
        package = root / "workbench/workbench"
        package.mkdir(parents=True)
        source = package / "main.py"
        source.write_text("x=1\n")
        private = root / "workbench/outputs"
        private.mkdir()
        (private / "secret.json").write_text('{"key":"private"}')
        (package / ".env").write_text("SECRET=private")
        metadata = root / "workbench/pyproject.toml"
        metadata.write_text('[project]\nname="fixture"\nversion="1"\n')
        before = source_snapshot(root)
        self.assertEqual(set(before["files"]), {"workbench/workbench/main.py", "workbench/pyproject.toml"})
        self.assertIsNone(before["git"])
        source.write_text("x=2\n")
        self.assertNotEqual(before["sha256"], source_snapshot(root)["sha256"])
        before_metadata = source_snapshot(root)["sha256"]
        metadata.write_text('[project]\nname="fixture"\nversion="2"\n')
        self.assertNotEqual(before_metadata, source_snapshot(root)["sha256"])

    def test_release_id_matches_wrappers_api_and_builder(self):
        from workbench.review_app import create_app
        from fastapi.testclient import TestClient
        builder_spec = importlib.util.spec_from_file_location("builder", ROOT / "workbench/tools/build_review_mvp.py")
        builder = importlib.util.module_from_spec(builder_spec)
        builder_spec.loader.exec_module(builder)
        self.assertEqual(builder.VERSION, __version__)
        from setuptools.config.pyprojecttoml import read_configuration
        config = read_configuration(str(ROOT / "workbench/pyproject.toml"))
        self.assertEqual(config["project"]["version"], __version__)
        for name in ["ArgScope.app", "ArgScope Codex.app"]:
            with (ROOT / "workbench" / name / "Contents/Info.plist").open("rb") as f:
                plist = plistlib.load(f)
            self.assertEqual(plist["CFBundleShortVersionString"], __version__)
            self.assertEqual(plist["CFBundleVersion"], __version__)
        # Do not enter app lifespan or launch services. Health catches unavailable KV.
        app = create_app(self.root / "api", 65534, namespace="version-test", provider=DemoProvider(), run_source_worker=False)
        self.addCleanup(app.state.review.close)
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.get("/openapi.json").json()["info"]["version"], __version__)
        with patch.object(app.state.review.flow.shared.client, "command", return_value=b"PONG"):
            self.assertEqual(client.get("/api/argument-health").json()["version"], __version__)


if __name__ == "__main__":
    unittest.main()
