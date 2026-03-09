from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import ChecklistItem, Entity, Reminder, Resource
from modules.suggestions import (
    EnrichedReminder,
    enrich_reminder,
    ensure_trip_checklist,
    generate_trip_checklist,
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


def _make_entity(type: str = "certificate", name: str = "SPA", days: int = 10) -> Entity:
    end = date.today() + timedelta(days=days)
    return Entity(id=1, type=type, name=name, status="active", end_date=end)


def _make_reminder(entity_id: int = 1) -> Reminder:
    return Reminder(
        id=1,
        entity_id=entity_id,
        trigger_date=date.today(),
        rule="before_N_days",
        status="pending",
    )


def _make_openai_response(data: dict) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = json.dumps(data)
    return response


# ── enrich_reminder ──────────────────────────────────────────────────────────


@patch("modules.suggestions._client")
async def test_enrich_certificate_returns_shortlist(
    mock_client: MagicMock, db: AsyncSession
) -> None:
    entity = _make_entity(type="certificate", name="SPA сертификат", days=10)
    reminder = _make_reminder()
    db.add(entity)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "next_action": "Записаться на массаж",
                "shortlist": ["Записаться в эти выходные", "Подарить подруге"],
                "note": None,
            }
        )
    )

    result = await enrich_reminder(reminder, entity, db)

    assert isinstance(result, EnrichedReminder)
    assert result.next_action == "Записаться на массаж"
    assert len(result.shortlist) == 2


@patch("modules.suggestions._client")
async def test_enrich_gift_returns_ideas(mock_client: MagicMock, db: AsyncSession) -> None:
    entity = _make_entity(type="gift", name="Подарок Насте", days=15)
    reminder = _make_reminder()
    db.add(entity)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "next_action": "Выбрать подарок и оформить заказ",
                "shortlist": ["Книга", "Духи", "Сертификат"],
                "note": None,
            }
        )
    )

    result = await enrich_reminder(reminder, entity, db)

    assert result.entity.type == "gift"
    assert len(result.shortlist) == 3


@patch("modules.suggestions._client")
async def test_enrich_trip_shows_missing_checklist(
    mock_client: MagicMock, db: AsyncSession
) -> None:
    entity = _make_entity(type="trip", name="Поездка в Турцию", days=7)
    db.add(entity)
    await db.flush()

    item = ChecklistItem(entity_id=entity.id, text="Оформить страховку", position=0, status="open")
    db.add(item)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "next_action": "Проверить чеклист поездки",
                "shortlist": [],
                "note": None,
            }
        )
    )

    result = await enrich_reminder(reminder=_make_reminder(entity.id), entity=entity, db=db)

    assert "Оформить страховку" in result.missing_checklist


@patch("modules.suggestions._client")
async def test_enrich_includes_resources(mock_client: MagicMock, db: AsyncSession) -> None:
    entity = _make_entity(type="document", name="Страховка", days=12)
    db.add(entity)
    await db.flush()

    resource = Resource(entity_id=entity.id, type="file", filename="policy.pdf", r2_key="r2/1")
    db.add(resource)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {"next_action": "Продлить полис", "shortlist": [], "note": None}
        )
    )

    result = await enrich_reminder(reminder=_make_reminder(entity.id), entity=entity, db=db)

    assert len(result.resources) == 1
    assert result.resources[0].filename == "policy.pdf"


@patch("modules.suggestions._client")
async def test_enrich_note_on_urgent(mock_client: MagicMock, db: AsyncSession) -> None:
    entity = _make_entity(type="payment", name="Оплата штрафа", days=1)
    reminder = _make_reminder()
    db.add(entity)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {
                "next_action": "Оплатить немедленно",
                "shortlist": [],
                "note": "Срок истекает завтра!",
            }
        )
    )

    result = await enrich_reminder(reminder, entity, db)

    assert result.note == "Срок истекает завтра!"


@patch("modules.suggestions._client")
async def test_enrich_fallback_on_openai_error(mock_client: MagicMock, db: AsyncSession) -> None:
    entity = _make_entity(type="certificate", name="Ваучер", days=5)
    reminder = _make_reminder()
    db.add(entity)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))

    result = await enrich_reminder(reminder, entity, db)

    assert result.next_action != ""
    assert result.shortlist == []


# ── generate_trip_checklist ───────────────────────────────────────────────────


@patch("modules.suggestions._fetch_trip_context")
@patch("modules.suggestions._client")
async def test_generate_trip_checklist_no_entity(
    mock_client: MagicMock,
    mock_fetch: AsyncMock,
    db: AsyncSession,
) -> None:
    """No matching entity → returns items list and entity=None."""
    mock_fetch.return_value = {"weather": "жарко +35°C", "destination_notes": ""}
    response = MagicMock()
    response.choices[0].message.content = "загранпаспорт\nстраховка\nсолнцезащитный крем"
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    items, entity = await generate_trip_checklist(
        {
            "destination": "Дубай",
            "dates": "20-25 апреля",
            "trip_type": "отдых",
            "travelers": "один",
        },
        db,
    )

    assert len(items) == 3
    assert "загранпаспорт" in items
    assert entity is None


@patch("modules.suggestions._fetch_trip_context")
@patch("modules.suggestions._client")
async def test_generate_trip_checklist_saves_to_entity(
    mock_client: MagicMock,
    mock_fetch: AsyncMock,
    db: AsyncSession,
) -> None:
    """Matching trip entity → checklist items saved with [авто] prefix."""
    from sqlalchemy import select as sa_select

    entity = Entity(
        id=None,
        type="trip",
        name="Дубай",
        status="active",
        start_date=date.today() + timedelta(days=10),
    )
    db.add(entity)
    await db.flush()

    mock_fetch.return_value = {"weather": "ясно +35°C", "destination_notes": ""}
    response = MagicMock()
    response.choices[0].message.content = "загранпаспорт\nстраховка"
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    items, found_entity = await generate_trip_checklist(
        {"destination": "Дубай", "dates": "", "trip_type": "отдых", "travelers": "один"},
        db,
    )

    assert found_entity is not None
    assert found_entity.id == entity.id

    saved = await db.execute(sa_select(ChecklistItem).where(ChecklistItem.entity_id == entity.id))
    saved_items = saved.scalars().all()
    assert len(saved_items) == 2
    assert all(item.text.startswith("[авто]") for item in saved_items)


@patch("modules.suggestions._fetch_trip_context")
@patch("modules.suggestions._client")
async def test_generate_trip_checklist_replaces_old_auto_items(
    mock_client: MagicMock,
    mock_fetch: AsyncMock,
    db: AsyncSession,
) -> None:
    """Re-generation removes old [авто] items before adding new ones."""
    from sqlalchemy import select as sa_select

    entity = Entity(type="trip", name="Стамбул", status="active")
    db.add(entity)
    await db.flush()

    old_item = ChecklistItem(
        entity_id=entity.id, text="[авто] старый пункт", position=1000, status="open"
    )
    db.add(old_item)
    await db.commit()

    mock_fetch.return_value = {"weather": "прохладно", "destination_notes": ""}
    response = MagicMock()
    response.choices[0].message.content = "загранпаспорт\nстраховка\nтёплая куртка"
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    await generate_trip_checklist(
        {"destination": "Стамбул", "dates": "", "trip_type": "город", "travelers": "один"},
        db,
    )

    saved = await db.execute(sa_select(ChecklistItem).where(ChecklistItem.entity_id == entity.id))
    saved_items = saved.scalars().all()
    texts = [i.text for i in saved_items]
    assert "[авто] старый пункт" not in texts
    assert len([t for t in texts if t.startswith("[авто]")]) == 3


# ── ensure_trip_checklist ─────────────────────────────────────────────────────


@patch("modules.suggestions._fetch_trip_context")
@patch("modules.suggestions._client")
async def test_ensure_trip_checklist_generates_when_empty(
    mock_client: MagicMock,
    mock_fetch: AsyncMock,
    db: AsyncSession,
) -> None:
    """No checklist on entity → generates and returns True."""
    entity = Entity(type="trip", name="Анталья", status="active")
    db.add(entity)
    await db.commit()

    mock_fetch.return_value = {"weather": "жарко", "destination_notes": ""}
    response = MagicMock()
    response.choices[0].message.content = "загранпаспорт\nстраховка\nкупальник"
    mock_client.chat.completions.create = AsyncMock(return_value=response)

    result = await ensure_trip_checklist(entity, db)

    assert result is True


@patch("modules.suggestions._fetch_trip_context")
@patch("modules.suggestions._client")
async def test_ensure_trip_checklist_skips_when_exists(
    mock_client: MagicMock,
    mock_fetch: AsyncMock,
    db: AsyncSession,
) -> None:
    """Checklist already exists → returns False without regenerating."""
    entity = Entity(type="trip", name="Сочи", status="active")
    db.add(entity)
    await db.flush()

    existing = ChecklistItem(entity_id=entity.id, text="Взять полотенце", position=0, status="open")
    db.add(existing)
    await db.commit()

    result = await ensure_trip_checklist(entity, db)

    assert result is False
    mock_client.chat.completions.create.assert_not_called()


@patch("modules.suggestions._client")
async def test_enrich_document_without_openai_uses_default(
    mock_client: MagicMock, db: AsyncSession
) -> None:
    """document type with end_date still calls OpenAI (days_left is set)."""
    entity = Entity(id=10, type="document", name="Паспорт", status="active", end_date=None)
    db.add(entity)
    await db.commit()

    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_openai_response(
            {"next_action": "Проверить срок", "shortlist": [], "note": None}
        )
    )
    reminder = _make_reminder(entity.id)
    result = await enrich_reminder(reminder, entity, db)

    assert result.next_action != ""
