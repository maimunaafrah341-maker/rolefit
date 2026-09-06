"""PDF resume text extraction.

Defence in depth on upload, because this is the one route where a user hands the
server a binary file:

  1. Extension check   - cheap rejection of obviously wrong files.
  2. Magic-byte check  - the real format test; an extension proves nothing.
  3. Size cap          - enforced on the actual bytes read, not the declared
                         Content-Length header, which a client controls.
  4. Page cap          - a 900-page PDF should not occupy a worker.
  5. Encryption check  - password-protected files fail with a clear message
                         instead of silently extracting nothing.

Extraction stays in-memory. Nothing is written to the container filesystem, so
there is no temp file to leak or clean up.
"""
import io
import logging
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

import config

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
MAX_PAGES = 15


class ExtractionError(ValueError):
    """A user-facing upload failure. The message is safe to display."""


def _tidy(text: str) -> str:
    """PDF extraction produces ragged whitespace and hyphenated line breaks.
    Normalising here means the model sees clean text, and - importantly - that
    quote verification in schemas.verify_quotes compares like with like."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)   # rejoin hyphen-split words
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def extract_text_from_pdf(file_storage) -> str:
    """Take a Werkzeug FileStorage and return clean resume text.

    Raises ExtractionError with a message intended for the user.
    """
    filename = (getattr(file_storage, "filename", "") or "").strip()
    if not filename:
        raise ExtractionError("No file was selected.")
    if not filename.lower().endswith(".pdf"):
        raise ExtractionError(
            "Only PDF files are supported. For .docx, copy the text and paste it instead."
        )

    # Read at most one byte over the limit: enough to detect an oversized file
    # without pulling an unbounded upload into memory.
    raw = file_storage.read(config.MAX_UPLOAD_BYTES + 1)
    if len(raw) > config.MAX_UPLOAD_BYTES:
        limit_mb = config.MAX_UPLOAD_BYTES / (1024 * 1024)
        raise ExtractionError("That PDF is larger than {:.0f} MB.".format(limit_mb))
    if not raw:
        raise ExtractionError("That file is empty.")
    if not raw.startswith(PDF_MAGIC):
        # The extension said PDF but the bytes disagree - never hand this to a parser.
        raise ExtractionError("That file is not a valid PDF.")

    try:
        reader = PdfReader(io.BytesIO(raw))
        if getattr(reader, "is_encrypted", False):
            raise ExtractionError(
                "That PDF is password protected. Remove the password, or paste "
                "the text instead."
            )
        pages = reader.pages[:MAX_PAGES]
        chunks = []
        for page in pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one bad page must not kill the upload
                logger.warning("Skipped an unreadable PDF page")
    except ExtractionError:
        raise
    except (PdfReadError, Exception) as exc:  # noqa: BLE001
        logger.warning("PDF parse failed: %s", exc)
        raise ExtractionError(
            "That PDF could not be read. It may be corrupted - try pasting the text instead."
        )

    text = _tidy("\n".join(chunks))

    if len(text) < config.MIN_RESUME_CHARS:
        raise ExtractionError(
            "Almost no text could be extracted. This usually means the PDF is a "
            "scan or an image export. Please paste your resume text instead."
        )

    if len(text) > config.MAX_RESUME_CHARS:
        text = text[: config.MAX_RESUME_CHARS]

    return text
