from __future__ import annotations

from modules import cards


def test_render_verification_card_passport() -> None:
    draft = {
        "type": "document",
        "kind": "passport",
        "owner_relation": "жена",
        "owner_full_name": "Иванова Анна Петровна",
        "fields": {
            "series": "4514",
            "number": "123456",
            "full_name": "Иванова Анна Петровна",
            "birthday": "1985-05-12",
            "issued_at": "2020-05-12",
            "expires_at": "2030-05-12",
            "issued_by": "ОВД Москвы",
        },
        "tags": ["паспорт", "passport"],
        "suggested_title": "Паспорт жены",
        "files": [
            {"r2_key": "k1", "filename": "p1.jpg", "content_type": "image/jpeg"},
            {"r2_key": "k2", "filename": "p2.jpg", "content_type": "image/jpeg"},
        ],
    }
    text = cards.render_verification_card(draft)
    assert "<b>Паспорт жены</b>" in text
    assert "Серия: 4514" in text
    assert "Номер: 123456" in text
    assert "Иванова Анна Петровна" in text
    assert "Срок до:" in text
    assert "Файлы: 2 фото" in text
    assert "Сохранить?" in text


def test_render_verification_card_note() -> None:
    draft = {
        "type": "note",
        "fields": {"body": "Ключи от дачи в верхнем ящике"},
        "tags": ["дача"],
        "suggested_title": "Ключи от дачи",
        "files": [],
    }
    text = cards.render_verification_card(draft)
    assert "Ключи от дачи" in text
    assert "Файлы: —" in text


def test_render_record_card_document() -> None:
    rec = {
        "id": 17,
        "kind": "passport",
        "title": "Паспорт",
        "expires_at": "2030-05-12",
        "fields": {"series": "4514", "number": "123456"},
        "tags": ["паспорт"],
        "files": [{"r2_key": "k1", "filename": "x.pdf", "content_type": "application/pdf"}],
    }
    text = cards.render_record_card("document", rec)
    assert "Паспорт" in text
    assert "Срок до:" in text
    assert "/document_17" in text


def test_html_escape() -> None:
    draft = {
        "type": "person",
        "owner_full_name": "<script>",
        "fields": {},
        "tags": [],
    }
    text = cards.render_verification_card(draft)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
