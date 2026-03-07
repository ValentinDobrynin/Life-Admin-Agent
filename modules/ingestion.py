from __future__ import annotations

import base64
import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from models import ChecklistItem, Entity, EventLog, Reminder, Resource
from modules import notifications, parser

logger = logging.getLogger(__name__)


async def process_text(text: str, db: AsyncSession) -> Entity:
    """Main entry point: parse text, persist entity, send confirmation."""
    log = EventLog(action="raw_input", payload={"text": text})
    db.add(log)
    await db.flush()

    entity_data = await parser.extract_entity(text)

    entity = Entity(
        type=entity_data.type,
        name=entity_data.name,
        start_date=_parse_date(entity_data.start_date),
        end_date=_parse_date(entity_data.end_date),
        notes=entity_data.notes,
        status="active",
        priority="normal",
    )
    db.add(entity)
    await db.flush()

    for i, item in enumerate(entity_data.checklist_items):
        checklist_item = ChecklistItem(
            entity_id=entity.id,
            text=item.get("text", ""),
            position=item.get("position", i),
            status="open",
        )
        db.add(checklist_item)

    today = date.today()
    for rule_data in entity_data.reminder_rules:
        trigger_date = _compute_trigger_date(rule_data, entity, today)
        reminder = Reminder(
            entity_id=entity.id,
            rule=rule_data.get("rule", "digest_only"),
            trigger_date=trigger_date,
            status="pending",
            channel="telegram",
        )
        db.add(reminder)

    log.entity_id = entity.id
    await db.commit()
    await db.refresh(entity)

    logger.info("Created entity id=%d type=%s name=%.50s", entity.id, entity.type, entity.name)

    await notifications.send_confirmation(entity)
    return entity


async def process_edit(entity_id: int, text: str, db: AsyncSession) -> None:
    """Apply a natural-language edit instruction to an existing entity."""
    from sqlalchemy import select

    from models import Entity

    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()

    if entity is None:
        await notifications.send_message(f"❌ Запись #{entity_id} не найдена.")
        return

    context = (
        f"Существующая запись (обнови по инструкции пользователя):\n"
        f"Название: {entity.name}\n"
        f"Тип: {entity.type}\n"
        f"Начало: {entity.start_date or '—'}\n"
        f"Дедлайн: {entity.end_date or '—'}\n"
        f"Заметки: {entity.notes or '—'}\n\n"
        f"Инструкция пользователя: {text}"
    )

    entity_data = await parser.extract_entity(context)

    if entity_data.name and entity_data.name != "Новый объект":
        entity.name = entity_data.name
    if entity_data.type:
        entity.type = entity_data.type
    new_start = _parse_date(entity_data.start_date)
    new_end = _parse_date(entity_data.end_date)
    if new_start:
        entity.start_date = new_start
    if new_end:
        entity.end_date = new_end
    if entity_data.notes:
        entity.notes = entity_data.notes

    await db.commit()
    await db.refresh(entity)

    dates: list[str] = []
    if entity.start_date:
        dates.append(entity.start_date.strftime("%d.%m.%Y"))
    if entity.end_date:
        dates.append(f"до {entity.end_date.strftime('%d.%m.%Y')}")
    date_str = " · ".join(dates)

    await notifications.send_message(
        f"✅ Запись #{entity_id} обновлена.\n"
        f"<b>{entity.name}</b>" + (f"\n{date_str}" if date_str else "")
    )

    logger.info("Updated entity id=%d via edit instruction: %.80s", entity_id, text)


async def process_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    db: AsyncSession,
    entity_id: int | None = None,
) -> Entity | None:
    """Upload file to R2, optionally parse content, attach as Resource."""
    from modules import storage

    log = EventLog(
        action="raw_file_input",
        payload={"filename": filename, "mime_type": mime_type, "size": len(file_bytes)},
    )
    db.add(log)
    await db.flush()

    r2_key = storage.upload_file(file_bytes, filename, entity_id or 0)

    if entity_id is not None:
        resource = Resource(
            entity_id=entity_id,
            type="file",
            filename=filename,
            r2_key=r2_key,
        )
        db.add(resource)
        await db.commit()
        logger.info("Attached file %s to entity %d", filename, entity_id)
        return None

    # No entity_id → parse file contents via OpenAI Vision and create new entity
    raw_text = await _extract_text_from_file(file_bytes, filename, mime_type)
    if not raw_text:
        raw_text = f"Файл: {filename}"

    entity = await process_text(raw_text, db)

    # Attach file to the newly created entity
    resource = Resource(
        entity_id=entity.id,
        type="file",
        filename=filename,
        r2_key=r2_key,
    )
    db.add(resource)
    await db.commit()

    return entity


async def _extract_text_from_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
) -> str:
    """Extract text from PDF or image using pdfplumber / OpenAI Vision."""
    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        return _extract_pdf_text(file_bytes)

    if mime_type.startswith("image/"):
        return await _extract_image_text(file_bytes, mime_type)

    return ""


def _extract_pdf_text(file_bytes: bytes) -> str:
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages[:3]]
        return "\n".join(pages).strip()
    except Exception:
        logger.exception("PDF text extraction failed")
        return ""


async def _extract_image_text(file_bytes: bytes, mime_type: str) -> str:
    try:
        from openai import AsyncOpenAI

        from config import settings

        b64 = base64.b64encode(file_bytes).decode()
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Извлеки весь текст с изображения. "
                                "Верни только текст, без пояснений."
                            ),
                        },
                    ],
                }
            ],
            max_tokens=500,
        )
        return response.choices[0].message.content or ""
    except Exception:
        logger.exception("Image OCR via OpenAI Vision failed")
        return ""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        logger.warning("Could not parse date: %s", value)
        return None


def _compute_trigger_date(
    rule_data: dict[str, object],
    entity: Entity,
    today: date,
) -> date:
    rule = str(rule_data.get("rule", "digest_only"))

    if rule == "before_N_days":
        raw_days = rule_data.get("days", 7)
        days = int(raw_days) if isinstance(raw_days, (int, float, str)) else 7
        ref_date = entity.end_date or entity.start_date
        if ref_date:
            from datetime import timedelta

            return ref_date - timedelta(days=days)

    if rule == "on_date":
        ref_date = entity.start_date or entity.end_date
        if ref_date:
            return ref_date

    # digest_only and recurring_weekly — trigger tomorrow so scheduler picks up soon
    from datetime import timedelta

    return today + timedelta(days=1)
