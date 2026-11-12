"""PDFs for the document tests, built by hand (CHY-174).

No generator library: a PDF with text needs five objects and an index, and
writing them here keeps the test dependencies what they were. Each page
carries its lines in Helvetica with WinAnsiEncoding, so pypdf reads back what
was written — umlauts included, which is what a German statement has.
"""
from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter


def _escaped(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf(*pages: str) -> bytes:
    """A PDF with one page per argument; an empty string is a page without text."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
         f"/Count {len(pages)} >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for page_id, text in zip(page_ids, pages, strict=True):
        operators = ["BT", "/F1 11 Tf", "14 TL", "50 800 Td"]
        operators += [f"({_escaped(line)}) Tj T*" for line in text.split("\n") if line]
        operators.append("ET")
        stream = "\n".join(operators).encode("cp1252")
        objects.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
             f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>").encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    index = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, index)
    return bytes(out)


def encrypted(source: bytes) -> bytes:
    """The same PDF behind a user password."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(source)))
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
