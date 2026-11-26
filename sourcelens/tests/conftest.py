from pathlib import Path

import pytest

from sourcelens.documents import chunk_markdown
from sourcelens.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "library.sqlite3")


def add(store, text, key="notes", label="v1", title="Engineering notes"):
    return store.ingest(
        source_key=key,
        title=title,
        label=label,
        source_url=None,
        data=text.encode(),
        chunks=chunk_markdown(text),
    )


@pytest.fixture
def library(store):
    for path in sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.md")):
        text = path.read_text()
        add(store, text, key=path.stem, title=text.splitlines()[0].lstrip("# "))
    return store
