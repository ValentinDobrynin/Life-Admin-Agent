from __future__ import annotations

from modules import pdf


def test_extract_text_layer_invalid_bytes() -> None:
    assert pdf.extract_text_layer(b"not a pdf") == ""


def test_extract_text_layer_empty_pdf() -> None:
    assert pdf.extract_text_layer(b"") == ""


def test_render_pages_to_images_invalid() -> None:
    assert pdf.render_pages_to_images(b"not a pdf") == []
