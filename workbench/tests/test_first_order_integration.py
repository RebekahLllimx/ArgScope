"""Actual KV child, proof/model replay and human edit versioning for FOL."""
from copy import deepcopy
import unittest
import test_review_mvp as base
from test_review_mvp import review
from test_first_order import fixture
from workbench.review import render


class FixtureProvider:
    name = "authored-fol-fixture-v1"
    live = False

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        if operation == "critique":
            return {
                "summary": "Authored fixture routing, not philosophical evaluation.",
                "needs_revision": False,
                "issues": issues or [],
            }
        return fixture()[1]


class FirstOrderIntegration(unittest.TestCase):
    setUpClass = classmethod(base.RealReviewTests.setUpClass.__func__)
    tearDownClass = classmethod(base.RealReviewTests.tearDownClass.__func__)
    setUp = base.RealReviewTests.setUp
    flow = base.RealReviewTests.flow

    def test_fol_child_preview_review_export_and_restart(self):
        flow = self.flow(FixtureProvider())
        run_id = flow.store.create(fixture()[0])
        flow.run(run_id)
        state = flow.snapshot(run_id)
        self.assertTrue(state.interrupts)
        audit = flow.payload(state.values["audit_ref"])["result"]
        self.assertFalse(audit["candidate_checks"][0]["human_approved"])
        self.assertEqual(
            audit["candidate_checks"][0]["outcome"], "no_counterexample_complete"
        )
        self.assertFalse(flow.store.human_records(run_id))
        self.assertTrue(
            any(
                e["node"] == "kv_tool_result" and e["worker_pid"]
                for e in flow.store.events(run_id)
            )
        )
        flow.close()
        flow = self.flow(FixtureProvider())
        self.addCleanup(flow.close)
        self.assertTrue(flow.snapshot(run_id).interrupts)
        flow.run(run_id, review(state.values["draft_ref"]))
        done = flow.snapshot(run_id)
        self.assertFalse(done.next)
        report = flow.payload(done.values["report_ref"])
        bundle = render.export(report, flow.store.events(run_id))
        self.assertEqual(render.verify(bundle)["status"], "snapshot_verified")
        self.assertIn("trusted-solver-unsat", render.html(bundle))
        broken = deepcopy(bundle)
        broken["report"]["formal_checks"][0]["evidence"]["proof_digest"] = "0" * 64
        from workbench.contracts import digest

        broken["export_hash"] = digest(
            {k: v for k, v in broken.items() if k != "export_hash"}
        )
        with self.assertRaises(ValueError):
            render.verify(broken)

    def test_fol_human_edit_gets_new_countermodel_and_no_old_preview(self):
        flow = self.flow(FixtureProvider())
        self.addCleanup(flow.close)
        run_id = flow.store.create(fixture()[0])
        flow.run(run_id)
        old = flow.snapshot(run_id).values
        edit = flow.payload(old["draft_ref"])
        edit["readings"][0]["formal_mapping"]["formulas"]["C"] = "forall x. ~W(x)"
        flow.run(run_id, review(old["draft_ref"], edited_draft=edit))
        state = flow.snapshot(run_id).values
        self.assertNotEqual(state["draft_ref"], state["audit_draft_ref"])
        report = flow.payload(state["report_ref"])
        result = report["formal_checks"][0]
        self.assertEqual(result["outcome"], "witness_found")
        self.assertEqual(
            result["validation"]["validator"], "first-order-original-ast-v1"
        )
        self.assertEqual(
            render.verify(render.export(report, []))["status"], "snapshot_verified"
        )
