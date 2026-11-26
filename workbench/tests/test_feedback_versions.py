"""Reader revision, saved comparisons and restart/failure behavior; no live model."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from workbench.contracts import digest
from workbench.review.providers import DemoProvider
from workbench.review.schemas import RevisionFeedback
from workbench.review.versions import compare
from falsifylab.result_store import StoreError
import test_review_mvp as existing
from test_review_mvp import request, review


class FeedbackProvider(DemoProvider):
    name = "feedback-test-fixture"

    def __init__(self):
        self.feedback_calls = []

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        feedback = next((i for i in issues or [] if i["kind"] == "reader_feedback"), None)
        if operation == "revise" and feedback:
            self.feedback_calls.append(deepcopy((draft, feedback)))
            value = deepcopy(draft)
            value["summary"] = "Fixture revision: " + feedback["message"]
            return value
        return super().generate(operation, request, draft, issues, evidence)


def feedback(ref, edited=None):
    return {"action": "request_revision", "expected_draft": ref, "reviewer": "Synthetic reader",
            "feedback": "Preserve the speaker attribution and explain the premise.", "edited_draft": edited}


class DifferenceTests(unittest.TestCase):
    def test_align_by_id_and_preserve_added_removed_and_order(self):
        a = {"readings": [{"id": "r", "nodes": [{"id": "P", "speaker": "\u4f5c\u8005"}, {"id": "C", "content": "old"}], "formal_mapping": None}]}
        b = deepcopy(a)
        b["readings"][0]["nodes"] = [{"id": "C", "content": "new"}, {"id": "P", "speaker": "\u5bf9\u624b"}, {"id": "A", "content": "extra"}]
        b["readings"][0]["formal_mapping"] = {"formulas": {"P": "p", "C": "q"}}
        changes = compare(a, b)
        speaker = next(c for c in changes if c["path"][-1] == "speaker")
        self.assertEqual(speaker["path"], ["readings", "r", "nodes", "P", "speaker"])
        self.assertEqual(speaker["after"], "\u5bf9\u624b")
        self.assertTrue(any(c["kind"] == "added" and c["path"][-1] == "A" for c in changes))
        self.assertTrue(any(c["kind"] == "order" for c in changes))
        self.assertTrue(any(c["kind"] == "removed" for c in compare(b, a)))
        self.assertEqual(compare(a, a), [])

    def test_feedback_rejects_approval_fields_and_blank_input(self):
        for extra in [{"feedback": "     "}, {"reviewer": " "}, {"approve_formal_mapping": True}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                RevisionFeedback.model_validate({**feedback("a" * 64), **extra})


class FeedbackIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(existing.RealReviewTests.setUpClass.__func__)
    tearDownClass = classmethod(existing.RealReviewTests.tearDownClass.__func__)
    setUp = existing.RealReviewTests.setUp
    flow = existing.RealReviewTests.flow
    initial = existing.RealReviewTests.initial
    wait = existing.RealReviewTests.wait
    app = existing.RealReviewTests.app

    def test_edit_feedback_once_fresh_pause_restart_and_final_binding(self):
        provider = FeedbackProvider()
        flow = self.flow(provider)
        run_id, state = self.initial(flow, 1)
        initial = state["draft_ref"]
        edited = flow.payload(initial)
        edited["summary"] = "Reader edit before asking for revision"
        flow.run(run_id, feedback(initial, edited))
        revised = flow.snapshot(run_id)
        self.assertTrue(revised.interrupts)
        self.assertEqual(revised.values["feedback_round"], 1)
        self.assertEqual(len(provider.feedback_calls), 1)
        self.assertEqual(provider.feedback_calls[0][0]["summary"], edited["summary"])
        self.assertFalse(flow.store.human_records(run_id))
        self.assertFalse(revised.values.get("human_ref"))
        self.assertEqual([v["origin"] for v in revised.values["versions"]], ["initial", "reader_edit_before_feedback", "feedback_revision"])
        self.assertEqual(revised.values["audit_draft_ref"], revised.values["draft_ref"])
        self.assertTrue(any(i["kind"] == "reader_feedback_review" for i in revised.values["issues"]))
        flow.close()
        resumed = self.flow(provider)
        self.addCleanup(resumed.close)
        self.assertTrue(resumed.snapshot(run_id).interrupts)
        resumed.run(run_id, review(revised.values["draft_ref"]))
        done = resumed.snapshot(run_id)
        self.assertFalse(done.next)
        self.assertEqual(len(provider.feedback_calls), 1)
        self.assertEqual(resumed.store.human_records(run_id)[0]["draft"], done.values["draft_ref"])
        report = resumed.payload(done.values["report_ref"])
        self.assertEqual(report["revision_feedback"]["feedback"], feedback(initial)["feedback"])
        self.assertEqual(len(report["saved_versions"]), 3)

    def test_feedback_receipt_survives_publication_failure(self):
        provider = FeedbackProvider()
        flow = self.flow(provider); self.addCleanup(flow.close)
        run_id, state = self.initial(flow, 1)
        publish = self.shared.ensure
        def outage(store, key):
            artifact = store.artifact(key)
            if artifact["kind"] == "argument-draft" and artifact["payload"]["summary"].startswith("Fixture revision:"):
                raise StoreError("After feedback receipt commit")
            return publish(store, key)
        with patch.object(self.shared, "ensure", side_effect=outage):
            flow.run(run_id, feedback(state["draft_ref"]))
        self.assertEqual(len(provider.feedback_calls), 1)
        flow.run(run_id)
        self.assertTrue(flow.snapshot(run_id).interrupts)
        self.assertEqual(len(provider.feedback_calls), 1)
        self.assertEqual(len([e for e in flow.store.events(run_id) if e["node"] == "feedback_model_call"]), 1)

    def test_extra_call_available_after_two_automatic_revisions(self):
        class Stubborn(FeedbackProvider):
            def generate(self, operation, request, draft=None, issues=None, evidence=None):
                if operation == "revise" and not any(i["kind"] == "reader_feedback" for i in issues or []):
                    return deepcopy(draft)
                return super().generate(operation, request, draft, issues, evidence)
        provider = Stubborn(); flow = self.flow(provider); self.addCleanup(flow.close)
        run_id, state = self.initial(flow)
        self.assertEqual(state["revision"], 2)
        flow.run(run_id, feedback(state["draft_ref"]))
        self.assertTrue(flow.snapshot(run_id).interrupts)
        self.assertEqual(len(provider.feedback_calls), 1)
        events = flow.store.events(run_id)
        self.assertEqual(sum(e["node"] == "model_call" for e in events), 6)
        self.assertEqual(sum(e["node"] == "feedback_model_call" for e in events), 1)

    def test_http_stale_repeat_cross_run_and_saved_comparison(self):
        with TestClient(self.app()) as client:
            run_id = client.post("/api/argument-runs", json=request(1)).json()["id"]
            state = self.wait(client, run_id)
            url = f"/api/argument-runs/{run_id}"
            self.assertEqual(client.post(url + "/feedback", json=feedback("a" * 64)).status_code, 409)
            self.assertEqual(client.post(url + "/feedback", json={**feedback(state["draft_ref"]), "feedback": "   "}).status_code, 422)
            edited = deepcopy(state["draft"]); edited["summary"] = "Manual input preserved in history"
            response = client.post(url + "/feedback", json=feedback(state["draft_ref"], edited))
            self.assertEqual(response.status_code, 202)
            updated = self.wait(client, run_id)
            self.assertEqual(updated["feedback_round"], 1)
            self.assertFalse(updated["feedback_available"])
            self.assertEqual(client.post(url + "/feedback", json=feedback(updated["draft_ref"])).status_code, 409)
            self.assertEqual(client.post(url + "/resume", json=review(state["draft_ref"])).status_code, 409)
            result = client.get(url + "/compare", params={"before": state["draft_ref"], "after": updated["draft_ref"]})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(any(c["path"] == ["summary"] for c in result.json()["changes"]))
            other = client.post("/api/argument-runs", json=request(0)).json()["id"]
            other_state = self.wait(client, other)
            self.assertEqual(client.get(url + "/compare", params={"before": state["draft_ref"], "after": other_state["draft_ref"]}).status_code, 404)
            self.assertFalse(updated["report"])

    def test_legacy_run_remains_reviewable_without_new_feedback(self):
        flow = self.flow(); self.addCleanup(flow.close)
        run_id, state = self.initial(flow, 1)
        flow.graph.update_state(flow.config(run_id), {"workflow_version": "review-workflow-v1", "versions": []}, as_node="critique")
        flow.run(run_id)
        self.assertTrue(flow.snapshot(run_id).interrupts)
        flow.run(run_id, review(state["draft_ref"]))
        self.assertFalse(flow.snapshot(run_id).next)

    def test_model_failure_is_not_silently_retried(self):
        class Failing(FeedbackProvider):
            def generate(self, operation, request, draft=None, issues=None, evidence=None):
                if any(i["kind"] == "reader_feedback" for i in issues or []):
                    self.feedback_calls.append(1)
                    raise ValueError("Authored provider failure")
                return super().generate(operation, request, draft, issues, evidence)
        provider = Failing(); flow = self.flow(provider); self.addCleanup(flow.close)
        run_id, state = self.initial(flow, 1)
        flow.run(run_id, feedback(state["draft_ref"]))
        flow.run(run_id)
        self.assertEqual(len(provider.feedback_calls), 1)
        self.assertFalse(flow.store.human_records(run_id))
        self.assertIn("budget exhausted", flow.store.events(run_id)[-1]["error"])
