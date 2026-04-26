from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from scheduler import setup_scheduler

    scheduler = setup_scheduler()
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    if settings.is_production and settings.render_url:
        try:
            from bot.client import set_webhook

            webhook_url = f"{settings.render_url}/webhook/telegram"
            await set_webhook(webhook_url)
            logger.info("Telegram webhook registered: %s", webhook_url)
        except Exception:
            logger.exception("Failed to register Telegram webhook")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(title="Life Admin Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/webhook/telegram")
async def webhook_telegram(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    from bot.handlers import handle_update

    update: dict[str, Any] = await request.json()
    try:
        await handle_update(update, db)
    except Exception:
        logger.exception("handle_update failed")
    return JSONResponse({"ok": True})
