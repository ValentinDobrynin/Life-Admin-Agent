"""High-level Telegram notifications: text + files + expiry digest."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import client
from config import settings
from models import Document, Person
from modules import storage

logger = logging.getLogger(__name__)


async def send_text(
    chat_id: int,
    text: str,
    keyboard: dict[str, Any] | None = None,
) -> None:
    try:
        await client.send_message(chat_id, text, reply_markup=keyboard)
    except Exception:
        logger.exception("send_text failed for chat=%s", chat_id)


async def send_files(
    chat_id: int,
    files: list[dict[str, Any]],
    caption: str | None = None,
) -> None:
    """Send saved files as a media-group (photos) or one-by-one (PDF/etc)."""
    if not files:
        return

    photos: list[tuple[bytes, str]] = []
    others: list[dict[str, Any]] = []
    for f in files:
        ct = (f.get("content_type") or "").lower()
        if ct.startswith("image/"):
            try:
                data = storage.download_file(f["r2_key"])
            except Exception:
                logger.exception("download failed for %s", f.get("r2_key"))
                continue
            photos.append((data, f.get("filename") or "photo.jpg"))
        else:
            others.append(f)

    if len(photos) >= 2:
        await client.send_media_group(chat_id, photos, caption=caption)
    elif len(photos) == 1:
        data, name = photos[0]
        await client.send_photo(chat_id, data, filename=name, caption=caption)

    for f in others:
        try:
            data = storage.download_file(f["r2_key"])
        except Exception:
            logger.exception("download failed for %s", f.get("r2_key"))
            continue
        await client.send_document(
            chat_id,
            data,
            filename=f.get("filename") or "file.bin",
            caption=caption if not photos else None,
        )


async def send_expiry_digest(db: AsyncSession) -> None:
    """Send the daily morning digest of upcoming document expirations."""
    today = date.today()
    deadline = today + timedelta(days=settings.expiry_window_days)
    result = await db.execute(
        select(Document)
        .where(
            Document.status == "active",
            Document.expires_at.is_not(None),
            Document.expires_at >= today,
            Document.expires_at <= deadline,
        )
        .order_by(Document.expires_at)
    )
    docs = result.scalars().all()
    if not docs:
        return

    person_ids = {d.owner_person_id for d in docs if d.owner_person_id is not None}
    persons: dict[int, str] = {}
    if person_ids:
        person_rows = await db.execute(
            select(Person.id, Person.full_name).where(Person.id.in_(person_ids))
        )
        for pid, name in person_rows.all():
            persons[pid] = name

    lines = [f"⏰ <b>Скоро истекают</b> ({len(docs)})", ""]
    button_rows: list[list[dict[str, str]]] = []
    for d in docs:
        owner = persons.get(d.owner_person_id) if d.owner_person_id else None
        days_left = (d.expires_at - today).days if d.expires_at else None
        when = d.expires_at.strftime("%d.%m.%Y") if d.expires_at else "—"
        owner_part = f" · {owner}" if owner else ""
        days_part = f" ({days_left} дн)" if days_left is not None else ""
        lines.append(f"• <b>{d.title}</b>{owner_part} — {when}{days_part}")
        button_rows.append(
            [
                {
                    "text": f"📨 Прислать «{d.title[:40]}»",
                    "callback_data": f"send_doc_{d.id}",
                }
            ]
        )

    keyboard = client.make_inline_keyboard(button_rows)
    await send_text(settings.telegram_chat_id, "\n".join(lines), keyboard=keyboard)
