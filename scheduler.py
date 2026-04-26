"""APScheduler setup. Single job: morning expiry digest."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from database import AsyncSessionLocal
from modules import notifications

logger = logging.getLogger(__name__)


async def expiry_check_job() -> None:
    """Daily 09:00 in `settings.timezone` — send digest of upcoming expirations."""
    async with AsyncSessionLocal() as db:
        try:
            await notifications.send_expiry_digest(db)
        except Exception:
            logger.exception("expiry_check_job failed")


def setup_scheduler() -> AsyncIOScheduler:
    tz = ZoneInfo(settings.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        expiry_check_job,
        trigger="cron",
        hour=9,
        minute=0,
        id="expiry_check",
        replace_existing=True,
    )
    return scheduler
