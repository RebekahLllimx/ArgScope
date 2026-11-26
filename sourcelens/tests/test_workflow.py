from concurrent.futures import ThreadPoolExecutor
import time
from unittest.mock import patch

import pytest

from sourcelens.retrieval import Retriever
from sourcelens.store import Conflict
from sourcelens.workflow import ResearchWorkflow, Worker, markdown_report
from conftest import add


def run(store, question, key="request"):
    job_id, _ = store.create_job(question, "hybrid", key)
    assert store.claim() == job_id
    workflow = ResearchWorkflow(store, Retriever(store))
    workflow.run(job_id)
    return store.job(job_id)


def test_complete_workflow_citations_and_no_network(library):
    with patch(
        "socket.create_connection",
        side_effect=AssertionError("Unexpected network connection"),
    ):
        job = run(library, "How do checkpoints reduce recovery replay?")
    report = job["state"]["report"]
    assert job["status"] == "succeeded" and report["status"] == "evidence_found"
    assert [item["citation"] for item in report["citations"]] == [
        "C" + str(i + 1) for i in range(len(report["citations"]))
    ]
    for section in report["sections"]:
        for quote in section["quotes"]:
            assert quote["text"] in library.chunk(quote["chunk_id"])["body"]
    assert "## Sources" in markdown_report(report)


def test_no_answer_stops_after_one_followup(library):
    report = run(library, "What is the boiling point of titanium?")["state"]["report"]
    assert report["status"] == "insufficient_evidence"
    assert report["retrieval_rounds"] == 2
    assert report["citations"] == []


def test_comparison_and_partial_evidence(library):
    report = run(
        library,
        "Explain write-ahead log recovery; Explain titanium boiling temperature",
    )["state"]["report"]
    assert len(report["sections"]) == 2 and report["status"] == "partial_evidence"


def test_submission_idempotency_and_conflicting_reuse(library):
    first = library.create_job("How do recovery checkpoints work?", "hybrid", "same")
    second = library.create_job("How do recovery checkpoints work?", "hybrid", "same")
    assert first[0] == second[0] and first[1] and not second[1]
    with pytest.raises(Conflict):
        library.create_job("Different question about recovery", "hybrid", "same")


def test_concurrent_creation_and_claim_are_atomic(library):
    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(
            pool.map(
                lambda _: library.create_job(
                    "How does log recovery work?", "hybrid", "same"
                ),
                range(4),
            )
        )
        claims = list(pool.map(lambda _: library.claim(), range(4)))
    assert sum(value[1] for value in created) == 1
    assert sum(value is not None for value in claims) == 1


def test_pinned_run_survives_source_update(store):
    first = add(
        store, "# Recovery\n\nThe recovery checkpoint replays committed log changes."
    )
    job_id, _ = store.create_job(
        "How does recovery replay log changes?", "hybrid", "old"
    )
    add(
        store,
        "# Recovery\n\nThe new checkpoint uses another recovery strategy.",
        label="v2",
    )
    store.claim()
    ResearchWorkflow(store, Retriever(store)).run(job_id)
    report = store.job(job_id)["state"]["report"]
    assert report["citations"]
    assert all(c["version_id"] == first["version_id"] for c in report["citations"])


def test_restart_resumes_checkpoint_without_replanning(library):
    job_id, _ = library.create_job(
        "How do recovery checkpoints work?", "hybrid", "restart"
    )
    library.claim()
    workflow = ResearchWorkflow(library, Retriever(library))
    with patch.object(
        workflow, "retrieve", side_effect=RuntimeError("Crash before commit")
    ):
        with pytest.raises(RuntimeError):
            workflow.run(job_id)
    assert library.job(job_id)["state"]["next_node"] == "retrieve"
    assert library.recover() == 1
    library.claim()
    with patch.object(
        workflow, "plan", side_effect=AssertionError("Already checkpointed")
    ):
        workflow.run(job_id)
    events = library.events(job_id)
    assert sum(event["payload"].get("completed") == "plan" for event in events) == 1
    assert library.job(job_id)["status"] == "succeeded"


def test_finished_checkpoint_is_recoverable_before_status_commit(library):
    job_id, _ = library.create_job(
        "How do recovery checkpoints work?", "hybrid", "final"
    )
    library.claim()
    workflow = ResearchWorkflow(library, Retriever(library))
    with patch.object(
        library, "finish", side_effect=RuntimeError("Crash after final checkpoint")
    ):
        with pytest.raises(RuntimeError):
            workflow.run(job_id)
    library.recover()
    library.claim()
    with patch.object(
        workflow, "compose", side_effect=AssertionError("Already committed")
    ):
        workflow.run(job_id)
    assert library.job(job_id)["status"] == "succeeded"


def test_checkpoint_and_event_rollback_together(library):
    job_id, _ = library.create_job(
        "How do recovery checkpoints work?", "hybrid", "atomic"
    )
    library.claim()
    before = library.job(job_id)["state"]
    with patch.object(
        library, "_event", side_effect=RuntimeError("Event insert failure")
    ):
        with pytest.raises(RuntimeError):
            library.checkpoint(job_id, {**before, "next_node": "compose"}, "plan")
    assert library.job(job_id)["state"] == before


def test_retry_budget_and_manual_retry(library):
    job_id, _ = library.create_job(
        "How do recovery checkpoints work?", "hybrid", "retry"
    )
    for index in range(3):
        library.claim()
        library.fail(job_id, "temporary", transient=True)
        assert library.job(job_id)["status"] == ("queued" if index < 2 else "failed")
    library.retry(job_id)
    assert library.job(job_id)["attempts"] == 0
    with pytest.raises(Conflict):
        library.retry(job_id)


def test_worker_lock_prevents_duplicate_process_ownership(library):
    first = Worker(library, ResearchWorkflow(library, Retriever(library)))
    second = Worker(library, ResearchWorkflow(library, Retriever(library)))
    first.start()
    try:
        with pytest.raises(RuntimeError, match="Another"):
            second.start()
    finally:
        first.stop()


def test_worker_failure_and_manual_retry_reach_terminal_states(library):
    job_id, _ = library.create_job(
        "How do recovery checkpoints work?", "hybrid", "worker-failure"
    )
    workflow = ResearchWorkflow(library, Retriever(library))
    worker = Worker(library, workflow)

    def await_status(status):
        for _ in range(100):
            if library.job(job_id)["status"] == status:
                return
            time.sleep(0.02)
        raise AssertionError("Worker did not reach " + status)

    with patch.object(workflow, "run", side_effect=RuntimeError("Deliberate failure")):
        worker.start()
        try:
            await_status("failed")
        finally:
            worker.stop()
    library.retry(job_id)
    recovered = Worker(library, workflow)
    recovered.start()
    try:
        await_status("succeeded")
        assert library.job(job_id)["error"] is None
    finally:
        recovered.stop()


def test_global_tracing_cannot_export_document_content(library, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with patch(
        "socket.create_connection",
        side_effect=AssertionError("Unexpected network connection"),
    ):
        job = run(library, "How do recovery checkpoints work?", key="offline")
    assert job["status"] == "succeeded"


def test_fabricated_citation_is_rejected(library):
    job = run(library, "How do recovery checkpoints work?")
    report = job["state"]["report"]
    report["sections"][0]["quotes"][0][
        "text"
    ] = "This quotation does not appear in any source."
    with pytest.raises(ValueError, match="verification"):
        ResearchWorkflow(library, Retriever(library)).validate_report(
            report, job["state"]["snapshot"]
        )


def test_misattributed_citation_metadata_is_rejected(library):
    job = run(library, "How do recovery checkpoints work?")
    report = job["state"]["report"]
    report["citations"][0]["title"] = "An unrelated source"
    with pytest.raises(ValueError, match="metadata"):
        ResearchWorkflow(library, Retriever(library)).validate_report(
            report, job["state"]["snapshot"]
        )
