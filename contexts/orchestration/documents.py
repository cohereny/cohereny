"""
contexts/orchestration/documents.py

A document a run received, read once: bytes in, text and findings out
(CHY-174, ADR-049).

Once, at the upload, and nowhere else. The banking pilot read its PDF inside a
node; here the file is read before anything is stored, so what the database
keeps is text — with the run's placeholders, put in by the service — and never
the file with every name in it unreplaced.

**Refusing is part of reading.** A PDF without a text layer would come out as
an empty string, and an empty document reaching a model reads like a document
that said nothing. It is refused instead, and so is everything else this cannot
take as it is: too large, too many pages, encrypted, not parseable, not text.
The reasons name the limit or the problem, never the content.

**The limits come from two places.** The installation sets a ceiling
(DOCUMENT_MAX_BYTES, DOCUMENT_MAX_PAGES) that protects the service; a document
node may only narrow it (ADR-049 point 4). The ceiling is configuration, which
this context may not import (ADR-030) — the transport hands it in, as it hands
in the keys of the placeholder mapping.

Dependencies:
  - pypdf (PDF to text), core.ingress (the scan)
"""
from __future__ import annotations

import io
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from pypdf import PdfReader

from contexts.orchestration.errors import (
    DocumentMediaTypeUnsupported,
    DocumentTooLarge,
    DocumentUnreadable,
)
from core import ingress

#: What a document may be (041). Nothing else is parsed.
PDF = "application/pdf"
TEXT = "text/plain"
SUPPORTED_MEDIA_TYPES: tuple[str, ...] = (PDF, TEXT)


@dataclass(frozen=True)
class DocumentLimits:
    """The installation's ceiling for one document.

    Handed in by the transport, which reads the configuration. A node narrows
    it and never widens it: activation refuses a node above it (rule
    `document_limit_above_installation`), and should the ceiling be lowered
    after a release, the smaller value is what applies.
    """

    max_bytes: int
    max_pages: int

    def for_node(self, max_bytes: int | None, max_pages: int | None) -> DocumentLimits:
        """The limits that apply to one node: its own where it set one, never above these."""
        return DocumentLimits(
            max_bytes=min(self.max_bytes, max_bytes or self.max_bytes),
            max_pages=min(self.max_pages, max_pages or self.max_pages),
        )


@dataclass(frozen=True)
class DocumentUpload:
    """One document as it arrived: for which run, under which name, which bytes
    and which declared type. One thing, so it travels as one."""

    run_id: UUID
    name: str
    data: bytes
    content_type: str | None


@dataclass(frozen=True)
class ReadDocument:
    """A document as text, and what it was."""

    media_type: str
    bytes: int
    #: PDF only; a text file has no pages.
    pages: int | None
    text: str


def media_type_of(content_type: str | None) -> str:
    """`text/plain; charset=utf-8` → `text/plain`. Lower case, without parameters."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def read(data: bytes, content_type: str | None, *, installation: DocumentLimits,
         node_max_bytes: int | None, node_max_pages: int | None) -> ReadDocument:
    """The document as text, or the reason it cannot be taken.

    The media type is the one the caller declared. It is not sniffed: a file
    declared as PDF that is not one is refused as unparsable, and a text file
    declared as PDF is not quietly read as text.
    """
    media_type = media_type_of(content_type)
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise DocumentMediaTypeUnsupported(media_type or "(none)", SUPPORTED_MEDIA_TYPES)
    limits = installation.for_node(node_max_bytes, node_max_pages)
    if len(data) > limits.max_bytes:
        raise DocumentTooLarge(limits.max_bytes, installation_limit=installation.max_bytes,
                               node_limit=node_max_bytes, size=len(data))
    if not data:
        raise DocumentUnreadable.empty()
    if media_type == TEXT:
        return ReadDocument(media_type, len(data), None, _text(data))
    text, pages = _pdf(data, limits.max_pages)
    return ReadDocument(media_type, len(data), pages, text)


def _text(data: bytes) -> str:
    """UTF-8, strictly; a byte order mark is dropped.

    A NUL character is refused: it is not text, and PostgreSQL cannot store it
    in a text column — accepted here, it would come back as a server error.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentUnreadable.not_utf8_text() from exc
    if "\x00" in text:
        raise DocumentUnreadable.contains_nul()
    if not text.strip():
        raise DocumentUnreadable.empty()
    return text


def _pdf(data: bytes, max_pages: int) -> tuple[str, int]:
    """The text of every page, pages separated by a blank line, and the page count.

    The pages are counted before any is read, so a document over the limit
    costs no extraction. NUL characters are removed from what the extractor
    returns: there they are an artefact of the font encoding, not content.
    """
    reader = _parsed(lambda: PdfReader(io.BytesIO(data)))
    if _parsed(lambda: reader.is_encrypted):
        raise DocumentUnreadable.encrypted()
    pages = _parsed(lambda: len(reader.pages))
    if pages > max_pages:
        raise DocumentUnreadable.too_many_pages(pages, max_pages)
    text = _parsed(lambda: "\n\n".join(page.extract_text() or "" for page in reader.pages))
    text = text.replace("\x00", "")
    if not pages or not text.strip():
        raise DocumentUnreadable.no_text()
    return text, pages


def _parsed[T](step: Callable[[], T]) -> T:
    """One step of reading foreign bytes, or `not_a_pdf`.

    Every exception, not a list of pypdf's: a parser handed bytes somebody else
    made breaks in ways no list covers, and an exception that escaped here would
    answer a broken upload with a server error.
    """
    try:
        return step()
    except Exception as exc:
        raise DocumentUnreadable.not_a_pdf() from exc


def findings(text: str) -> list[dict]:
    """What the scan found, counted by kind and rule — never an excerpt (CHY-135).

    `core.ingress.scan` returns each hit with the text around it. That excerpt
    is document text: it must reach neither the audit log, which is kept for
    good, nor a response. What stays is which rule fired and how often.
    """
    counted = Counter((f.kind, f.rule) for f in ingress.scan(text))
    return [{"kind": kind, "rule": rule, "count": count}
            for (kind, rule), count in sorted(counted.items())]
