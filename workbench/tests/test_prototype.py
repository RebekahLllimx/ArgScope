"""Real loopback HTTP, portable backend replay and immutable prototype history."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from workbench.legacy import reconstruction as r
from workbench.legacy.prototype import (
    App,
    APIError,
    create_server,
    example_documents,
    inspect_document,
)
from workbench.legacy.runner import run, replay
from falsifylab.result_store import FileStore, StoreError, canonical, fingerprint


def trajectory_fixture(intervention_time=None):
    value = {
        "schema": "trajectory-input-v1",
        "model": {
            "adapter": "fixture",
            "source_file": "fixture.py",
            "source_sha256": "a" * 64,
            "numpy": "fixture",
            "scipy": "fixture",
            "adapter_version": 2,
        },
        "environment": {
            name: "fixture"
            for name in (
                "python",
                "implementation",
                "system",
                "machine",
                "numpy",
                "scipy",
                "grid_dtype",
                "rng",
            )
        },
        "dynamics": {
            "grid_size": 4,
            "steps": 6,
            "b": 1.5,
            "p": 0 if intervention_time is None else 0.2,
            "seed": 11,
            "intervention_time": intervention_time,
        },
        "initial_state_sha256": "b" * 64,
        "observation": "cooperation-and-grid-sha256-state-0-through-steps-v1",
    }
    value["environment"]["trajectory_adapter"] = 1
    result = {"cooperation": [0.5] * 7, "state_sha256": ["b" * 64] * 7}
    return {
        "schema": "trajectory-record-v1",
        "key": fingerprint(value),
        "input": value,
        "result": result,
        "result_sha256": fingerprint(result),
    }


def approved(doc):
    for reading in list(doc["readings"]):
        for aspect in r.ASPECTS:
            doc = r.review(
                doc,
                reading["id"],
                aspect,
                "self_reviewed",
                "fixture author",
                "Authored control only",
            )
    return doc


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(Path(self.temp.name) / "data")
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, payload=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        body = (
            raw
            if raw is not None
            else json.dumps(payload)
            if payload is not None
            else None
        )
        method = "POST" if body is not None else "GET"
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        ctype = response.getheader("Content-Type")
        conn.close()
        return (
            status,
            json.loads(data) if ctype.startswith("application/json") else data,
        )

    def document(self):
        return deepcopy(example_documents()[0]["document"])

    def test_legacy_namespace_keeps_documented_module_entry(self):
        from workbench import prototype as compatibility
        from workbench.legacy import prototype as implementation

        self.assertIs(compatibility.main, implementation.main)
        self.assertIn("/legacy/", implementation.__file__)

    def test_trajectory_reader_is_opt_in_and_cannot_select_a_server(self):
        payload = {"control_key": "a" * 64, "treated_key": "b" * 64, "window": 2}
        self.assertIsNone(self.request("/api/health")[1]["trajectory_store"])
        self.assertEqual(self.request("/api/measure", payload)[0], 503)
        self.assertEqual(
            self.request("/api/measure", {**payload, "port": 6379})[0], 400
        )

    def test_trajectory_measurement_http_and_store_failure(self):
        store = FileStore(Path(self.temp.name) / "trajectories")
        control, treated = trajectory_fixture(), trajectory_fixture(3)
        for record in (control, treated):
            store.put(record["key"], canonical(record))
        server = create_server(
            Path(self.temp.name) / "reader-data", trajectory_store=store
        )
        thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
        )
        thread.start()
        old = self.server
        try:
            self.server = server
            payload = {
                "control_key": control["key"],
                "treated_key": treated["key"],
                "window": 2,
            }
            status, result = self.request("/api/measure", payload)
            self.assertEqual((status, result["measurement"]["effect"]), (200, 0))
            self.assertEqual(
                self.request("/api/measure", {**payload, "window": 4})[0], 400
            )
            with patch.object(store, "get", side_effect=StoreError("offline")):
                self.assertEqual(self.request("/api/measure", payload)[0], 503)
            self.assertFalse((Path(self.temp.name) / "reader-data" / "pairs").exists())
        finally:
            self.server = old
            server.shutdown()
            server.server_close()
            thread.join()

    def test_health_examples_assets_and_inspection(self):
        status, result = self.request("/api/health")
        self.assertEqual((status, result["status"]), (200, "ok"))
        status, result = self.request("/api/examples")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["examples"]), 8)
        for example in result["examples"]:
            self.assertEqual(example["document"]["reviews"], [])
            r.validate(example["document"])
        self.assertIn(b"Make the reasoning visible", self.request("/")[1])
        info = self.request("/api/inspect", {"document": self.document()})[1]
        self.assertEqual(info["readings"][0]["asts"][0]["ast"]["op"], "implies")
        self.assertIn("reading_hash", info["readings"][0])

    def test_review_run_replay_and_report_flow(self):
        doc = self.document()
        draft = self.request("/api/analyze", {"document": doc, "backend": "dpll"})[1]
        self.assertEqual(
            draft["results"][0]["analysis"]["result"]["outcome"], "review_required"
        )
        for aspect in r.ASPECTS:
            status, value = self.request(
                "/api/review",
                {
                    "document": doc,
                    "reading_id": doc["readings"][0]["id"],
                    "aspect": aspect,
                    "decision": "self_reviewed",
                    "reviewer": "fixture author",
                    "rationale": "Authored control",
                },
            )
            self.assertEqual(status, 200)
            doc = value["document"]
        status, bundle = self.request(
            "/api/analyze", {"document": doc, "backend": "dpll"}
        )
        self.assertEqual(
            (status, bundle["schema_version"]), (200, "reconstruction-bundle-v3")
        )
        result = bundle["results"][0]["analysis"]["result"]
        self.assertEqual(result["validation"]["status"], "formal_witness_verified")
        self.assertEqual(result["kernel_result"]["backend"], "dpll-v1")
        self.assertEqual(
            self.request("/api/replay", {"bundle": bundle})[1]["status"],
            "replay_verified",
        )
        self.assertIn(
            "Verified formal witness",
            self.request("/api/report", {"bundle": bundle})[1]["html"],
        )
        bundle["results"][0]["analysis"]["result"]["assignment"]["p"] = True
        self.assertEqual(self.request("/api/replay", {"bundle": bundle})[0], 400)

    def test_snapshot_edit_review_and_stale_head_rejection(self):
        doc = approved(self.document())
        first = self.request("/api/save", {"document": doc, "expected_head": None})[1]
        changed = deepcopy(doc)
        changed["source"]["text"] += " Reader note."
        for status in inspect_document(changed)["readings"][0][
            "review_status"
        ].values():
            self.assertEqual(status, "review_required")
        changed = approved(changed)
        code, second = self.request(
            "/api/save", {"document": changed, "expected_head": first["snapshot_hash"]}
        )
        self.assertEqual(code, 200)
        self.assertEqual(second["document"]["parent_revision"], first["revision_hash"])
        self.assertEqual(
            self.request(
                "/api/save",
                {"document": changed, "expected_head": first["snapshot_hash"]},
            )[0],
            409,
        )
        loaded = self.request("/api/history/" + first["snapshot_hash"])[1]["document"]
        self.assertEqual(loaded, doc)
        restarted = App(Path(self.temp.name) / "data")
        self.assertEqual(restarted.heads()[0]["snapshot_hash"], second["snapshot_hash"])

    def test_import_starts_a_new_draft_without_copied_approval(self):
        doc = approved(self.document())
        doc["parent_revision"] = "0" * 64
        status, result = self.request("/api/import", {"record": doc})
        self.assertEqual(status, 200)
        self.assertEqual(result["document"]["reviews"], [])
        self.assertIsNone(result["document"]["parent_revision"])
        self.assertEqual(self.request("/api/import", {"record": []})[0], 400)

    def test_origin_host_size_json_and_endpoint_guards(self):
        self.assertEqual(
            self.request(
                "/api/inspect",
                {"document": self.document()},
                headers={"Origin": "https://unrelated.example"},
            )[0],
            403,
        )
        self.assertEqual(
            self.request("/api/health", headers={"Host": "unrelated.example"})[0], 403
        )
        self.assertEqual(
            self.request("/api/import", raw='{"record":1,"record":2}')[0], 400
        )
        self.assertEqual(self.request("/api/import", raw="[]")[0], 400)
        self.assertEqual(
            self.request(
                "/api/import", raw="{}", headers={"Content-Type": "text/plain"}
            )[0],
            415,
        )
        self.assertEqual(
            self.request("/api/import", raw="{}", headers={"Content-Length": "110000"})[
                0
            ],
            413,
        )
        self.assertEqual(self.request("/not-a-file")[0], 404)
        self.assertEqual(
            self.request(
                "/api/analyze", {"document": self.document(), "backend": "arbitrary"}
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                "/api/analyze",
                {
                    "document": self.document(),
                    "backend": "dpll",
                    "model_path": "untrusted.py",
                },
            )[0],
            400,
        )

    def test_worker_deadline_and_concurrency_guards(self):
        app = App(Path(self.temp.name) / "guard-data")
        with patch(
            "workbench.legacy.prototype.subprocess.run",
            side_effect=subprocess.TimeoutExpired("worker", 5),
        ):
            with self.assertRaises(APIError) as error:
                app.calculate(
                    "analyze", {"document": self.document(), "backend": "dpll"}
                )
            self.assertEqual(error.exception.status, 408)
        self.assertTrue(app.slots.acquire(False))
        self.assertTrue(app.slots.acquire(False))
        with self.assertRaises(APIError) as error:
            app.calculate("analyze", {})
        self.assertEqual(error.exception.status, 503)
        app.slots.release()
        app.slots.release()

    def test_semantic_gates_survive_dpll_and_single_case_versions(self):
        doc = approved(example_documents()[2]["document"])
        bundle = r.execute(doc, backend="dpll")
        self.assertEqual(
            bundle["results"][1]["analysis"]["result"]["outcome"],
            "unsupported_semantics",
        )
        self.assertEqual(r.replay_reconstruction(bundle)["status"], "replay_verified")
        case = deepcopy(bundle["results"][0]["analysis"]["case"])
        for backend in ("exhaustive", "dpll"):
            single = run(case, backend=backend)
            self.assertEqual(replay(single)["status"], "replay_verified")
        bundle["execution"]["backend"] = "unknown"
        with self.assertRaises(ValueError):
            r.replay_reconstruction(bundle)

    def test_portable_archive_runs_and_replays_without_site_packages(self):
        import importlib.util
        import hashlib

        tools = Path(__file__).resolve().parents[1] / "tools/build_prototype.py"
        spec = importlib.util.spec_from_file_location("prototype_builder", tools)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        destination = Path(self.temp.name) / "portable"
        built = builder.build(destination)
        archive = destination / "ArgScopePrototype.pyz"
        readme = (destination / "README.md").read_text()
        self.assertIn("ArgScopePrototype.pyz --data-dir ./data", readme)
        self.assertIn("no third-party Python packages", readme)
        doc = approved(self.document())
        payload = {"document": doc, "backend": "dpll", "source_db": None}
        import sys

        process = subprocess.run(
            [sys.executable, "-I", "-S", str(archive), "--worker", "analyze"],
            input=json.dumps(payload).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        bundle = json.loads(process.stdout)
        self.assertEqual(
            bundle["results"][0]["analysis"]["result"]["outcome"], "witness_found"
        )
        bundle_path = destination / "result.json"
        bundle_path.write_text(json.dumps(bundle))
        replayed = subprocess.run(
            [sys.executable, "-I", "-S", str(archive), "replay", str(bundle_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(replayed.returncode, 0, replayed.stderr.decode())
        self.assertEqual(json.loads(replayed.stdout)["status"], "replay_verified")
        self.assertEqual(
            built["archive_sha256"], hashlib.sha256(archive.read_bytes()).hexdigest()
        )
