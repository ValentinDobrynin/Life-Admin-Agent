"""Tests for file-based reference functions and parser helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from modules.parser import is_send_file_request
from modules.reference import (
    extract_reference_label,
    find_and_send_reference_file,
    get_reference_filename,
    is_reference_caption,
    parse_and_save_reference_from_file,
    save_reference_item,
)


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── is_reference_caption ──────────────────────────────────────────────────────


def test_is_reference_caption_empty() -> None:
    assert is_reference_caption("") is False


def test_is_reference_caption_trigger_only() -> None:
    assert is_reference_caption("справочник") is True
    assert is_reference_caption("в справочник") is True
    assert is_reference_caption("Справочник") is True


def test_is_reference_caption_with_label() -> None:
    assert is_reference_caption("справочник: мой загранпаспорт") is True
    assert is_reference_caption("в справочник: водительские права") is True
    assert is_reference_caption("сохрани в справочник: паспорт жены") is True


def test_is_reference_caption_unrelated() -> None:
    assert is_reference_caption("загранпаспорт истекает через 3 месяца") is False
    assert is_reference_caption("вот мой паспорт") is False
    assert is_reference_caption("просто фото") is False


# ── extract_reference_label ───────────────────────────────────────────────────


def test_extract_reference_label_with_label() -> None:
    assert extract_reference_label("справочник: мой загранпаспорт") == "мой загранпаспорт"
    assert extract_reference_label("в справочник: водительские права") == "водительские права"


def test_extract_reference_label_no_label() -> None:
    assert extract_reference_label("справочник") is None
    assert extract_reference_label("в справочник") is None


def test_extract_reference_label_preserves_case() -> None:
    result = extract_reference_label("справочник: Загранпаспорт РФ")
    assert result == "Загранпаспорт РФ"


# ── is_send_file_request ──────────────────────────────────────────────────────


def test_is_send_file_request_true() -> None:
    assert is_send_file_request("пришли права") is True
    assert is_send_file_request("скинь загранпаспорт") is True
    assert is_send_file_request("дай скан паспорта") is True
    assert is_send_file_request("дай фото прав") is True
    assert is_send_file_request("где мои права") is True
    assert is_send_file_request("где мой паспорт") is True


def test_is_send_file_request_false() -> None:
    assert is_send_file_request("покажи мне список задач") is False
    assert is_send_file_request("отправь уведомление") is False
    assert is_send_file_request("загранпаспорт истекает через 3 месяца") is False
    assert is_send_file_request("добавь в справочник") is False


# ── get_reference_filename ────────────────────────────────────────────────────


def test_get_reference_filename_with_path() -> None:
    assert get_reference_filename("reference/abc123.jpg") == "abc123.jpg"


def test_get_reference_filename_no_path() -> None:
    assert get_reference_filename("abc123.jpg") == "abc123.jpg"


# ── parse_and_save_reference_from_file ────────────────────────────────────────


async def test_parse_and_save_creates_document_entity(db: AsyncSession) -> None:
    """Document type → entity created, r2_key stored."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "document", "label": "Водительские права",'
        ' "data": {"doc_type": "rights"}, "end_date": null}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="OCR текст")),
        patch("modules.storage.upload_file", return_value="reference/abc.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, entity = await parse_and_save_reference_from_file(
            b"bytes", "rights.jpg", "image/jpeg", "справочник: Водительские права", db
        )

    assert ref_item.type == "document"
    assert ref_item.label == "Водительские права"
    assert ref_item.r2_key == "reference/abc.jpg"
    assert entity is not None
    assert entity.end_date is None


async def test_parse_and_save_with_end_date_creates_reminders(db: AsyncSession) -> None:
    """Document with end_date → entity and reminders created."""
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = (
        '{"type": "document", "label": "Загранпаспорт", "data": {}, "end_date": "2030-01-01"}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/pass.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, entity = await parse_and_save_reference_from_file(
            b"bytes", "pass.jpg", "image/jpeg", "справочник: загранпаспорт", db
        )

    assert entity is not None
    assert entity.end_date is not None

    from sqlalchemy import select as sa_select

    from models import Reminder

    reminders = (
        (await db.execute(sa_select(Reminder).where(Reminder.entity_id == entity.id)))
        .scalars()
        .all()
    )
    assert len(reminders) > 0


async def test_parse_and_save_car_no_entity(db: AsyncSession) -> None:
    """Car type → no entity created."""
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = (
        '{"type": "car", "label": "Тойота Камри", "data": {"plate": "А123БВ777"}, "end_date": null}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/car.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, entity = await parse_and_save_reference_from_file(
            b"bytes", "car.jpg", "image/jpeg", "справочник: моя машина", db
        )

    assert ref_item.type == "car"
    assert entity is None


async def test_parse_and_save_label_from_caption_takes_priority(db: AsyncSession) -> None:
    """Label from caption overrides OpenAI label."""
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = '{"type": "document", "label": "Паспорт", "data": {}, "end_date": null}'

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/x.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, _ = await parse_and_save_reference_from_file(
            b"bytes", "x.jpg", "image/jpeg", "справочник: мой загранпаспорт", db
        )

    assert ref_item.label == "мой загранпаспорт"


# ── find_and_send_reference_file ──────────────────────────────────────────────


async def test_find_and_send_no_items(db: AsyncSession) -> None:
    result = await find_and_send_reference_file("пришли права", db)
    assert result is None


async def test_find_and_send_no_files(db: AsyncSession) -> None:
    """Items without r2_key → returns None."""
    await save_reference_item("document", "Права", {}, db)
    result = await find_and_send_reference_file("пришли права", db)
    assert result is None


async def test_find_and_send_found(db: AsyncSession) -> None:
    item = await save_reference_item("document", "Водительские права", {}, db)
    # Manually set r2_key since save_reference_item doesn't accept it
    item.r2_key = "reference/rights.jpg"
    await db.commit()

    mock_response = MagicMock()
    mock_response.choices[0].message.content = str(item.id)

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await find_and_send_reference_file("пришли права", db)

    assert result == "reference/rights.jpg"
