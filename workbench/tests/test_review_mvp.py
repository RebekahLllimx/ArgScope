"""Prose contracts, actual LangGraph checkpoints, RESP child tools and HTTP review."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from workbench.contracts import digest, canonical
from workbench.review.schemas import Request, validate_draft
from workbench.review.providers import DemoProvider, OllamaProvider, examples
from workbench.review.shared import Shared, decode
from workbench.review.store import Store
from workbench.review.graph import Workflow
from workbench.review.prompt_policy import PROMPT_POLICY_ID, instructions
from workbench.review import render
from workbench.review_app import create_app
from falsifylab.result_store import StoreError


def request(index=0):
    return Request.model_validate(examples()[index]["request"]).model_dump(mode="json")


def review(ref, **changes):
    return {
        "expected_draft": ref,
        "reviewer": "Authored control reviewer",
        "rationale": "Self-review of an authored demonstration; no expert certification.",
        "fidelity": "accepted",
        "attribution": "accepted",
        "interpretation": "accepted",
        "approve_formal_mapping": True,
        "edited_draft": None,
        **changes,
    }


class ProseTests(unittest.TestCase):
    def draft(self, index=0):
        return DemoProvider().generate("draft", request(index))

    def test_request_caps_types_and_reference_url(self):
        for value in (
            {**request(), "extra": 1},
            {**request(), "question": True},
            {**request(), "sources": []},
            {
                **request(),
                "sources": [
                    {**request()["sources"][0], "reference_url": "javascript:alert(1)"}
                ],
            },
            {
                **request(),
                "sources": [{**request()["sources"][0], "text": "word " * 1501}],
            },
        ):
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError):
                Request.model_validate(value)

    def test_prose_does_not_require_a_formula_and_demo_is_explicit(self):
        draft, issues = validate_draft(self.draft(2), request(2))
        self.assertIsNone(draft["readings"][0]["formal_mapping"])
        self.assertEqual(draft["provider"], "demo-rules-v1")
        self.assertIn("unsupported_semantics", [i["kind"] for i in issues])

    def test_exact_unicode_quotes_and_wrong_spans(self):
        value = request()
        value["sources"][0][
            "text"
        ] = "😀 A premise is reported. A conclusion is suggested."
        draft = DemoProvider().generate("draft", value)
        validate_draft(draft, value)
        draft["readings"][0]["nodes"][0]["source_refs"][0]["start"] += 1
        with self.assertRaisesRegex(ValueError, "quotation"):
            validate_draft(draft, value)

    def test_cycle_missing_dependency_duplicate_and_bad_formula(self):
        for mutation in (
            lambda d: d["readings"][0]["nodes"][-1].update(depends_on=["C"]),
            lambda d: d["readings"][0]["nodes"][-1].update(depends_on=["missing"]),
            lambda d: d["readings"][0]["nodes"][1].update(id="P1"),
            lambda d: d["readings"][0]["formal_mapping"]["formulas"].update(
                P1="p && q"
            ),
        ):
            draft = self.draft()
            mutation(draft)
            with self.assertRaises(ValueError):
                validate_draft(draft, request())

    def test_attribution_issue_and_substantive_demo_revision(self):
        provider = DemoProvider()
        draft, issues = validate_draft(self.draft(), request())
        self.assertEqual(issues[0]["kind"], "attribution_without_reference")
        fixed = provider.generate("revise", request(), draft, issues)
        _, remaining = validate_draft(fixed, request())
        self.assertFalse(remaining)
        assumption = next(n for n in fixed["readings"][0]["nodes"] if n["id"] == "A1")
        self.assertEqual(assumption["commitment"], "proposed_assumption")

    def test_archive_integrity_and_unrelated_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "review.sqlite3"
            store = Store(path)
            key = store.archive("draft", {"value": 1})
            self.assertEqual(store.artifact(key)["payload"], {"value": 1})
            with store.db() as db:
                db.execute("UPDATE review_artifacts SET body='{}' WHERE hash=?", (key,))
            with self.assertRaisesRegex(ValueError, "integrity"):
                store.artifact(key)
            other = Path(folder) / "other.sqlite3"
            with sqlite3.connect(other) as db:
                db.execute("CREATE TABLE private_records(id TEXT)")
            with self.assertRaisesRegex(ValueError, "separate"):
                Store(other)

    def test_provider_budget_survives_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "review.sqlite3")
            run_id = store.create(request())
            for i in range(6):
                self.assertEqual(
                    store.reserve(run_id, "model_call", 6, {"live_model": False}), i + 1
                )
            with self.assertRaisesRegex(ValueError, "budget exhausted"):
                store.reserve(run_id, "model_call", 6, {})

    def test_prompt_policy_is_general_and_content_versioned(self):
        policy = instructions("dictionary")
        self.assertTrue(PROMPT_POLICY_ID.startswith("review-prompts-v2:"))
        for case_name in ("McX", "Quine", "Chalmers"):
            self.assertNotIn(case_name, policy)
        self.assertIn("reported opponent", policy)
        self.assertIn("proposed_assumption", policy)

    def test_latest_event_is_not_hidden_by_public_event_page(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "review.sqlite3")
            run_id = store.create(request())
            for index in range(201):
                store.event(run_id, "trace", {"index": index}, identity=str(index))
            store.event(
                run_id,
                "execution_failed",
                {"category": "failed", "error": "late failure"},
                identity="late-failure",
            )
            self.assertEqual(len(store.events(run_id)), 200)
            self.assertEqual(
                store.latest_event(run_id, ("execution_failed",))["error"],
                "late failure",
            )

    def test_shared_record_guards_and_optional_ollama_mock(self):
        body = {
            "schema": "review-artifact-v1",
            "kind": "draft",
            "inputs": [],
            "payload": {"ok": True},
        }
        self.assertEqual(decode(canonical(body).encode(), digest(body)), body)
        with self.assertRaisesRegex(ValueError, "identity"):
            decode(canonical(body).encode(), "a" * 64)
        with self.assertRaises(ValueError):
            decode(b'{"schema":1,"schema":2}')
        with self.assertRaises(ValueError):
            OllamaProvider("model", "https://example.com")
        output = self.draft(1)
        fake = io.BytesIO(
            json.dumps({"message": {"content": json.dumps(output)}}).encode()
        )
        with patch("urllib.request.urlopen", return_value=fake) as call:
            actual = OllamaProvider("fixture-model").generate("draft", request(1))
        payload = json.loads(call.call_args[0][0].data)
        self.assertIsInstance(payload["format"], dict)
        self.assertFalse(payload["stream"])
        self.assertEqual(actual["provider"], "ollama:fixture-model:review-v2")


class RealReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("REVIEW_TEST_KV_ROOT")
        if not root:
            raise unittest.SkipTest(
                "Set REVIEW_TEST_KV_ROOT to exercise the actual custom KV"
            )
        sys.path.insert(
            0, str(Path(__file__).resolve().parents[2] / "falsifylab/tools")
        )
        from evaluate_kv import SessionServer, snapshot_source

        cls.snapshot_source = staticmethod(snapshot_source)
        cls.kv_root = Path(root)
        cls.before = snapshot_source(cls.kv_root)
        cls.session_temp = tempfile.TemporaryDirectory()
        cls.session = SessionServer(
            cls.kv_root, Path(cls.session_temp.name) / "test.aof"
        )
        cls.session.start()

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()
        assert cls.snapshot_source(cls.kv_root) == cls.before
        cls.session_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = Shared(self.session.port, "test-" + os.urandom(5).hex())

    def flow(self, provider=None):
        store = Store(self.root / "reviews.sqlite3")
        return Workflow(
            store, self.shared, provider or DemoProvider(), self.root / "graph.sqlite3"
        )

    def initial(self, flow, index=0):
        run_id = flow.store.create(request(index))
        flow.run(run_id)
        state = flow.snapshot(run_id)
        self.assertTrue(
            state.interrupts, json.dumps(flow.store.events(run_id), indent=2)
        )
        return run_id, state.values

    def test_single_draft_pauses_and_scoped_review_survives_reopen(self):
        flow = self.flow()
        value = request(1)
        value["workflow_mode"] = "single_draft"
        run_id = flow.store.create(value)
        flow.run(run_id)
        state = flow.snapshot(run_id).values
        self.assertTrue(flow.snapshot(run_id).interrupts)
        self.assertEqual(state["revision"], 0)
        self.assertEqual(len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 1)
        original_ref = state["draft_ref"]
        flow.close()
        flow = self.flow()
        self.addCleanup(flow.close)
        self.assertEqual(flow.snapshot(run_id).values["draft_ref"], original_ref)
        draft = flow.payload(original_ref)
        draft["readings"][0]["formal_scope"] = {"mode": "declared_formulas", "rationale": "Only these formulas; interpretation remains disputed in this simulated test."}
        draft["readings"][0]["qualifications"] = [{"kind": "meaning", "description": "Unencoded interpretation remains disputed.", "representation": "unencoded", "rationale": "Preserved in the output."}]
        flow.run(run_id, review(original_ref, fidelity="disputed", interpretation="disputed", approve_formula_scope=True, edited_draft=draft))
        done = flow.snapshot(run_id)
        self.assertFalse(done.next)
        report = flow.payload(done.values["report_ref"])
        self.assertEqual(report["review_status"], "disputed")
        self.assertEqual(report["formal_checks"][0]["outcome"], "no_counterexample_complete")
        self.assertEqual(len(done.values["versions"]), 2)
        self.assertEqual(len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 1)
        self.assertEqual(render.verify(render.export(report, flow.store.events(run_id)))["status"], "snapshot_verified")

    def test_real_graph_revision_child_kv_approval_and_export(self):
        flow = self.flow()
        self.addCleanup(flow.close)
        run_id, state = self.initial(flow)
        self.assertEqual(state["revision"], 1)
        self.assertFalse(flow.store.human_records(run_id))
        events = flow.store.events(run_id)
        workers = [
            e for e in events if e["node"] == "kv_tool_result" and not e["cache_hit"]
        ]
        self.assertTrue(workers)
        self.assertTrue(all(e["worker_pid"] != os.getpid() for e in workers))
        self.assertTrue(all(e["tool_total_ms"] >= 0 for e in workers))
        self.assertTrue(all(e["input_json_bytes"] > 0 and e["output_json_bytes"] > 0 for e in workers))
        self.assertEqual(len([e for e in events if e["node"] == "model_call"]), 4)
        flow.run(run_id, review(state["draft_ref"]))
        done = flow.snapshot(run_id)
        self.assertFalse(done.next, json.dumps(flow.store.events(run_id), indent=2))
        report = flow.payload(done.values["report_ref"])
        self.assertEqual(
            report["formal_checks"][0]["assignment"], {"p": False, "q": True}
        )
        self.assertFalse(report["provider"]["live_model"])
        bundle = render.export(report, flow.store.events(run_id) + [{"active_ms": 0.0}])
        self.assertIs(type(bundle["trace"][-1]["active_ms"]), int)
        self.assertEqual(
            render.verify(json.loads(json.dumps(bundle)))["status"], "snapshot_verified"
        )
        self.assertIn("DEMO", render.html(bundle))
        broken = deepcopy(bundle)
        broken["report"]["formal_checks"][0]["assignment"]["p"] = True
        broken["export_hash"] = digest(
            {k: v for k, v in broken.items() if k != "export_hash"}
        )
        with self.assertRaisesRegex(ValueError, "recomputation"):
            render.verify(broken)

    def test_checkpoint_restart_expiry_and_no_repeated_demo_calls(self):
        self.shared.ttl = 1
        flow = self.flow()
        run_id, state = self.initial(flow, 1)
        calls = len([e for e in flow.store.events(run_id) if e["node"] == "model_call"])
        flow.close()
        time.sleep(1.1)
        with self.assertRaises(StoreError):
            self.shared.fetch(state["draft_ref"])
        resumed = self.flow()
        self.addCleanup(resumed.close)
        self.assertTrue(resumed.snapshot(run_id).interrupts)
        resumed.run(run_id, review(state["draft_ref"]))
        report = resumed.payload(resumed.snapshot(run_id).values["report_ref"])
        self.assertEqual(
            report["formal_checks"][0]["outcome"], "no_counterexample_complete"
        )
        self.assertEqual(
            len([e for e in resumed.store.events(run_id) if e["node"] == "model_call"]),
            calls,
        )

    def test_semantics_and_human_dispute_cannot_be_overridden(self):
        flow = self.flow()
        self.addCleanup(flow.close)
        for index, changes, expected in (
            (2, {}, "unsupported_semantics"),
            (1, {"attribution": "disputed"}, "review_required"),
            (1, {"approve_formal_mapping": False}, "review_required"),
        ):
            run_id, state = self.initial(flow, index)
            flow.run(run_id, review(state["draft_ref"], **changes))
            report = flow.payload(flow.snapshot(run_id).values["report_ref"])
            self.assertEqual(report["formal_checks"][0]["outcome"], expected)

    def test_warm_tool_reuse_and_corrupt_payload_rejected(self):
        flow = self.flow()
        self.addCleanup(flow.close)
        first, _ = self.initial(flow, 1)
        second, state = self.initial(flow, 1)
        hits = [e for e in flow.store.events(second) if e["node"] == "kv_tool_result"]
        self.assertTrue(any(e["cache_hit"] for e in hits))
        self.shared.client.command(
            b"SET", self.shared.key("artifact", state["draft_ref"]), b'{"bad":true}'
        )
        with self.assertRaises(ValueError):
            self.shared.fetch(state["draft_ref"])

    def test_two_revision_cap_and_provider_identity_on_restart(self):
        class NoProgressDemo(DemoProvider):
            def generate(
                self, operation, request, draft=None, issues=None, evidence=None
            ):
                if operation == "revise":
                    return deepcopy(draft)
                return super().generate(operation, request, draft, issues, evidence)

        flow = self.flow(NoProgressDemo())
        run_id, state = self.initial(flow)
        self.assertEqual(state["revision"], 2)
        self.assertTrue(state["issues"])
        self.assertEqual(
            len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 6
        )
        flow.close()
        other = DemoProvider()
        other.name = "different-provider"
        resumed = self.flow(other)
        self.addCleanup(resumed.close)
        resumed.run(run_id, review(state["draft_ref"]))
        self.assertIn("original provider", resumed.store.events(run_id)[-1]["error"])
        self.assertFalse(resumed.store.human_records(run_id))

    def test_failed_tool_retry_reuses_durably_completed_draft(self):
        flow = self.flow()
        self.addCleanup(flow.close)
        run_id = flow.store.create(request(1))
        with patch.object(
            flow, "tool", side_effect=StoreError("KV temporarily offline")
        ):
            flow.run(run_id)
        self.assertEqual(flow.store.events(run_id)[-1]["node"], "execution_failed")
        self.assertEqual(
            len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 1
        )
        flow.run(run_id)
        self.assertTrue(flow.snapshot(run_id).interrupts)
        self.assertEqual(
            len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 2
        )

    def test_provider_receipt_precedes_kv_publication(self):
        flow = self.flow()
        self.addCleanup(flow.close)
        run_id = flow.store.create(request(1))
        original = self.shared.ensure

        def publication_outage(store, key):
            if store.artifact(key)["kind"] == "argument-draft":
                raise StoreError("Publication interrupted after durable receipt")
            return original(store, key)

        with patch.object(self.shared, "ensure", side_effect=publication_outage):
            flow.run(run_id)
        self.assertEqual(
            len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 1
        )
        flow.run(run_id)
        self.assertTrue(flow.snapshot(run_id).interrupts)
        self.assertEqual(
            len([e for e in flow.store.events(run_id) if e["node"] == "model_call"]), 2
        )
        self.assertTrue(
            any(
                e["node"] == "operation_reused" and e["operation"] == "draft"
                for e in flow.store.events(run_id)
            )
        )

    def wait(self, client, run_id, status="awaiting_review"):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = client.get("/api/argument-runs/" + run_id).json()
            if value["status"] == status:
                return value
            if value["status"] in ("failed", "budget_exhausted", "cancelled"):
                self.fail(json.dumps(value, indent=2))
            time.sleep(0.02)
        self.fail("Review did not settle")

    def app(self):
        return create_app(
            self.root / "app",
            self.session.port,
            self.shared.namespace,
            run_source_worker=False,
        )

    def test_http_guards_stale_review_and_packet_verification(self):
        with TestClient(self.app()) as client:
            self.assertFalse(
                client.get("/api/argument-health").json()["provider"]["live_model"]
            )
            self.assertEqual(
                client.post(
                    "/api/argument-runs",
                    content='{"question":1,"question":2}',
                    headers={"Content-Type": "application/json"},
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/api/argument-runs", json={**request(), "model_url": "http://bad"}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/argument-runs",
                    json=request(),
                    headers={"Origin": "https://unrelated.example"},
                ).status_code,
                403,
            )
            self.assertEqual(
                client.get(
                    "/api/argument-health", headers={"Host": "unrelated.example"}
                ).status_code,
                400,
            )
            created = client.post("/api/argument-runs", json=request()).json()
            run_id = created["id"]
            current = self.wait(client, run_id)
            self.assertEqual(
                client.post(
                    f"/api/argument-runs/{run_id}/resume", json=review("a" * 64)
                ).status_code,
                409,
            )
            self.assertFalse(client.app.state.review.store.human_records(run_id))
            edited = deepcopy(current["draft"])
            edited["readings"][0]["nodes"][0]["speaker"] = "Explicit human correction"
            response = client.post(
                f"/api/argument-runs/{run_id}/resume",
                json=review(current["draft_ref"], edited_draft=edited),
            )
            self.assertEqual(response.status_code, 202, response.text)
            done = self.wait(client, run_id, "succeeded")
            self.assertNotEqual(done["draft_ref"], current["draft_ref"])
            bundle = client.get(f"/api/argument-runs/{run_id}/report.json").json()
            self.assertEqual(
                client.post("/api/argument-verify", json=bundle).json()["status"],
                "snapshot_verified",
            )
            self.assertIn(
                "DEMO", client.get(f"/api/argument-runs/{run_id}/report.html").text
            )

    def test_http_restart_library_binding_and_unavailable_kv(self):
        app = self.app()
        with TestClient(app) as client:
            body = {
                "source_key": "authored-note",
                "title": "Authored note",
                "text": request(1)["sources"][0]["text"],
            }
            self.assertEqual(client.post("/api/documents", json=body).status_code, 201)
            passage = client.get("/api/argument-sources").json()[0]
            selected = request(1)
            selected["sources"][0].update(
                text=passage["text"], binding=passage["binding"]
            )
            run_id = client.post("/api/argument-runs", json=selected).json()["id"]
            current = self.wait(client, run_id)
            selected["sources"][0]["text"] += " changed"
            self.assertEqual(
                client.post("/api/argument-runs", json=selected).status_code, 400
            )
        with TestClient(self.app()) as client:
            restored = client.get("/api/argument-runs/" + run_id).json()
            self.assertEqual(restored["status"], "awaiting_review")
            self.assertEqual(restored["draft_ref"], current["draft_ref"])
            with patch.object(
                client.app.state.review.flow.shared.client,
                "command",
                side_effect=StoreError("offline"),
            ):
                self.assertEqual(
                    client.post("/api/argument-runs", json=request()).status_code, 503
                )

    def test_cancelled_human_pause_cannot_be_approved(self):
        with TestClient(self.app()) as client:
            run_id = client.post("/api/argument-runs", json=request(1)).json()["id"]
            paused = self.wait(client, run_id)
            self.assertEqual(
                client.post(f"/api/argument-runs/{run_id}/cancel", json={}).status_code,
                202,
            )
            self.assertEqual(
                client.get(f"/api/argument-runs/{run_id}").json()["status"], "cancelled"
            )
            self.assertEqual(
                client.post(
                    f"/api/argument-runs/{run_id}/resume",
                    json=review(paused["draft_ref"]),
                ).status_code,
                409,
            )
            self.assertFalse(client.app.state.review.store.human_records(run_id))


if __name__ == "__main__":
    unittest.main()
