from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-process dedup window for Telegram update_ids. Telegram retries delivery
# if our webhook doesn't return 200 fast enough — without this, a slow PDF
# pipeline ends up running multiple times in parallel and OOMs the instance.
_SEEN_CAPACITY = 1024
_seen_update_ids: deque[int] = deque(maxlen=_SEEN_CAPACITY)
_seen_set: set[int] = set()

_STARTUP_GREETING = (
    "🟢 <b>Снова на связи.</b>\n\n"
    "Хранилище подняли, очередь в порядке. Продолжаем складывать и доставать."
)

# Background tasks created from the webhook handler. We hold strong refs so
# the loop doesn't garbage-collect them mid-flight (asyncio.create_task only
# stores a weak reference).
_background_tasks: set[asyncio.Task[None]] = set()


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

    # Send a startup ping so the user knows the bot is back online after a
    # deploy / restart. Best-effort — never block startup if Telegram is down.
    try:
        from modules import notifications

        await notifications.send_text(settings.telegram_chat_id, _STARTUP_GREETING)
    except Exception:
        logger.exception("startup greeting failed")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(title="Life Admin Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/webhook/telegram")
async def webhook_telegram(request: Request) -> JSONResponse:
    """Acknowledge Telegram immediately and process the update in a background
    task. This guarantees we never block the webhook long enough to trigger
    Telegram's retry loop, which previously caused parallel processing and
    out-of-memory kills on heavy uploads (PDFs).
    """
    update: dict[str, Any] = await request.json()

    update_id = update.get("update_id")
    kind = _update_kind(update)
    logger.info("webhook update_id=%s kind=%s", update_id, kind)

    if isinstance(update_id, int):
        if update_id in _seen_set:
            logger.info("Skipping duplicate update_id=%s", update_id)
            return JSONResponse({"ok": True})
        if len(_seen_update_ids) == _SEEN_CAPACITY:
            evicted = _seen_update_ids[0]
            _seen_set.discard(evicted)
        _seen_update_ids.append(update_id)
        _seen_set.add(update_id)

    task = asyncio.create_task(_process_update_safe(update))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return JSONResponse({"ok": True})


def _update_kind(update: dict[str, Any]) -> str:
    """Return short tag for the update (callback/text/photo/document/etc)."""
    if "callback_query" in update:
        data = update["callback_query"].get("data") or ""
        return f"callback:{data}"
    msg = update.get("message") or update.get("edited_message") or {}
    if "photo" in msg:
        return "photo"
    if "document" in msg:
        return "document"
    if msg.get("text", "").startswith("/"):
        return f"command:{msg['text'].split()[0]}"
    if msg.get("text"):
        return "text"
    return "unknown"


async def _process_update_safe(update: dict[str, Any]) -> None:
    """Open a fresh DB session and run the bot handler. Swallow exceptions so
    the background task never bubbles up to the loop's default handler.
    """
    from bot.handlers import handle_update
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await handle_update(update, db)
    except Exception:
        logger.exception("background handle_update failed")
