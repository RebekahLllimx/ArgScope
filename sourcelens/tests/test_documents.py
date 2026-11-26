from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from sourcelens.documents import DocumentError, chunk_markdown, parse_file
from conftest import add


def test_headings_and_line_locations():
    chunks = chunk_markdown(
        "# System\n\n## Recovery\n\nA log contains committed changes.\nA replay restores them.\n"
    )
    assert len(chunks) == 1
    assert chunks[0].heading == "Recovery"
    assert (chunks[0].line_start, chunks[0].line_end) == (5, 6)


def test_long_paragraph_preserves_all_words():
    text = " ".join("term" + str(i) for i in range(1000))
    chunks = chunk_markdown(text)
    assert " ".join(chunk.text for chunk in chunks).split() == text.split()
    assert max(len(c.text) for c in chunks) < 1300


@pytest.mark.parametrize(
    "text", ["", "  \n", "abc\x00def", "a" * 6000, "x" * 5_000_001]
)
def test_invalid_text_is_bounded(text):
    with pytest.raises(DocumentError):
        chunk_markdown(text)


def test_markdown_utf8_and_unsupported_uploads():
    assert (
        parse_file("NOTES.MD", b"A readable passage.")[0].text == "A readable passage."
    )
    for name, data in [
        ("bad.md", b"\xff"),
        ("code.py", b"print(1)"),
        ("bad.pdf", b"not pdf"),
    ]:
        with pytest.raises(DocumentError):
            parse_file(name, data)


def test_scanned_pdf_does_not_silently_import():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    with pytest.raises(DocumentError, match="No readable text"):
        parse_file("blank.pdf", stream.getvalue())


def test_text_pdf_preserves_page_number():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 10 100 Td (Recovery checkpoints reduce log replay.) Tj ET"
    )
    page[NameObject("/Contents")] = content
    stream = BytesIO()
    writer.write(stream)
    chunks = parse_file("text.pdf", stream.getvalue())
    assert chunks[0].page == 1 and chunks[0].line_start is None
    assert "Recovery checkpoints" in chunks[0].text


def test_idempotent_ingestion_and_old_content_does_not_rollback(store):
    first = add(store, "Original recovery implementation.")
    second = add(store, "Updated recovery implementation.", label="v2")
    again = add(store, "Original recovery implementation.")
    assert first["created"] and second["created"]
    assert not again["created"] and not again["current"]
    assert store.snapshot() == [second["version_id"]]
    assert len(store.versions(first["document_id"])) == 2
    assert (
        store.chunks([first["version_id"]])[0]["body"]
        == "Original recovery implementation."
    )


def test_ingestion_rolls_back_document_on_failure(store):
    from unittest.mock import patch

    with patch("sourcelens.store.hashlib.sha256") as hashing:
        hashing.return_value.hexdigest.return_value = "same-id"
        with pytest.raises(Exception):
            add(store, "First paragraph.\n\nSecond paragraph.")
    assert store.documents() == []
