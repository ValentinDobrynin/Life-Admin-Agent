from __future__ import annotations

from modules import pdf


def test_extract_text_layer_invalid_bytes() -> None:
    assert pdf.extract_text_layer(b"not a pdf") == ""


def test_extract_text_layer_empty_pdf() -> None:
    assert pdf.extract_text_layer(b"") == ""


def test_render_pages_to_images_invalid() -> None:
    assert pdf.render_pages_to_images(b"not a pdf") == ([], 0)


def test_render_pages_to_images_truncates_long_pdf() -> None:
    pymupdf = _import_pymupdf()
    if pymupdf is None:
        return

    doc = pymupdf.open()
    try:
        for _ in range(5):
            doc.new_page(width=100, height=100)
        pdf_bytes: bytes = doc.tobytes()
    finally:
        doc.close()

    images, total = pdf.render_pages_to_images(pdf_bytes, max_pages=2)
    assert total == 5
    assert len(images) == 2


def _import_pymupdf() -> object | None:
    try:
        import pymupdf  # type: ignore[import-untyped]
    except ImportError:
        return None
    return pymupdf
