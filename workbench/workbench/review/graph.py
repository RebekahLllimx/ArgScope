"""One bounded LangGraph workflow; model judgments never become human approvals."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith import tracing_context

from workbench.contracts import digest, canonical
from .providers import Critique, StructuredProvider
from .prompt_policy import PROMPT_POLICY_ID
from .schemas import Request, HumanReview, RevisionFeedback, validate_draft, issue
from .tool_worker import TOOL_VERSION


class State(TypedDict, total=False):
    provider_name: str
    live_model: bool
    workflow_version: str
    run_id: str
    request_ref: str
    evidence_ref: str
    draft_ref: str
    audit_ref: str
    audit_draft_ref: str
    critique_ref: str
    human_ref: str
    formal_ref: str
    report_ref: str
    issues: list
    issue_log: list
    revision: int
    needs_revision: bool
    feedback_ref: str
    feedback_round: int
    human_action: str
    versions: list


class Workflow:
    def __init__(self, store, shared, provider: StructuredProvider, checkpoint_path):
        self.store, self.shared, self.provider = store, shared, provider
        self.connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        graph = StateGraph(State)
        for name in (
            "evidence",
            "draft",
            "audit",
            "critique",
            "revise",
            "formal",
            "report",
            "feedback_revise",
            "feedback_audit",
        ):
            graph.add_node(name, self.node(name))
        graph.add_node("human", self.human)
        graph.add_edge(START, "evidence")
        graph.add_edge("evidence", "draft")
        graph.add_edge("draft", "audit")
        graph.add_conditional_edges(
            "audit",
            lambda state: "human" if self.payload(state["request_ref"]).get("workflow_mode") == "single_draft" else "critique",
            {"human": "human", "critique": "critique"},
        )
        graph.add_conditional_edges(
            "critique",
            lambda s: "revise"
            if s["needs_revision"] and s["revision"] < 2
            else "human",
            {"revise": "revise", "human": "human"},
        )
        graph.add_edge("revise", "audit")
        graph.add_conditional_edges("human", lambda s: "feedback_revise" if s.get("human_action") == "request_revision" else "formal")
        graph.add_edge("feedback_revise", "feedback_audit")
        graph.add_edge("feedback_audit", "human")
        graph.add_edge("formal", "report")
        graph.add_edge("report", END)
        self.graph = graph.compile(
            checkpointer=self.checkpointer, name="argscope-review-v1"
        )
        self.deadline = None

    def config(self, run_id):
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 40}

    def snapshot(self, run_id):
        return self.graph.get_state(self.config(run_id))

    def guard(self, run_id):
        if self.store.has_event(run_id, "cancel_requested"):
            raise ValueError("Review was cancelled")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ValueError("Active run budget exhausted")

    def node(self, name):
        def execute(state):
            self.guard(state["run_id"])
            start = time.monotonic()
            try:
                update = getattr(self, name)(state)
            except Exception as error:
                self.store.event(
                    state["run_id"],
                    "node_failed",
                    {
                        "stage": name,
                        "error": str(error),
                        "active_ms": round((time.monotonic() - start) * 1000, 3),
                    },
                )
                raise
            self.store.event(
                state["run_id"],
                "node_completed",
                {
                    "stage": name,
                    "revision": update.get("revision", state.get("revision", 0)),
                    "active_ms": round((time.monotonic() - start) * 1000, 3),
                    "outputs": {k: v for k, v in update.items() if k.endswith("_ref")},
                },
            )
            return update

        return execute

    def run(self, run_id, human=None):
        state = self.snapshot(run_id)
        spent = sum(
            e.get("active_ms", 0) for e in self.store.events(run_id, limit=None)
        ) / 1000
        extension = state.values.get("feedback_round", 0) or (human and human.get("action") == "request_revision")
        self.deadline = time.monotonic() + max(0, (210 if extension else 120) - spent)
        self.store.event(
            run_id,
            "execution_started",
            {"resume": human is not None},
            identity=os.urandom(16).hex(),
        )
        try:
            if state.values and (
                state.values.get("provider_name") != self.provider.name
                or state.values.get("live_model") != self.provider.live
                or state.values.get("workflow_version") not in ("review-workflow-v1", "review-workflow-v2")
            ):
                raise ValueError(
                    "Resume this run with its original provider and workflow version"
                )
            if human is not None:
                value = Command(resume=human)
            elif state.values:
                value = None
            else:
                request = self.store.request(run_id)
                ref = self.artifact("review-request", request)
                value = {
                    "provider_name": self.provider.name,
                    "live_model": self.provider.live,
                    "workflow_version": "review-workflow-v2",
                    "run_id": run_id,
                    "request_ref": ref,
                    "revision": 0,
                    "issue_log": [],
                    "feedback_round": 0,
                    "versions": [],
                }
            # No tracing export is enabled by this application, even if global env opts in.
            with tracing_context(enabled=False):
                self.graph.invoke(value, self.config(run_id), durability="sync")
            after = self.snapshot(run_id)
            self.store.event(
                run_id,
                "execution_finished",
                {"awaiting_human": bool(after.interrupts), "complete": not after.next},
                identity=os.urandom(16).hex(),
            )
        except Exception as error:
            self.store.event(
                run_id,
                "execution_failed",
                {
                    "error": str(error),
                    "category": "budget_exhausted"
                    if "budget exhausted" in str(error)
                    else "cancelled"
                    if "cancelled" in str(error)
                    else "failed",
                },
                identity=os.urandom(16).hex(),
            )
        finally:
            self.deadline = None

    def artifact(self, kind, payload, inputs=()):
        key = self.store.archive(kind, payload, inputs)
        self.shared.ensure(self.store, key)
        return key

    def payload(self, key):
        return self.store.artifact(key)["payload"]

    def model(self, state, operation, draft=None, issues=None, feedback=False):
        request = self.payload(state["request_ref"])
        input_hash = digest(
            {
                "provider": self.provider.name,
                "operation": operation,
                "request": request,
                "draft": draft,
                "issues": issues,
                "prompt": PROMPT_POLICY_ID,
                "revision": state.get("revision", 0),
                "evidence_ref": state["evidence_ref"],
                **({"feedback_ref": state["feedback_ref"]} if feedback else {}),
            }
        )
        receipt = self.store.receipt(state["run_id"], operation, input_hash)
        if receipt:
            self.shared.ensure(self.store, receipt)
            self.store.event(
                state["run_id"],
                "operation_reused",
                {"operation": operation, "artifact": receipt},
            )
            return receipt
        self.guard(state["run_id"])
        self.store.reserve(
            state["run_id"],
            "feedback_model_call" if feedback else "model_call",
            1 if feedback else 6,
            {
                "operation": operation,
                "provider": self.provider.name,
                "live_model": self.provider.live,
            },
        )
        usage_start = len(getattr(self.provider, "usage", []))
        try:
            result = self.provider.generate(
                operation,
                request,
                draft,
                issues,
                evidence=self.payload(state["evidence_ref"]),
            )
        finally:
            for usage in getattr(self.provider, "usage", [])[usage_start:]:
                self.store.event(state["run_id"], "provider_usage", usage)
        if operation == "critique":
            result = Critique.model_validate(result).model_dump(mode="json")
            for value in result["issues"]:
                required = {
                    "id",
                    "kind",
                    "reading_id",
                    "node_id",
                    "message",
                    "action",
                    "status",
                }
                if (
                    not isinstance(value, dict)
                    or set(value) != required
                    or value["action"] not in ("revise", "human")
                    or value["status"] != "open"
                ):
                    raise ValueError("Malformed critique issue")
        else:
            result, _ = validate_draft(result, request)
        key = self.store.archive(
            "critique" if operation == "critique" else "argument-draft",
            result,
            [state["request_ref"]]
            + ([state["draft_ref"]] if "draft_ref" in state else []),
        )
        key = self.store.record(state["run_id"], operation, input_hash, key)
        self.shared.ensure(self.store, key)
        return key

    def tool(self, state, operation, human=None):
        started = time.monotonic()
        request = {
            "operation": operation,
            "request": self.payload(state["request_ref"]),
            "draft": self.payload(state["draft_ref"]),
            "human_review": human,
            "tool_version": TOOL_VERSION,
        }
        key = self.artifact(
            "tool-request", request, [state["request_ref"], state["draft_ref"]]
        )
        self.store.reserve(
            state["run_id"], "tool_call", 12, {"operation": operation, "input": key}
        )
        output = self.shared.memo(key)
        cached = output is not None
        worker = None
        if output is None:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "workbench.review.tool_worker",
                    "--kv-port",
                    str(self.shared.client.port),
                    "--namespace",
                    self.shared.namespace,
                    "--ttl",
                    str(self.shared.ttl),
                    "--input",
                    key,
                ],
                capture_output=True,
                timeout=5,
            )
            if len(completed.stdout) > 2000:
                raise ValueError("Unexpected worker response size")
            try:
                response = json.loads(completed.stdout)
            except ValueError:
                raise ValueError(
                    "Tool process stopped without a complete result"
                ) from None
            if completed.returncode:
                raise ValueError(response.get("error", "Tool process failed"))
            output = response["output_key"]
            worker = response["worker_pid"]
            if worker == os.getpid():
                raise ValueError(
                    "The deterministic tool must run in a separate process"
                )
        body = self.shared.fetch(output)
        if (
            body["kind"] != "tool-result"
            or body["inputs"] != [key]
            or body["payload"].get("tool_version") != TOOL_VERSION
            or body["payload"].get("result", {}).get("operation") != operation
        ):
            raise ValueError("Cached tool result belongs to another request or version")
        archived = self.store.archive(body["kind"], body["payload"], body["inputs"])
        if archived != output:
            raise ValueError("Tool artifact archive mismatch")
        self.shared.remember(key, output)
        self.store.event(
            state["run_id"],
            "kv_tool_result",
            {
                "operation": operation,
                "input": key,
                "output": output,
                "cache_hit": cached,
                "worker_pid": worker,
                "transport": "RESP/TCP",
                "ttl_seconds": self.shared.ttl,
                "tool_total_ms": round((time.monotonic() - started) * 1000, 3),
                "input_json_bytes": len(canonical(self.store.artifact(key)).encode()),
                "output_json_bytes": len(canonical(body).encode()),
            },
        )
        return output

    def evidence(self, state):
        request = Request.model_validate(self.payload(state["request_ref"]))
        # Pinned-passage retrieval: rank only the supplied passages, never fetch a URL.
        from collections import Counter
        import re

        words = Counter(re.findall(r"\w+", request.question.lower()))
        evidence = []
        for source in request.sources:
            text = source.text
            for match in re.finditer(r"[^.!?\n]+[.!?]?", text):
                quote = match.group().strip()
                if not quote:
                    continue
                start = match.start() + len(match.group()) - len(match.group().lstrip())
                score = sum(
                    words[word] for word in set(re.findall(r"\w+", quote.lower()))
                )
                evidence.append(
                    {
                        "source_id": source.id,
                        "version": source.version,
                        "start": start,
                        "end": start + len(quote),
                        "quote": quote,
                        "score": score,
                    }
                )
        evidence = sorted(evidence, key=lambda e: -e["score"])[:12]
        return {
            "evidence_ref": self.artifact(
                "pinned-evidence", evidence, [state["request_ref"]]
            )
        }

    def draft(self, state):
        ref = self.model(state, "draft")
        return {"draft_ref": ref, "versions": self.add_version(state, ref, "initial")}

    def add_version(self, state, ref, origin, parent=None):
        versions = list(state.get("versions", []))
        if not versions and state.get("draft_ref"):
            versions = [{"ref": state["draft_ref"], "origin": "legacy_current", "parent": None}]
        if not versions or versions[-1]["ref"] != ref:
            versions.append({"ref": ref, "origin": origin, "parent": parent,
                             "feedback_ref": state.get("feedback_ref") if origin == "feedback_revision" else None})
        return versions

    def audit(self, state):
        key = self.tool(state, "audit")
        return {
            "audit_ref": key,
            "audit_draft_ref": state["draft_ref"],
            "issues": self.payload(key)["result"]["issues"],
        }

    def critique(self, state):
        key = self.model(
            state, "critique", self.payload(state["draft_ref"]), state["issues"]
        )
        critique = self.payload(key)
        issues = {i["id"]: i for i in state["issues"]}
        for value in critique["issues"]:
            issues.setdefault(value["id"], value)
        current = list(issues.values())
        log = {i["id"]: dict(i) for i in state.get("issue_log", [])}
        for previous in log.values():
            if previous["id"] not in issues and previous["status"] == "open":
                previous["status"] = (
                    "resolved_by_demo_revision"
                    if not self.provider.live
                    else "proposed_resolution"
                )
        for item in current:
            log[item["id"]] = {**item, "draft_ref": state["draft_ref"]}
        return {
            "critique_ref": key,
            "issues": current,
            "issue_log": list(log.values()),
            "needs_revision": critique["needs_revision"]
            and any(i["action"] == "revise" for i in current),
        }

    def revise(self, state):
        ref = self.model(state, "revise", self.payload(state["draft_ref"]), state["issues"])
        return {
            "draft_ref": ref,
            "versions": self.add_version(state, ref, "automatic_revision", state["draft_ref"]),
            "revision": state["revision"] + 1,
        }

    def feedback_revise(self, state):
        feedback = self.payload(state["feedback_ref"])
        if feedback["draft_ref"] != state["draft_ref"]:
            raise ValueError("Feedback does not match the revision input")
        item = issue("reader_feedback", "", "", feedback["feedback"], "revise")
        item["id"] = state["feedback_ref"][:16]
        ref = self.model(state, "revise", self.payload(state["draft_ref"]),
                         state["issues"] + [item], feedback=True)
        return {"draft_ref": ref, "versions": self.add_version(state, ref, "feedback_revision", state["draft_ref"]),
                "human_ref": "", "formal_ref": "", "report_ref": "", "human_action": "",
                "audit_ref": "", "audit_draft_ref": ""}

    def feedback_audit(self, state):
        update = self.audit(state)
        # Structural checks do not decide whether the reader's semantic concern was fixed.
        feedback = self.payload(state["feedback_ref"])
        pending = issue("reader_feedback_review", "", "", "Review the revision against your feedback: " + feedback["feedback"], "human")
        update["issues"] = update["issues"] + [pending]
        update["issue_log"] = state.get("issue_log", []) + [{**pending, "draft_ref": state["draft_ref"]}]
        return update

    def human(self, state):
        # No side effect before interrupt: resuming restarts this node.
        response = interrupt(
            {
                "kind": "argument_review",
                "draft_ref": state["draft_ref"],
                "issues": state["issues"],
                "provider": self.provider.name,
                "live_model": self.provider.live,
            }
        )
        self.guard(state["run_id"])
        if response.get("action") == "request_revision":
            feedback = RevisionFeedback.model_validate(response)
            if state.get("workflow_version") != "review-workflow-v2":
                raise ValueError("Start a new run to use feedback revision with this workflow")
            if feedback.expected_draft != state["draft_ref"]:
                raise ValueError("Stale draft feedback; reopen the current content")
            if state.get("feedback_round", 0):
                raise ValueError("The one reader-requested revision has already been used")
            ref, issues = state["draft_ref"], state["issues"]
            versions = state.get("versions", [])
            if feedback.edited_draft is not None:
                draft, issues = validate_draft(feedback.edited_draft.model_dump(mode="json"), self.payload(state["request_ref"]))
                if digest(draft) != digest(self.payload(ref)):
                    ref = self.artifact("argument-draft", draft, [state["request_ref"], state["draft_ref"]])
                    versions = self.add_version(state, ref, "reader_edit_before_feedback", state["draft_ref"])
            body = {"draft_ref": ref, "base_ref": state["draft_ref"], "reviewer": feedback.reviewer, "feedback": feedback.feedback}
            key = self.artifact("revision-feedback", body, [state["request_ref"], ref])
            self.store.event(state["run_id"], "reader_feedback", {"feedback_ref": key, "draft_ref": ref})
            return {"draft_ref": ref, "issues": issues, "versions": versions, "feedback_ref": key,
                    "feedback_round": 1, "human_action": "request_revision", "human_ref": "", "formal_ref": "", "report_ref": ""}
        review = HumanReview.model_validate(response)
        if review.expected_draft != state["draft_ref"]:
            raise ValueError("Stale draft review; reopen the current content")
        draft_ref = state["draft_ref"]
        issues = state["issues"]
        if review.edited_draft is not None:
            draft, issues = validate_draft(
                review.edited_draft.model_dump(mode="json"),
                self.payload(state["request_ref"]),
            )
            if digest(draft) != digest(self.payload(state["draft_ref"])):
                draft_ref = self.artifact(
                    "argument-draft", draft, [state["request_ref"], state["draft_ref"]]
                )
        body = review.model_dump(
            exclude={"edited_draft", "expected_draft"}, mode="json"
        )
        self.store.human(state["run_id"], draft_ref, body)
        key = self.artifact(
            "human-review",
            {"draft_ref": draft_ref, "review": body},
            [state["request_ref"], draft_ref],
        )
        self.store.event(
            state["run_id"],
            "human_review",
            {
                "draft_ref": draft_ref,
                "review_ref": key,
                "edited": draft_ref != state["draft_ref"],
                "decisions": {
                    k: body[k] for k in ("fidelity", "attribution", "interpretation")
                },
            },
        )
        return {"draft_ref": draft_ref, "human_ref": key, "issues": issues, "human_action": "finish",
                "versions": self.add_version(state, draft_ref, "reader_final_edit", state["draft_ref"])}

    def formal(self, state):
        human = self.payload(state["human_ref"])
        if human["draft_ref"] != state["draft_ref"]:
            raise ValueError("Human approval does not match current content")
        return {"formal_ref": self.tool(state, "formal", human["review"])}

    def report(self, state):
        review = self.payload(state["human_ref"])["review"]
        report = {
            "schema_version": "argument-review-report-v1",
            "tool_version": TOOL_VERSION,
            "revision_feedback": self.payload(state["feedback_ref"]) if state.get("feedback_ref") else None,
            "saved_versions": state.get("versions", []),
            "run_id": state["run_id"],
            "request": self.payload(state["request_ref"]),
            "draft": self.payload(state["draft_ref"]),
            "draft_ref": state["draft_ref"],
            "evidence": self.payload(state["evidence_ref"]),
            "current_issues": state["issues"],
            "issue_history": state.get("issue_log", []),
            "human_review": review,
            "review_status": "accepted"
            if all(
                review[k] == "accepted"
                for k in ("fidelity", "attribution", "interpretation")
            )
            else "disputed",
            "formal_checks": self.payload(state["formal_ref"])["result"]["results"],
            "provider": {"name": self.provider.name, "live_model": self.provider.live},
            "scope": "Human-reviewed interpretation and bounded deterministic checks. A matching quotation is not proof of support. Demo rules are not model reasoning or expert review.",
        }
        return {
            "report_ref": self.artifact(
                "argument-review-report",
                report,
                [
                    state["request_ref"],
                    state["draft_ref"],
                    state["human_ref"],
                    state["formal_ref"],
                ],
            )
        }

    def close(self):
        self.connection.close()
