from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from modules.parser import _fallback_entity, extract_entity


def _make_openai_response(data: dict) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = json.dumps(data)
    return response


@patch("modules.parser._client")
async def test_extract_entity_certificate(mock_client: MagicMock) -> None:
    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "type": "certificate",
                "name": "SPA сертификат",
                "start_date": None,
                "end_date": "2026-06-30",
                "notes": None,
                "checklist_items": [],
                "reminder_rules": [
                    {"rule": "before_N_days", "days": 14},
                    {"rule": "before_N_days", "days": 3},
                ],
            }
        )
    )

    result = await extract_entity("Сертификат на массаж, до 30 июня")

    assert result.type == "certificate"
    assert result.name == "SPA сертификат"
    assert result.end_date == "2026-06-30"
    assert len(result.reminder_rules) == 2


@patch("modules.parser._client")
async def test_extract_entity_trip(mock_client: MagicMock) -> None:
    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "type": "trip",
                "name": "Поездка в Турцию",
                "start_date": "2026-05-12",
                "end_date": "2026-05-16",
                "notes": None,
                "checklist_items": [
                    {"text": "Проверить документы", "position": 0},
                    {"text": "Оформить страховку", "position": 1},
                ],
                "reminder_rules": [{"rule": "before_N_days", "days": 7}],
            }
        )
    )

    result = await extract_entity("Поездка в Турцию 12-16 мая")

    assert result.type == "trip"
    assert result.start_date == "2026-05-12"
    assert len(result.checklist_items) == 2
    assert result.checklist_items[0]["text"] == "Проверить документы"


@patch("modules.parser._client")
async def test_extract_entity_document(mock_client: MagicMock) -> None:
    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "type": "document",
                "name": "Страховка авто",
                "start_date": None,
                "end_date": "2026-08-01",
                "notes": "Ингосстрах",
                "checklist_items": [],
                "reminder_rules": [
                    {"rule": "before_N_days", "days": 30},
                    {"rule": "before_N_days", "days": 14},
                    {"rule": "before_N_days", "days": 7},
                ],
            }
        )
    )

    result = await extract_entity("Страховка авто истекает 1 августа")

    assert result.type == "document"
    assert result.end_date == "2026-08-01"
    assert len(result.reminder_rules) == 3


@patch("modules.parser._client")
async def test_fallback_on_openai_error(mock_client: MagicMock) -> None:
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API error"))

    result = await extract_entity("Какой-то текст")

    assert result.type == "logistics"
    assert result.reminder_rules == [{"rule": "digest_only"}]


def test_fallback_entity_truncates_long_text() -> None:
    long_text = "а" * 200
    result = _fallback_entity(long_text)
    assert len(result.name) <= 100
    assert result.notes == long_text


@patch("modules.parser._client")
async def test_extract_entity_with_invalid_json_falls_back(mock_client: MagicMock) -> None:
    response = MagicMock()
    response.choices[0].message.content = "not valid json {"
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    result = await extract_entity("Тест")

    assert result.type == "logistics"


@patch("modules.parser._client")
async def test_extract_entity_missing_fields_use_defaults(mock_client: MagicMock) -> None:
    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response({"type": "payment"})
    )

    result = await extract_entity("Заплатить за квартиру")

    assert result.type == "payment"
    assert result.name == "Новый объект"
    assert result.checklist_items == []
    assert result.reminder_rules == []
