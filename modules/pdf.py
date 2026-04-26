from __future__ import annotations

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Pages with at least this many non-whitespace characters are considered
# real text; below that threshold the caller should fall back to Vision OCR.
_MIN_TEXT_LEN = 30


def extract_text_layer(pdf_bytes: bytes) -> str:
    """Extract text from a PDF using its text layer. Returns "" for scans / invalid input."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        logger.exception("Failed to open PDF")
        return ""

    chunks: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            logger.exception("PDF page extract failed")
            continue
        if len(text.strip()) >= _MIN_TEXT_LEN:
            chunks.append(text.strip())

    return "\n\n".join(chunks).strip()


def render_pages_to_images(pdf_bytes: bytes, dpi: int = 200) -> list[bytes]:
    """Render every PDF page to a PNG image. Used as Vision OCR fallback for scans.

    Returns an empty list if PyMuPDF is unavailable or the PDF cannot be opened.
    Lazily imports pymupdf so unrelated tests don't pay the import cost.
    """
    try:
        import pymupdf  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pymupdf is not installed — scanned PDF fallback disabled")
        return []

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        logger.exception("pymupdf failed to open PDF")
        return []

    images: list[bytes] = []
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            images.append(pix.tobytes("png"))
    finally:
        doc.close()
    return images
