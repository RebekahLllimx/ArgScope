"""Bounded document parsing; source positions survive ingestion."""

from dataclasses import dataclass
from io import BytesIO
import re

MAX_BYTES = 5_000_000
MAX_CHUNKS = 500
PARSER_VERSION = "paragraph-v1"


class DocumentError(ValueError):
    """A document cannot be parsed within the supported contract."""


@dataclass(frozen=True)
class Chunk:
    text: str
    heading: str
    page: int | None
    line_start: int | None
    line_end: int | None


def chunk_markdown(text: str, *, page: int | None = None) -> list[Chunk]:
    """Keep paragraphs and headings; split long paragraphs at word boundaries."""
    if not text.strip() or "\x00" in text:
        raise DocumentError("Provide nonempty text without NUL characters")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise DocumentError("Documents must be at most 5 MB")
    result, paragraph = [], []
    heading, first = "Document", 1

    def flush(last):
        if not paragraph:
            return
        body = "\n".join(paragraph).strip()
        # Preserve exact text within each chunk so quotes can be verified.
        for match in re.finditer(r"[\s\S]{1,1200}(?:\s+|$)|\S{1201,}", body):
            part = match.group().strip()
            if part:
                result.append(
                    Chunk(
                        part,
                        heading,
                        page,
                        None if page else first,
                        None if page else last,
                    )
                )
        paragraph.clear()

    for number, line in enumerate(text.replace("\r\n", "\n").splitlines(), 1):
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            flush(number - 1)
            heading = match.group(1)
        elif not line.strip():
            flush(number - 1)
        else:
            if not paragraph:
                first = number
            paragraph.append(line)
    flush(len(text.splitlines()))
    if (
        not result
        or len(result) > MAX_CHUNKS
        or any(len(c.text) > 4000 for c in result)
    ):
        raise DocumentError(
            "Use 1-500 readable chunks with no token longer than 4000 characters"
        )
    return result


def parse_file(filename: str, data: bytes) -> list[Chunk]:
    if len(data) > MAX_BYTES:
        raise DocumentError("Documents must be at most 5 MB")
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension in {"md", "markdown", "txt"}:
        try:
            return chunk_markdown(data.decode("utf-8-sig"))
        except UnicodeError as error:
            raise DocumentError("Text files must use UTF-8") from error
    if extension != "pdf":
        raise DocumentError("Upload Markdown, UTF-8 text, or a text-based PDF")
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
            raise DocumentError("Use an unencrypted PDF with at most 100 pages")
        result = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                result.extend(chunk_markdown(text, page=number))
            if len(result) > MAX_CHUNKS:
                raise DocumentError("PDF exceeds the 500-chunk limit")
        if not result:
            raise DocumentError(
                "No readable text found; scanned PDFs need OCR before import"
            )
        return result
    except DocumentError:
        raise
    except Exception as error:
        raise DocumentError("Unable to parse this PDF") from error
