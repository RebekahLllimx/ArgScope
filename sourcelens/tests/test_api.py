import time

import pytest
from fastapi.testclient import TestClient

from sourcelens.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3")) as value:
        yield value


def complete(client, job_id):
    for _ in range(100):
        job = client.get("/api/runs/" + job_id).json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Worker did not finish")


def test_api_end_to_end_with_events_and_export(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/examples").status_code == 201
    assert len(client.get("/api/documents").json()) == 3
    response = client.post(
        "/api/runs",
        json={"question": "How do recovery checkpoints work?"},
        headers={"Idempotency-Key": "one"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    job = complete(client, job_id)
    assert job["status"] == "succeeded"
    events = client.get(f"/api/runs/{job_id}/events").text
    assert "event: progress" in events and "event: done" in events
    assert client.get(f"/api/runs/{job_id}/report.md").status_code == 200
    citation = job["state"]["report"]["citations"][0]
    assert client.get("/api/chunks/" + citation["id"]).status_code == 200


def test_stale_report_is_marked_not_rewritten(client):
    source = {
        "source_key": "recovery",
        "title": "Recovery",
        "text": "# Recovery\n\nRecovery checkpoints retain a stable log position.",
    }
    client.post("/api/documents", json=source)
    job_id = client.post(
        "/api/runs", json={"question": "How do recovery checkpoints work?"}
    ).json()["id"]
    old = complete(client, job_id)["state"]["report"]
    client.post(
        "/api/documents",
        json={
            **source,
            "version_label": "v2",
            "text": "# Recovery\n\nRecovery checkpoints now use a different log position.",
        },
    )
    new = client.get("/api/runs/" + job_id).json()["state"]["report"]
    assert new["sections"] == old["sections"]
    assert new["outdated_sources"] > 0
    assert new["outdated_sources"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"question": "x"},
        {"question": "valid question", "method": "magic"},
        {"question": "valid question", "secret": 1},
    ],
)
def test_invalid_run_requests(client, body):
    assert client.post("/api/runs", json=body).status_code == 422


def test_empty_library_missing_records_and_bad_origins(client):
    assert (
        client.post(
            "/api/runs", json={"question": "How does recovery work?"}
        ).status_code
        == 400
    )
    assert client.get("/api/runs/missing").status_code == 404
    assert (
        client.post(
            "/api/examples", headers={"Origin": "https://outside.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/health", headers={"Host": "attacker.example"}).status_code
        == 400
    )


def test_upload_uses_content_not_path_and_rejects_code(client):
    data = {"source_key": "upload", "title": "Upload test"}
    result = client.post(
        "/api/documents/upload",
        data=data,
        files={
            "file": (
                "../../note.md",
                b"A readable engineering passage.",
                "text/markdown",
            )
        },
    )
    assert result.status_code == 201
    result = client.post(
        "/api/documents/upload",
        data=data,
        files={"file": ("run.py", b"print(1)", "text/plain")},
    )
    assert result.status_code == 400


def test_links_cannot_embed_credentials_or_scripts(client):
    base = {"source_key": "safe", "title": "Safe", "text": "Readable source text."}
    for url in ("javascript:alert(1)", "https://user:password@example.org"):
        assert (
            client.post("/api/documents", json={**base, "source_url": url}).status_code
            == 422
        )


def test_oversize_stream_is_bounded(client):
    response = client.post(
        "/api/documents",
        content=b"x" * 5_100_100,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in {400, 413}
