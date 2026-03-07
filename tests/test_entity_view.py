from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import ChecklistItem, Entity, Reminder
from modules.entity_view import (
    archive_entity,
    get_entity_card_text,
    get_status_text,
    pause_entity,
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


async def _make_entity(
    db: AsyncSession,
    name: str = "Test entity",
    type_: str = "certificate",
    status: str = "active",
    end_date: date | None = None,
    start_date: date | None = None,
    notes: str | None = None,
) -> Entity:
    entity = Entity(
        type=type_,
        name=name,
        status=status,
        end_date=end_date,
        start_date=start_date,
        notes=notes,
    )
    db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return entity


# ── get_status_text ───────────────────────────────────────────────────────────


async def test_status_empty_db(db: AsyncSession) -> None:
    text = await get_status_text(db)
    assert "Пока ничего не отслеживается" in text


async def test_status_shows_active_entities(db: AsyncSession) -> None:
    await _make_entity(db, name="Страховка", type_="document", status="active")
    await _make_entity(db, name="Отель Бали", type_="trip", status="active")

    text = await get_status_text(db)

    assert "Страховка" in text
    assert "Отель Бали" in text
    assert "Документы" in text
    assert "Поездки" in text


async def test_status_hides_archived(db: AsyncSession) -> None:
    await _make_entity(db, name="Старый полис", status="archived")
    text = await get_status_text(db)
    assert "Старый полис" not in text
    assert "Пока ничего" in text


async def test_status_expiring_soon_label(db: AsyncSession) -> None:
    soon = date.today() + timedelta(days=5)
    await _make_entity(db, name="Сертификат СПА", end_date=soon)

    text = await get_status_text(db)
    assert "⚡ через 5д" in text


async def test_status_far_date_shows_full_date(db: AsyncSession) -> None:
    far = date.today() + timedelta(days=30)
    await _make_entity(db, name="Загранпаспорт", end_date=far)

    text = await get_status_text(db)
    assert "до " in text
    assert "Загранпаспорт" in text


# ── get_entity_card_text ──────────────────────────────────────────────────────


async def test_card_not_found(db: AsyncSession) -> None:
    text = await get_entity_card_text(9999, db)
    assert "не найден" in text


async def test_card_basic_fields(db: AsyncSession) -> None:
    end = date.today() + timedelta(days=10)
    entity = await _make_entity(db, name="Полис ОСАГО", type_="document", end_date=end)

    text = await get_entity_card_text(entity.id, db)

    assert "Полис ОСАГО" in text
    assert f"#{entity.id}" in text
    assert "активно" in text
    assert "через 10д" in text


async def test_card_shows_checklist(db: AsyncSession) -> None:
    entity = await _make_entity(db, name="Поездка", type_="trip")
    db.add(ChecklistItem(entity_id=entity.id, text="Купить билеты", status="pending", position=1))
    db.add(
        ChecklistItem(entity_id=entity.id, text="Забронировать отель", status="done", position=2)
    )
    await db.commit()

    text = await get_entity_card_text(entity.id, db)

    assert "Чеклист" in text
    assert "☐ Купить билеты" in text
    assert "✅ Забронировать отель" in text


async def test_card_shows_reminders(db: AsyncSession) -> None:
    entity = await _make_entity(db, name="Подписка")
    reminder_date = date.today() + timedelta(days=7)
    db.add(
        Reminder(
            entity_id=entity.id,
            trigger_date=reminder_date,
            rule="before_N_days",
            channel="telegram",
            status="pending",
        )
    )
    await db.commit()

    text = await get_entity_card_text(entity.id, db)
    assert "Напоминания" in text
    assert reminder_date.strftime("%d.%m.%Y") in text


# ── archive_entity ────────────────────────────────────────────────────────────


async def test_archive_entity_sets_status(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    result = await archive_entity(entity.id, db)

    assert result is True
    await db.refresh(entity)
    assert entity.status == "archived"


async def test_archive_entity_cancels_reminders(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    db.add(
        Reminder(
            entity_id=entity.id,
            trigger_date=date.today() + timedelta(days=3),
            rule="before_N_days",
            channel="telegram",
            status="pending",
        )
    )
    await db.commit()

    await archive_entity(entity.id, db)

    from sqlalchemy import select

    result = await db.execute(select(Reminder).where(Reminder.entity_id == entity.id))
    reminder = result.scalar_one()
    assert reminder.status == "cancelled"


async def test_archive_entity_not_found(db: AsyncSession) -> None:
    result = await archive_entity(9999, db)
    assert result is False


# ── pause_entity ──────────────────────────────────────────────────────────────


async def test_pause_entity_sets_status(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    result = await pause_entity(entity.id, db)

    assert result is True
    await db.refresh(entity)
    assert entity.status == "paused"


async def test_pause_entity_not_found(db: AsyncSession) -> None:
    result = await pause_entity(9999, db)
    assert result is False
