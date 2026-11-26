"""A bounded evidence workflow with application-owned transactional checkpoints."""

import fcntl
import logging
import re
import sqlite3
import threading
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from .retrieval import Retriever, terms
from .store import Store

LOG = logging.getLogger(__name__)
WORKFLOW_VERSION = "evidence-v1"


class State(TypedDict, total=False):
    job_id: str
    question: str
    method: str
    snapshot: list[str]
    next_node: str
    round: int
    queries: list[str]
    evidence: list[dict]
    gaps: list[str]
    report: dict
    workflow_version: str


def plan_queries(question):
    """Use explicit subquestions, or split a clearly marked comparison."""
    if ";" in question:
        return [part.strip() for part in question.split(";") if part.strip()][:3]
    match = re.match(
        r"compare\s+(.+?)\s+(?:and|versus|vs\.?|with)\s+(.+?)(?:[?.]|$)",
        question.strip(),
        re.I,
    )
    if match:
        return [question, match.group(1), match.group(2)][:3]
    return [question]


def relevant_sentences(query, chunk):
    """Return exact excerpts, not generated claims or assertions of entailment."""
    wanted = set(terms(query))
    if not wanted:
        return []
    pieces = re.split(r"(?<=[.!?])\s+|\n+", chunk["body"])
    scored = []
    for sentence in pieces:
        sentence = sentence.strip()
        overlap = len(wanted & set(terms(sentence + " " + chunk["heading"])))
        # Single generic shared words should not produce an answer.
        if overlap >= min(2, len(wanted)) and len(sentence) >= 25:
            scored.append((overlap, sentence))
    return [text for _, text in sorted(scored, key=lambda pair: -pair[0])[:2]]


class ResearchWorkflow:
    def __init__(self, store: Store, retriever: Retriever):
        self.store, self.retriever = store, retriever
        graph = StateGraph(State)
        for name in ("plan", "retrieve", "assess", "follow_up", "compose"):
            graph.add_node(name, self._node(name))
        paths = {
            name: name
            for name in ("plan", "retrieve", "assess", "follow_up", "compose")
        }
        paths["done"] = END
        graph.add_conditional_edges(START, lambda state: state["next_node"], paths)
        for name in ("plan", "retrieve", "assess", "follow_up", "compose"):
            graph.add_conditional_edges(name, lambda state: state["next_node"], paths)
        self.graph = graph.compile()

    def _node(self, name):
        def execute(state):
            update = getattr(self, name)(state)
            next_state = {**state, **update, "workflow_version": WORKFLOW_VERSION}
            self.store.checkpoint(state["job_id"], next_state, name)
            return update

        return execute

    def plan(self, state):
        return {"queries": plan_queries(state["question"]), "next_node": "retrieve"}

    def retrieve(self, state):
        evidence = {item["id"]: item for item in state["evidence"]}
        for query in state["queries"]:
            for chunk in self.retriever.search(
                query, snapshot=state["snapshot"], method=state["method"], limit=6
            ):
                evidence.setdefault(chunk["id"], chunk)
        # Three subquestions, six results each, and one follow-up stay bounded.
        return {"evidence": list(evidence.values())[:24], "next_node": "assess"}

    def assess(self, state):
        original = plan_queries(state["question"])
        gaps = [
            query
            for query in original
            if not any(relevant_sentences(query, chunk) for chunk in state["evidence"])
        ]
        return {
            "gaps": gaps,
            "next_node": "follow_up" if gaps and state["round"] == 0 else "compose",
        }

    def follow_up(self, state):
        return {
            "queries": [" ".join(terms(query)) for query in state["gaps"]][:3],
            "round": 1,
            "next_node": "retrieve",
        }

    def compose(self, state):
        sections, citations = [], {}
        for query in plan_queries(state["question"]):
            quotes, used = [], set()
            for chunk in state["evidence"]:
                for quote in relevant_sentences(query, chunk):
                    if quote in used:
                        continue
                    used.add(quote)
                    key = chunk["id"]
                    if key not in citations:
                        citations[key] = {
                            field: chunk[field]
                            for field in (
                                "id",
                                "version_id",
                                "document_id",
                                "title",
                                "label",
                                "heading",
                                "page",
                                "line_start",
                                "line_end",
                                "source_url",
                                "digest",
                            )
                        }
                        citations[key]["citation"] = "C" + str(len(citations))
                    quotes.append(
                        {
                            "text": quote,
                            "chunk_id": key,
                            "citation": citations[key]["citation"],
                        }
                    )
                    if len(quotes) >= 4:
                        break
                if len(quotes) >= 4:
                    break
            sections.append({"question": query, "quotes": quotes})
        found = sum(bool(section["quotes"]) for section in sections)
        status = (
            "insufficient_evidence"
            if not found
            else "evidence_found"
            if found == len(sections)
            else "partial_evidence"
        )
        report = {
            "question": state["question"],
            "status": status,
            "answer_mode": "extractive",
            "method": state["method"],
            "sections": sections,
            "citations": list(citations.values()),
            "retrieval_rounds": state["round"] + 1,
            "workflow_version": WORKFLOW_VERSION,
        }
        self.validate_report(report, state["snapshot"])
        return {"report": report, "next_node": "done"}

    def validate_report(self, report, snapshot):
        chunks = {c["id"]: c for c in self.store.chunks(snapshot)}
        citations = {c["id"]: c for c in report["citations"]}
        for section in report["sections"]:
            for quote in section["quotes"]:
                chunk = chunks.get(quote["chunk_id"])
                citation = citations.get(quote["chunk_id"])
                if (
                    not chunk
                    or not citation
                    or quote["text"] not in chunk["body"]
                    or citation["citation"] != quote["citation"]
                ):
                    raise ValueError("Report citation failed source verification")
                if any(
                    citation[field] != chunk[field]
                    for field in (
                        "version_id",
                        "document_id",
                        "digest",
                        "title",
                        "label",
                        "heading",
                        "page",
                        "line_start",
                        "line_end",
                        "source_url",
                    )
                ):
                    raise ValueError(
                        "Report citation metadata failed source verification"
                    )

    def run(self, job_id):
        state = self.store.job(job_id)["state"]
        if state.get("workflow_version", WORKFLOW_VERSION) != WORKFLOW_VERSION:
            raise ValueError(
                "The saved workflow version requires an explicit migration"
            )
        # Document content stays local even if the shell enables global tracing.
        with tracing_context(enabled=False):
            self.graph.invoke(state, {"recursion_limit": 15})
        self.store.finish(job_id)


class Worker:
    """One process owns a database worker; individual jobs checkpoint each node."""

    def __init__(self, store, workflow):
        self.store, self.workflow = store, workflow
        self.stop_event = threading.Event()
        self.thread = None
        self.lock_file = None

    def start(self):
        self.lock_file = open(str(self.store.path) + ".worker.lock", "a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError("Another SourceLens worker already owns this database")
        try:
            self.store.recover()
            self.thread = threading.Thread(
                target=self._loop, name="sourcelens-worker", daemon=True
            )
            self.thread.start()
        except BaseException:
            self.lock_file.close()
            raise

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                job_id = self.store.claim()
            except sqlite3.OperationalError:
                LOG.warning("Queue temporarily unavailable; retrying")
                self.stop_event.wait(0.5)
                continue
            if not job_id:
                self.stop_event.wait(0.25)
                continue
            try:
                self.workflow.run(job_id)
            except sqlite3.OperationalError:
                self.store.fail(job_id, "Database operation failed", transient=True)
                self.stop_event.wait(0.5)
            except Exception:
                LOG.exception("Research run %s failed", job_id)
                self.store.fail(
                    job_id,
                    "Research stopped; inspect the local server log and retry after correcting the cause",
                )

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError(
                    "Worker is still running; do not start a second process"
                )
        if self.lock_file:
            self.lock_file.close()


def markdown_report(report):
    lines = [
        "# " + report["question"],
        "",
        "Mode: source excerpts · Retrieval: " + report["method"],
        "",
    ]
    for section in report["sections"]:
        lines.extend(["## " + section["question"], ""])
        if not section["quotes"]:
            lines.extend(["No matching evidence found in this library snapshot.", ""])
        for quote in section["quotes"]:
            lines.extend(
                [
                    "> " + quote["text"].replace("\n", "\n> "),
                    "",
                    "[" + quote["citation"] + "]",
                    "",
                ]
            )
    lines.extend(["## Sources", ""])
    for citation in report["citations"]:
        location = (
            f"page {citation['page']}"
            if citation["page"]
            else f"lines {citation['line_start']}-{citation['line_end']}"
        )
        lines.append(
            f"- [{citation['citation']}] {citation['title']} · {citation['label']} · {citation['heading']} · {location} · chunk `{citation['id']}`"
        )
    return "\n".join(lines) + "\n"
