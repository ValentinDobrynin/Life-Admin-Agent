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


def test_render_verification_card_uses_passport_type_in_title() -> None:
    """If suggested_title is missing, title should reflect passport_type."""
    draft_foreign = {
        "type": "document",
        "kind": "passport",
        "fields": {"number": "758911941", "passport_type": "foreign"},
        "tags": [],
        "files": [],
    }
    text = cards.render_verification_card(draft_foreign)
    assert "Загранпаспорт" in text

    draft_internal = {
        "type": "document",
        "kind": "passport",
        "fields": {"series": "4514", "number": "123456", "passport_type": "internal"},
        "tags": [],
        "files": [],
    }
    text2 = cards.render_verification_card(draft_internal)
    assert "Внутренний паспорт" in text2


def test_render_ticket_transport_card() -> None:
    draft = {
        "type": "document",
        "kind": "ticket",
        "fields": {
            "category": "transport",
            "subtype": "train",
            "carrier_or_venue": "РЖД / ДОСС",
            "from": "Москва",
            "to": "Санкт-Петербург",
            "departure_at": "2026-05-22T07:30",
            "arrival_at": "2026-05-22T11:45",
            "round_trip": True,
            "return_departure_at": "2026-05-24T21:00",
            "return_arrival_at": "2026-05-25T01:08",
            "train_number": "758",
            "order_number": "73564554567043",
            "price_total": 51424.60,
            "currency": "RUB",
            "passengers": [
                {"full_name": "Добрынин Валентин", "passport": "4505997513", "seat": "01/027"},
                {"full_name": "Добрынина Анастасия", "passport": "4525645062", "seat": "01/029"},
                {"full_name": "Раскоснов Максим", "passport": "4525581686", "seat": "01/030"},
            ],
        },
        "tags": ["билет", "ticket", "сапсан"],
        "suggested_title": "Сапсан · Москва ↔ СПб · 22–25.05.2026",
        "files": [{"r2_key": "k", "filename": "tickets.pdf", "content_type": "application/pdf"}],
    }
    text = cards.render_verification_card(draft)
    assert "🚆" in text
    assert "<b>Сапсан · Москва ↔ СПб · 22–25.05.2026</b>" in text
    assert "Откуда:" in text and "Москва" in text
    assert "Куда:" in text and "Санкт-Петербург" in text
    assert "Туда-обратно: да" in text
    assert "Пассажиры:" in text
    assert "Добрынин Валентин" in text
    assert "Раскоснов Максим" in text
    assert "Файлы: 1 PDF" in text


def test_render_ticket_event_fallback_title() -> None:
    """Event tickets without suggested_title get a built-in fallback."""
    draft = {
        "type": "document",
        "kind": "ticket",
        "fields": {
            "category": "event",
            "subtype": "concert",
            "carrier_or_venue": "Rammstein",
            "venue": "Лужники",
            "event_at": "2026-07-20T20:00",
            "passengers": [{"full_name": "Валентин", "seat": "A-12"}],
        },
        "tags": ["билет", "ticket", "концерт"],
        "files": [],
    }
    text = cards.render_verification_card(draft)
    assert "🎤" in text
    assert "Rammstein" in text
    assert "20.07.2026" in text
    assert "Пассажиры:" in text


def test_render_ticket_record_card_skips_top_level_dates() -> None:
    """Top-level 'Срок до'/'Выдан' не должны мешать билетам — даты и так в полях."""
    rec = {
        "id": 5,
        "kind": "ticket",
        "title": "Сапсан · МСК↔СПб",
        "expires_at": "2026-05-25",
        "fields": {
            "category": "transport",
            "subtype": "train",
            "from": "Москва",
            "to": "Санкт-Петербург",
            "departure_at": "2026-05-22T07:30",
            "passengers": [{"full_name": "Иванов"}],
        },
        "tags": ["билет"],
        "files": [],
    }
    text = cards.render_record_card("document", rec)
    assert "Срок до:" not in text
    assert "Отправление:" in text


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
