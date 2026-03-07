from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import Entity, EventLog, Reminder
from modules.lifecycle import ARCHIVE_GRACE_DAYS, archive_expired_entities, get_stale_entities


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
    status: str = "active",
    end_date: date | None = None,
    updated_days_ago: int = 0,
) -> Entity:
    entity = Entity(type="certificate", name="Test", status=status, end_date=end_date)
    db.add(entity)
    await db.flush()

    if updated_days_ago:
        # Manually backdating updated_at via raw SQL for SQLite
        from sqlalchemy import text

        old_ts = datetime.now(tz=UTC) - timedelta(days=updated_days_ago)
        await db.execute(
            text("UPDATE entities SET updated_at = :ts WHERE id = :id"),
            {"ts": old_ts.replace(tzinfo=None), "id": entity.id},
        )

    await db.commit()
    await db.refresh(entity)
    return entity


# ── archive_expired_entities ─────────────────────────────────────────────────


async def test_archives_entity_past_grace_period(db: AsyncSession) -> None:
    past_date = date.today() - timedelta(days=ARCHIVE_GRACE_DAYS + 1)
    entity = await _make_entity(db, end_date=past_date)

    count = await archive_expired_entities(db)

    assert count == 1
    await db.refresh(entity)
    assert entity.status == "archived"


async def test_does_not_archive_within_grace_period(db: AsyncSession) -> None:
    recent_date = date.today() - timedelta(days=ARCHIVE_GRACE_DAYS - 1)
    await _make_entity(db, end_date=recent_date)

    count = await archive_expired_entities(db)

    assert count == 0


async def test_does_not_archive_entity_without_end_date(db: AsyncSession) -> None:
    await _make_entity(db, end_date=None)

    count = await archive_expired_entities(db)

    assert count == 0


async def test_archive_cancels_pending_reminders(db: AsyncSession) -> None:
    from sqlalchemy import select

    past_date = date.today() - timedelta(days=ARCHIVE_GRACE_DAYS + 1)
    entity = await _make_entity(db, end_date=past_date)

    reminder = Reminder(
        entity_id=entity.id,
        trigger_date=date.today() + timedelta(days=5),
        rule="before_N_days",
        status="pending",
    )
    db.add(reminder)
    await db.commit()

    await archive_expired_entities(db)

    result = await db.execute(select(Reminder).where(Reminder.id == reminder.id))
    updated = result.scalar_one()
    assert updated.status == "cancelled"


async def test_archive_writes_event_log(db: AsyncSession) -> None:
    from sqlalchemy import select

    past_date = date.today() - timedelta(days=ARCHIVE_GRACE_DAYS + 1)
    entity = await _make_entity(db, end_date=past_date)

    await archive_expired_entities(db)

    result = await db.execute(select(EventLog).where(EventLog.action == "entity_auto_archived"))
    log = result.scalar_one()
    assert log.entity_id == entity.id


async def test_skips_already_archived(db: AsyncSession) -> None:
    past_date = date.today() - timedelta(days=ARCHIVE_GRACE_DAYS + 1)
    await _make_entity(db, status="archived", end_date=past_date)

    count = await archive_expired_entities(db)

    assert count == 0


# ── get_stale_entities ───────────────────────────────────────────────────────


async def test_get_stale_returns_old_entities(db: AsyncSession) -> None:
    entity = await _make_entity(db, updated_days_ago=35)

    stale = await get_stale_entities(db, days=30)

    assert any(e.id == entity.id for e in stale)


async def test_get_stale_excludes_recently_updated(db: AsyncSession) -> None:
    await _make_entity(db, updated_days_ago=5)

    stale = await get_stale_entities(db, days=30)

    assert len(stale) == 0


async def test_get_stale_excludes_archived(db: AsyncSession) -> None:
    await _make_entity(db, status="archived", updated_days_ago=60)

    stale = await get_stale_entities(db, days=30)

    assert len(stale) == 0


# ── monthly_review job in scheduler ─────────────────────────────────────────


def test_scheduler_has_monthly_review_job() -> None:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "monthly_review" in job_ids


def test_monthly_review_runs_on_first_of_month() -> None:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    job = next(j for j in scheduler.get_jobs() if j.id == "monthly_review")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["day"] == "1"
    assert fields["hour"] == "10"
