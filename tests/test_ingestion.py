from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import ChecklistItem, Entity, EventLog, Reminder
from modules.ingestion import (
    _compute_trigger_date,
    _extract_text_from_file,
    _parse_date,
    _pdf_first_page_as_image,
    process_text,
)


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _make_parser_result(
    type: str = "certificate",
    name: str = "SPA сертификат",
    end_date: str = "2026-06-30",
    checklist_items: list = [],
    reminder_rules: list = [],
) -> MagicMock:
    from modules.parser import EntityData

    return EntityData(
        type=type,
        name=name,
        start_date=None,
        end_date=end_date,
        notes=None,
        checklist_items=checklist_items,
        reminder_rules=reminder_rules,
    )


@patch("modules.ingestion.notifications.send_confirmation", new_callable=AsyncMock)
@patch("modules.ingestion.parser.extract_entity")
async def test_process_text_creates_entity(
    mock_extract: MagicMock,
    mock_notify: AsyncMock,
    db_session: AsyncSession,
) -> None:
    mock_extract.return_value = _make_parser_result()

    entity = await process_text("Сертификат на массаж, до 30 июня", db_session)

    assert entity.id is not None
    assert entity.type == "certificate"
    assert entity.name == "SPA сертификат"
    assert entity.status == "active"
    mock_notify.assert_called_once_with(entity)


@patch("modules.ingestion.notifications.send_confirmation", new_callable=AsyncMock)
@patch("modules.ingestion.parser.extract_entity")
async def test_process_text_creates_checklist(
    mock_extract: MagicMock,
    mock_notify: AsyncMock,
    db_session: AsyncSession,
) -> None:
    mock_extract.return_value = _make_parser_result(
        type="trip",
        name="Поездка в Турцию",
        checklist_items=[
            {"text": "Оформить страховку", "position": 0},
            {"text": "Забронировать трансфер", "position": 1},
        ],
    )

    entity = await process_text("Поездка в Турцию", db_session)

    from sqlalchemy import select

    result = await db_session.execute(
        select(ChecklistItem).where(ChecklistItem.entity_id == entity.id)
    )
    items = result.scalars().all()
    assert len(items) == 2
    assert items[0].text == "Оформить страховку"


@patch("modules.ingestion.notifications.send_confirmation", new_callable=AsyncMock)
@patch("modules.ingestion.parser.extract_entity")
async def test_process_text_creates_reminders(
    mock_extract: MagicMock,
    mock_notify: AsyncMock,
    db_session: AsyncSession,
) -> None:
    mock_extract.return_value = _make_parser_result(
        reminder_rules=[
            {"rule": "before_N_days", "days": 14},
            {"rule": "before_N_days", "days": 3},
        ]
    )

    entity = await process_text("Сертификат", db_session)

    from sqlalchemy import select

    result = await db_session.execute(select(Reminder).where(Reminder.entity_id == entity.id))
    reminders = result.scalars().all()
    assert len(reminders) == 2
    assert all(r.status == "pending" for r in reminders)


@patch("modules.ingestion.notifications.send_confirmation", new_callable=AsyncMock)
@patch("modules.ingestion.parser.extract_entity")
async def test_process_text_logs_raw_input(
    mock_extract: MagicMock,
    mock_notify: AsyncMock,
    db_session: AsyncSession,
) -> None:
    mock_extract.return_value = _make_parser_result()

    await process_text("тестовый ввод", db_session)

    from sqlalchemy import select

    result = await db_session.execute(select(EventLog).where(EventLog.action == "raw_input"))
    log = result.scalar_one()
    assert log.payload == {"text": "тестовый ввод"}


def test_parse_date_valid() -> None:
    assert _parse_date("2026-06-30") == date(2026, 6, 30)


def test_parse_date_none() -> None:
    assert _parse_date(None) is None


def test_parse_date_invalid() -> None:
    assert _parse_date("not-a-date") is None


def test_compute_trigger_date_before_n_days() -> None:
    entity = Entity(type="certificate", name="test", end_date=date(2026, 7, 1))
    rule = {"rule": "before_N_days", "days": 14}
    result = _compute_trigger_date(rule, entity, date.today())
    assert result == date(2026, 6, 17)


@patch("modules.ingestion._extract_image_text", new_callable=AsyncMock)
@patch("modules.ingestion._pdf_first_page_as_image")
@patch("modules.ingestion._extract_pdf_text")
async def test_extract_text_from_file_pdf_scan_uses_vision(
    mock_pdf_text: MagicMock,
    mock_to_image: MagicMock,
    mock_vision: AsyncMock,
) -> None:
    """When pdfplumber returns little text, Vision fallback is called."""
    mock_pdf_text.return_value = "мало"
    mock_to_image.return_value = b"fake-png-bytes"
    mock_vision.return_value = "Страховой полис ОСАГО серия ВВВ 123456"

    result = await _extract_text_from_file(b"fake-pdf", "doc.pdf", "application/pdf")

    mock_to_image.assert_called_once_with(b"fake-pdf")
    mock_vision.assert_called_once_with(b"fake-png-bytes", "image/png")
    assert result == "Страховой полис ОСАГО серия ВВВ 123456"


@patch("modules.ingestion._extract_image_text", new_callable=AsyncMock)
@patch("modules.ingestion._extract_pdf_text")
async def test_extract_text_from_file_searchable_pdf_skips_vision(
    mock_pdf_text: MagicMock,
    mock_vision: AsyncMock,
) -> None:
    """When pdfplumber returns enough text, Vision is NOT called."""
    mock_pdf_text.return_value = "x" * 200

    result = await _extract_text_from_file(b"fake-pdf", "doc.pdf", "application/pdf")

    mock_vision.assert_not_called()
    assert result == "x" * 200


def test_pdf_first_page_as_image_returns_bytes_on_invalid_input() -> None:
    """Graceful fallback on broken PDF bytes."""
    result = _pdf_first_page_as_image(b"not-a-pdf")
    assert result == b""


def test_compute_trigger_date_digest_only_is_tomorrow() -> None:
    from datetime import timedelta

    today = date.today()
    entity = Entity(type="logistics", name="test")
    rule = {"rule": "digest_only"}
    result = _compute_trigger_date(rule, entity, today)
    assert result == today + timedelta(days=1)
