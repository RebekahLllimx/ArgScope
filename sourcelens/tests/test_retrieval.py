import pytest

from sourcelens.retrieval import Retriever, terms
from conftest import add


@pytest.mark.parametrize("method", ["bm25", "tfidf", "hybrid"])
def test_retrieval_finds_relevant_passage(library, method):
    results = Retriever(library).search("idempotency key request retry", method=method)
    assert results and results[0]["heading"] == "Idempotent effects"


def test_query_is_not_fts_or_sql_syntax(library):
    retriever = Retriever(library)
    retriever.search('" OR * NEAR(foo) ; DROP TABLE documents; --')
    assert len(library.documents()) == 3
    assert retriever.search("and the of") == []


def test_search_uses_current_version_and_can_pin_old(store):
    first = add(store, "# Alpha\n\nPrevious log flushing policy was synchronous.")
    second = add(
        store,
        "# Beta\n\nCurrent recovery policy uses a background checkpoint.",
        label="v2",
    )
    retriever = Retriever(store)
    assert all(
        c["version_id"] == second["version_id"]
        for c in retriever.search("recovery checkpoint")
    )
    old = retriever.search("previous log flushing", snapshot=[first["version_id"]])
    assert old and all(c["version_id"] == first["version_id"] for c in old)
    assert old[0]["is_current"] == 0


def test_rankings_remain_stable_for_same_snapshot(library):
    retriever = Retriever(library)
    a = retriever.search("checkpoint recovery")
    b = retriever.search("checkpoint recovery")
    assert a == b
    assert len(retriever._cache) == 1


def test_cached_snapshot_refreshes_current_version_indicator(store):
    original = add(
        store, "# Recovery\n\nRecovery checkpoints restore committed log changes."
    )
    retriever = Retriever(store)
    snapshot = [original["version_id"]]
    assert retriever.search("recovery checkpoints", snapshot=snapshot)[0]["is_current"]
    add(
        store,
        "# Recovery\n\nRecovery checkpoints now apply a changed replay policy.",
        label="v2",
    )
    assert not retriever.search("recovery checkpoints", snapshot=snapshot)[0][
        "is_current"
    ]


def test_empty_library_and_invalid_method(store):
    assert Retriever(store).search("recovery") == []
    with pytest.raises(ValueError):
        Retriever(store).search("recovery", method="neural")
