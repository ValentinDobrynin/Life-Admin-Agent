from __future__ import annotations

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Pages with at least this many non-whitespace characters are considered
# real text; below that threshold the caller should fall back to Vision OCR.
_MIN_TEXT_LEN = 30

# Hard cap on how many pages we render to images for OCR fallback. PyMuPDF
# rasterisation + base64-encoded payloads to OpenAI Vision are memory-heavy;
# on Render Starter (512 MB RAM) anything past a handful of pages risks OOM.
DEFAULT_MAX_OCR_PAGES = 3
DEFAULT_OCR_DPI = 150


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


def render_pages_to_images(
    pdf_bytes: bytes,
    dpi: int = DEFAULT_OCR_DPI,
    max_pages: int = DEFAULT_MAX_OCR_PAGES,
) -> tuple[list[bytes], int]:
    """Render the first ``max_pages`` PDF pages to PNG images for Vision OCR.

    Returns ``(images, total_pages)``. ``len(images) < total_pages`` means we
    truncated; the caller should surface that to the user. Returns ``([], 0)``
    if PyMuPDF is unavailable or the PDF cannot be opened.
    """
    try:
        import pymupdf  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pymupdf is not installed — scanned PDF fallback disabled")
        return [], 0

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        logger.exception("pymupdf failed to open PDF")
        return [], 0

    images: list[bytes] = []
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    try:
        total_pages = len(doc)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=matrix)
            images.append(pix.tobytes("png"))
            del pix
    finally:
        doc.close()
    return images, total_pages
