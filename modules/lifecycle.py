from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, EventLog, Reminder

logger = logging.getLogger(__name__)

ARCHIVE_GRACE_DAYS = 7
STALE_DAYS = 30


async def archive_expired_entities(db: AsyncSession) -> int:
    """Archive entities whose end_date + grace period has passed. Returns count archived."""
    cutoff = date.today() - timedelta(days=ARCHIVE_GRACE_DAYS)

    result = await db.execute(
        select(Entity).where(
            and_(
                Entity.end_date <= cutoff,
                Entity.status.in_(["active", "expiring_soon", "expired"]),
            )
        )
    )
    entities = result.scalars().all()

    count = 0
    for entity in entities:
        entity.status = "archived"

        pending = await db.execute(
            select(Reminder).where(
                and_(Reminder.entity_id == entity.id, Reminder.status == "pending")
            )
        )
        for reminder in pending.scalars().all():
            reminder.status = "cancelled"

        log = EventLog(
            entity_id=entity.id,
            action="entity_auto_archived",
            payload={"end_date": entity.end_date.isoformat() if entity.end_date else None},
        )
        db.add(log)
        count += 1

    if count:
        await db.commit()
        logger.info("Auto-archived %d expired entities", count)

    return count


async def get_stale_entities(db: AsyncSession, days: int = STALE_DAYS) -> list[Entity]:
    """Return active entities not updated in N days (for monthly review)."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    result = await db.execute(
        select(Entity).where(
            and_(
                Entity.status.in_(["active", "paused"]),
                Entity.updated_at <= cutoff,
            )
        )
    )
    return list(result.scalars().all())


async def send_monthly_review(db: AsyncSession) -> None:
    """Send a monthly review message listing stale entities with action buttons."""
    from modules.notifications import send_message

    stale = await get_stale_entities(db)
    if not stale:
        return

    lines = [f"🗂 <b>Месячный обзор</b> — {len(stale)} записей не обновлялось >30 дней:\n"]
    for entity in stale[:10]:
        lines.append(f"  • {entity.name}")

    lines.append("\nОтправь «/archive_stale» чтобы архивировать всё сразу.")
    await send_message("\n".join(lines))
    logger.info("Monthly review sent: %d stale entities", len(stale))
