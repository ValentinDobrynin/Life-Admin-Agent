from __future__ import annotations

import logging
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import AsyncSessionLocal

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler(timezone=settings.timezone)


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


async def check_reminders_job() -> None:
    """Daily 09:00: send point-push for urgent reminders (due within 48h)."""
    from sqlalchemy import select

    from models import Entity
    from modules import notifications
    from modules.reminders import (
        get_due_reminders,
        is_already_reminded_today,
        is_urgent,
        mark_reminder_sent,
    )

    logger.info("check_reminders_job: starting")
    async with AsyncSessionLocal() as db:
        today = date.today()
        reminders = await get_due_reminders(db, today)

        sent_count = 0
        for reminder in reminders:
            if not is_urgent(reminder, today):
                continue
            if reminder.rule == "digest_only":
                continue
            if await is_already_reminded_today(reminder.id, db):
                logger.debug("Reminder %d already sent today, skipping", reminder.id)
                continue

            result = await db.execute(select(Entity).where(Entity.id == reminder.entity_id))
            entity = result.scalar_one_or_none()
            if entity is None or entity.status in ("archived", "closed"):
                continue

            from modules.suggestions import enrich_reminder

            enriched = await enrich_reminder(reminder, entity, db)
            await notifications.send_enriched_reminder(enriched)
            await mark_reminder_sent(reminder.id, db)
            sent_count += 1

    logger.info("check_reminders_job: sent %d reminders", sent_count)


async def send_digest_job() -> None:
    """Daily 09:05: send digest of everything due this week."""
    from sqlalchemy import select

    from models import Entity
    from modules import notifications
    from modules.reminders import get_digest_reminders, is_urgent

    logger.info("send_digest_job: starting")
    async with AsyncSessionLocal() as db:
        today = date.today()
        reminders = await get_digest_reminders(db, today)

        items: list[tuple[object, object]] = []
        for reminder in reminders:
            if is_urgent(reminder, today):
                continue  # urgent ones are handled by check_reminders_job

            result = await db.execute(select(Entity).where(Entity.id == reminder.entity_id))
            entity = result.scalar_one_or_none()
            if entity is None or entity.status in ("archived", "closed"):
                continue

            items.append((reminder, entity))

        await notifications.send_digest(items)  # type: ignore[arg-type]

        from modules.reminders import mark_reminder_sent

        for sent_reminder, _ in items:
            await mark_reminder_sent(sent_reminder.id, db)  # type: ignore[attr-defined]

    logger.info("send_digest_job: digest sent with %d items", len(items))


async def lifecycle_check_job() -> None:
    """Weekly Monday 10:00: archive expired entities."""
    from modules.lifecycle import archive_expired_entities

    logger.info("lifecycle_check_job: starting")
    async with AsyncSessionLocal() as db:
        count = await archive_expired_entities(db)
    logger.info("lifecycle_check_job: archived %d entities", count)


async def monthly_review_job() -> None:
    """Monthly 1st day 10:00: send list of stale entities."""
    from modules.lifecycle import send_monthly_review

    logger.info("monthly_review_job: starting")
    async with AsyncSessionLocal() as db:
        await send_monthly_review(db)
    logger.info("monthly_review_job: done")


def setup_scheduler() -> AsyncIOScheduler:
    """Register all jobs and return the scheduler (not yet started)."""
    tz = settings.timezone

    _scheduler.add_job(
        check_reminders_job,
        CronTrigger(hour=9, minute=0, timezone=tz),
        id="check_reminders",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        send_digest_job,
        CronTrigger(hour=9, minute=5, timezone=tz),
        id="send_digest",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        lifecycle_check_job,
        CronTrigger(day_of_week="mon", hour=10, minute=0, timezone=tz),
        id="lifecycle_check",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    _scheduler.add_job(
        monthly_review_job,
        CronTrigger(day=1, hour=10, minute=0, timezone=tz),
        id="monthly_review",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # ВРЕМЕННЫЙ ТЕСТ — удалить после проверки
    if settings.scheduler_test_mode:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        test_time = datetime.now(tz=ZoneInfo(settings.timezone)) + timedelta(minutes=2)
        _scheduler.add_job(
            check_reminders_job,
            "date",
            run_date=test_time,
            id="test_check_reminders",
            replace_existing=True,
        )
        _scheduler.add_job(
            send_digest_job,
            "date",
            run_date=test_time + timedelta(seconds=10),
            id="test_send_digest",
            replace_existing=True,
        )
        logger.info(
            "TEST MODE: check_reminders and send_digest scheduled at %s",
            test_time.strftime("%H:%M:%S"),
        )

    return _scheduler
