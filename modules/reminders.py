from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, EventLog, Reminder

logger = logging.getLogger(__name__)

URGENT_DAYS = 2  # reminders within this many days get a point push, rest go to digest


async def get_due_reminders(db: AsyncSession, today: date | None = None) -> list[Reminder]:
    """Return all pending reminders whose trigger_date is today or earlier."""
    today = today or date.today()
    result = await db.execute(
        select(Reminder).where(
            and_(
                Reminder.trigger_date <= today,
                Reminder.status == "pending",
            )
        )
    )
    return list(result.scalars().all())


async def get_digest_reminders(db: AsyncSession, today: date | None = None) -> list[Reminder]:
    """Return reminders suitable for the daily digest (rule=digest_only or due this week)."""
    today = today or date.today()
    week_ahead = today + timedelta(days=7)
    result = await db.execute(
        select(Reminder).where(
            and_(
                Reminder.status == "pending",
                Reminder.trigger_date <= week_ahead,
            )
        )
    )
    return list(result.scalars().all())


async def snooze_reminder(reminder_id: int, days: int, db: AsyncSession) -> Reminder | None:
    """Snooze a reminder by N days. Writes to event_log."""
    result = await db.execute(select(Reminder).where(Reminder.id == reminder_id))
    reminder = result.scalar_one_or_none()
    if not reminder:
        return None

    snooze_until = date.today() + timedelta(days=days)
    reminder.status = "snoozed"
    reminder.snoozed_until = snooze_until

    # Create a new pending reminder for the snoozed date
    new_reminder = Reminder(
        entity_id=reminder.entity_id,
        trigger_date=snooze_until,
        rule=reminder.rule,
        channel=reminder.channel,
        text=reminder.text,
        status="pending",
    )
    db.add(new_reminder)

    log = EventLog(
        entity_id=reminder.entity_id,
        action="reminder_snoozed",
        payload={"reminder_id": reminder_id, "days": days, "until": snooze_until.isoformat()},
    )
    db.add(log)
    await db.commit()
    logger.info("Reminder %d snoozed for %d days until %s", reminder_id, days, snooze_until)
    return reminder


async def mark_reminder_sent(reminder_id: int, db: AsyncSession) -> None:
    """Mark reminder as sent and write to event_log."""
    result = await db.execute(select(Reminder).where(Reminder.id == reminder_id))
    reminder = result.scalar_one_or_none()
    if not reminder:
        return

    reminder.status = "sent"
    log = EventLog(
        entity_id=reminder.entity_id,
        action="reminder_sent",
        payload={"reminder_id": reminder_id, "rule": reminder.rule},
    )
    db.add(log)
    await db.commit()


async def mark_entity_done(entity_id: int, db: AsyncSession) -> None:
    """Close entity and cancel all its pending reminders."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        return

    entity.status = "closed"

    pending = await db.execute(
        select(Reminder).where(and_(Reminder.entity_id == entity_id, Reminder.status == "pending"))
    )
    for reminder in pending.scalars().all():
        reminder.status = "cancelled"

    log = EventLog(
        entity_id=entity_id,
        action="entity_closed",
        payload={"entity_id": entity_id},
    )
    db.add(log)
    await db.commit()
    logger.info("Entity %d closed, reminders cancelled", entity_id)


async def is_already_reminded_today(reminder_id: int, db: AsyncSession) -> bool:
    """Guard against double-sending: check if this reminder was sent today."""
    today_str = date.today().isoformat()
    result = await db.execute(
        select(EventLog).where(
            and_(
                EventLog.action == "reminder_sent",
                EventLog.payload["reminder_id"].as_integer() == reminder_id,
            )
        )
    )
    logs = result.scalars().all()
    for log in logs:
        if log.created_at and log.created_at.date().isoformat() == today_str:
            return True
    return False


def is_urgent(reminder: Reminder, today: date | None = None) -> bool:
    """Urgent reminders get a point push; non-urgent go to digest."""
    today = today or date.today()
    delta = (reminder.trigger_date - today).days
    return delta <= URGENT_DAYS
