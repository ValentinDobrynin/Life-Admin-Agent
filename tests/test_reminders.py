from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import Entity, EventLog, Reminder
from modules.reminders import (
    get_digest_reminders,
    get_due_reminders,
    is_urgent,
    mark_entity_done,
    mark_reminder_sent,
    snooze_reminder,
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


async def _make_entity(db: AsyncSession, **kwargs: object) -> Entity:
    entity = Entity(type="certificate", name="Test", status="active", **kwargs)  # type: ignore[arg-type]
    db.add(entity)
    await db.flush()
    return entity


async def _make_reminder(
    db: AsyncSession,
    entity: Entity,
    trigger_date: date,
    rule: str = "before_N_days",
    status: str = "pending",
) -> Reminder:
    reminder = Reminder(
        entity_id=entity.id,
        trigger_date=trigger_date,
        rule=rule,
        status=status,
        channel="telegram",
    )
    db.add(reminder)
    await db.flush()
    return reminder


# ── get_due_reminders ────────────────────────────────────────────────────────


async def test_get_due_reminders_returns_pending_today(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    today = date.today()
    reminder = await _make_reminder(db, entity, trigger_date=today)
    await db.commit()

    results = await get_due_reminders(db, today)
    assert any(r.id == reminder.id for r in results)


async def test_get_due_reminders_skips_future(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    future = date.today() + timedelta(days=5)
    await _make_reminder(db, entity, trigger_date=future)
    await db.commit()

    results = await get_due_reminders(db, date.today())
    assert len(results) == 0


async def test_get_due_reminders_skips_sent(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    today = date.today()
    await _make_reminder(db, entity, trigger_date=today, status="sent")
    await db.commit()

    results = await get_due_reminders(db, today)
    assert len(results) == 0


# ── get_digest_reminders ─────────────────────────────────────────────────────


async def test_get_digest_includes_this_week(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    in_3_days = date.today() + timedelta(days=3)
    reminder = await _make_reminder(db, entity, trigger_date=in_3_days, rule="digest_only")
    await db.commit()

    results = await get_digest_reminders(db)
    assert any(r.id == reminder.id for r in results)


async def test_get_digest_excludes_beyond_week(db: AsyncSession) -> None:
    entity = await _make_entity(db)
    far_future = date.today() + timedelta(days=14)
    await _make_reminder(db, entity, trigger_date=far_future, rule="digest_only")
    await db.commit()

    results = await get_digest_reminders(db)
    assert len(results) == 0


# ── snooze_reminder ──────────────────────────────────────────────────────────


async def test_snooze_creates_new_pending_reminder(db: AsyncSession) -> None:
    from sqlalchemy import select

    entity = await _make_entity(db)
    today = date.today()
    reminder = await _make_reminder(db, entity, trigger_date=today)
    await db.commit()

    await snooze_reminder(reminder.id, 7, db)

    result = await db.execute(
        select(Reminder).where(Reminder.entity_id == entity.id, Reminder.status == "pending")
    )
    new_reminders = result.scalars().all()
    assert len(new_reminders) == 1
    assert new_reminders[0].trigger_date == today + timedelta(days=7)


async def test_snooze_marks_original_as_snoozed(db: AsyncSession) -> None:
    from sqlalchemy import select

    entity = await _make_entity(db)
    reminder = await _make_reminder(db, entity, trigger_date=date.today())
    await db.commit()

    await snooze_reminder(reminder.id, 3, db)

    result = await db.execute(select(Reminder).where(Reminder.id == reminder.id))
    updated = result.scalar_one()
    assert updated.status == "snoozed"
    assert updated.snoozed_until == date.today() + timedelta(days=3)


async def test_snooze_writes_event_log(db: AsyncSession) -> None:
    from sqlalchemy import select

    entity = await _make_entity(db)
    reminder = await _make_reminder(db, entity, trigger_date=date.today())
    await db.commit()

    await snooze_reminder(reminder.id, 7, db)

    result = await db.execute(select(EventLog).where(EventLog.action == "reminder_snoozed"))
    log = result.scalar_one()
    assert log.payload["reminder_id"] == reminder.id
    assert log.payload["days"] == 7


async def test_snooze_nonexistent_returns_none(db: AsyncSession) -> None:
    result = await snooze_reminder(99999, 7, db)
    assert result is None


# ── mark_reminder_sent ───────────────────────────────────────────────────────


async def test_mark_reminder_sent_updates_status(db: AsyncSession) -> None:
    from sqlalchemy import select

    entity = await _make_entity(db)
    reminder = await _make_reminder(db, entity, trigger_date=date.today())
    await db.commit()

    await mark_reminder_sent(reminder.id, db)

    result = await db.execute(select(Reminder).where(Reminder.id == reminder.id))
    updated = result.scalar_one()
    assert updated.status == "sent"


# ── mark_entity_done ─────────────────────────────────────────────────────────


async def test_mark_entity_done_closes_entity(db: AsyncSession) -> None:
    from sqlalchemy import select

    entity = await _make_entity(db)
    await db.commit()

    await mark_entity_done(entity.id, db)

    result = await db.execute(select(Entity).where(Entity.id == entity.id))
    updated = result.scalar_one()
    assert updated.status == "closed"


async def test_mark_entity_done_cancels_pending_reminders(db: AsyncSession) -> None:
    from sqlalchemy import select

    entity = await _make_entity(db)
    await _make_reminder(db, entity, trigger_date=date.today())
    await _make_reminder(db, entity, trigger_date=date.today() + timedelta(days=5))
    await db.commit()

    await mark_entity_done(entity.id, db)

    result = await db.execute(select(Reminder).where(Reminder.entity_id == entity.id))
    all_reminders = result.scalars().all()
    assert all(r.status == "cancelled" for r in all_reminders)


# ── is_urgent ────────────────────────────────────────────────────────────────


def test_is_urgent_today() -> None:
    today = date.today()
    reminder = Reminder(entity_id=1, trigger_date=today, rule="before_N_days", status="pending")
    assert is_urgent(reminder, today) is True


def test_is_urgent_tomorrow() -> None:
    today = date.today()
    reminder = Reminder(
        entity_id=1, trigger_date=today + timedelta(days=1), rule="before_N_days", status="pending"
    )
    assert is_urgent(reminder, today) is True


def test_is_urgent_in_5_days_is_false() -> None:
    today = date.today()
    reminder = Reminder(
        entity_id=1,
        trigger_date=today + timedelta(days=5),
        rule="before_N_days",
        status="pending",
    )
    assert is_urgent(reminder, today) is False
