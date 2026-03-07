from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from modules import ingestion, notifications

logger = logging.getLogger(__name__)

# Single-user MVP: stores entity_id waiting for edit text from the user.
_pending_edit_entity_id: int | None = None

START_MESSAGE = (
    "👋 Привет! Я Life Admin Agent.\n\n"
    "Просто напиши мне что нужно запомнить:\n"
    "— <i>Сертификат на массаж, до 30 июня</i>\n"
    "— <i>День рождения Насти 15 мая</i>\n"
    "— <i>Страховка авто истекает 1 августа</i>\n\n"
    "Я сохраню, создам напоминания и напомню вовремя."
)


async def handle_update(update: dict[str, Any], db: AsyncSession = Depends(get_db)) -> None:
    """Route incoming Telegram update to the appropriate handler."""
    if "message" in update:
        await _handle_message(update["message"], db)
    elif "callback_query" in update:
        await _handle_callback(update["callback_query"], db)


async def _handle_message(message: dict[str, Any], db: AsyncSession) -> None:
    global _pending_edit_entity_id

    text = message.get("text", "")

    if text == "/start":
        await notifications.send_message(START_MESSAGE)
        return

    if text == "/status":
        from modules.entity_view import get_status_text

        status_text = await get_status_text(db)
        await notifications.send_message(status_text)
        return

    if text.startswith("/entity"):
        parts = text.strip().split()
        if len(parts) < 2 or not parts[1].isdigit():
            await notifications.send_message(
                "Использование: /entity &lt;id&gt;\nНапример: /entity 42"
            )
            return
        entity_id = int(parts[1])
        from modules.entity_view import get_entity_card_text, make_entity_card_buttons

        card_text = await get_entity_card_text(entity_id, db)
        buttons = make_entity_card_buttons(entity_id)
        await notifications.send_message(card_text, buttons=buttons)
        return

    if text == "/profile":
        from modules.reference import get_profile_text

        await notifications.send_message(await get_profile_text(db))
        return

    if text.startswith("/ref"):
        parts = text.strip().split()
        if len(parts) < 2 or not parts[1].isdigit():
            await notifications.send_message("Использование: /ref &lt;id&gt;\nНапример: /ref 3")
            return
        ref_id = int(parts[1])
        from modules.reference import get_ref_card_text

        await notifications.send_message(await get_ref_card_text(ref_id, db))
        return

    if text and _pending_edit_entity_id is not None:
        entity_id = _pending_edit_entity_id
        _pending_edit_entity_id = None
        await ingestion.process_edit(entity_id, text, db)
        return

    if text:
        from modules.parser import detect_intent, is_send_file_request

        if is_send_file_request(text):
            from bot import client as tg
            from modules.reference import find_and_send_reference_file, get_reference_filename
            from modules.storage import download_file

            r2_key = await find_and_send_reference_file(text, db)
            if r2_key:
                try:
                    file_bytes = download_file(r2_key)
                    await tg.send_document(
                        chat_id=settings.telegram_chat_id,
                        file_bytes=file_bytes,
                        filename=get_reference_filename(r2_key),
                    )
                except Exception:
                    logger.exception("Failed to send reference file")
                    await notifications.send_message("❌ Не удалось отправить файл.")
            else:
                await notifications.send_message(
                    "❌ Не нашёл подходящий документ в справочнике.\n"
                    "Проверь /profile — возможно файл не прикреплён."
                )
            return

        intent = detect_intent(text)

        if intent == "generate":
            from modules.reference import generate_text

            result = await generate_text(text, db)
            await notifications.send_message(result)
            return

        if intent == "reference_add":
            from modules.reference import parse_and_save_reference

            item = await parse_and_save_reference(text, db)
            if item:
                await notifications.send_message(
                    f"🗂 Сохранил в справочник: <b>{item.label}</b>\n"
                    f"Тип: {item.type} · #{item.id}\n\n"
                    f"/profile — посмотреть весь справочник"
                )
            else:
                await notifications.send_message(
                    "❌ Не удалось распознать данные. Попробуй написать подробнее."
                )
            return

        await ingestion.process_text(text, db)
        return

    if "photo" in message:
        await _handle_photo(message, db)
        return

    if "document" in message:
        await _handle_document(message, db)
        return

    logger.debug("Unhandled message type: %s", list(message.keys()))


async def _handle_photo(message: dict[str, Any], db: AsyncSession) -> None:
    from bot import client as tg

    photos: list[dict[str, Any]] = message["photo"]
    largest = max(photos, key=lambda p: p.get("file_size", 0))
    file_id = largest["file_id"]

    file_info = await tg.get_file(file_id)
    file_path = file_info.get("result", {}).get("file_path", "")
    if not file_path:
        await notifications.send_message("❌ Не удалось получить файл от Telegram.")
        return

    file_bytes = await tg.download_file(file_path)
    filename = f"{file_id}.jpg"

    caption: str = message.get("caption", "")

    from modules.reference import is_reference_caption, parse_and_save_reference_from_file

    if is_reference_caption(caption):
        ref_item, entity = await parse_and_save_reference_from_file(
            file_bytes, filename, "image/jpeg", caption, db
        )
        msg = f"🗂 Сохранил в справочник: <b>{ref_item.label}</b> · #{ref_item.id}"
        if entity and entity.end_date:
            msg += f"\n📅 Напомню до {entity.end_date.strftime('%d.%m.%Y')} · /entity {entity.id}"
        elif entity:
            msg += f"\n📋 Создал запись без срока · /entity {entity.id}"
        await notifications.send_message(msg)
        return

    entity_id: int | None = None
    if caption and caption.startswith("#"):
        try:
            entity_id = int(caption.strip().lstrip("#"))
        except ValueError:
            pass

    await ingestion.process_file(file_bytes, filename, "image/jpeg", db, entity_id)
    await notifications.send_message("📎 Файл сохранён.")


async def _handle_document(message: dict[str, Any], db: AsyncSession) -> None:
    from bot import client as tg

    doc: dict[str, Any] = message["document"]
    file_id = doc["file_id"]
    filename: str = doc.get("file_name", f"{file_id}.bin")
    mime_type: str = doc.get("mime_type", "application/octet-stream")

    file_info = await tg.get_file(file_id)
    file_path = file_info.get("result", {}).get("file_path", "")
    if not file_path:
        await notifications.send_message("❌ Не удалось получить файл от Telegram.")
        return

    file_bytes = await tg.download_file(file_path)

    caption: str = message.get("caption", "")

    from modules.reference import is_reference_caption, parse_and_save_reference_from_file

    if is_reference_caption(caption):
        ref_item, entity = await parse_and_save_reference_from_file(
            file_bytes, filename, mime_type, caption, db
        )
        msg = f"🗂 Сохранил в справочник: <b>{ref_item.label}</b> · #{ref_item.id}"
        if entity and entity.end_date:
            msg += f"\n📅 Напомню до {entity.end_date.strftime('%d.%m.%Y')} · /entity {entity.id}"
        elif entity:
            msg += f"\n📋 Создал запись без срока · /entity {entity.id}"
        await notifications.send_message(msg)
        return

    entity_id: int | None = None
    if caption and caption.startswith("#"):
        try:
            entity_id = int(caption.strip().lstrip("#"))
        except ValueError:
            pass

    await ingestion.process_file(file_bytes, filename, mime_type, db, entity_id)
    await notifications.send_message(f"📎 Документ «{filename}» сохранён.")


async def _handle_callback(callback_query: dict[str, Any], db: AsyncSession) -> None:
    from bot import client
    from modules import reminders as reminder_module

    callback_id = callback_query.get("id", "")
    data = callback_query.get("data", "")

    # toast_text is shown as a pop-up in Telegram; empty string = silent dismiss
    toast_text = ""

    if data.startswith("ok_"):
        entity_id = int(data.split("_", 1)[1])
        toast_text = "✅ Сохранено"
        logger.info("User acknowledged entity id=%d", entity_id)

    elif data.startswith("done_"):
        entity_id = int(data.split("_", 1)[1])
        await reminder_module.mark_entity_done(entity_id, db)
        await notifications.send_message(f"✅ Записано как выполненное #{entity_id}.")

    elif data.startswith("later_7d_"):
        reminder_id = int(data.split("_", 2)[2])
        await reminder_module.snooze_reminder(reminder_id, 7, db)
        await notifications.send_message("⏰ Напомню через 7 дней.")

    elif data.startswith("later_3d_"):
        reminder_id = int(data.split("_", 2)[2])
        await reminder_module.snooze_reminder(reminder_id, 3, db)
        await notifications.send_message("⏰ Напомню через 3 дня.")

    elif data.startswith("ignore_"):
        reminder_id = int(data.split("_", 1)[1])
        from sqlalchemy import select

        from models import Reminder

        result = await db.execute(select(Reminder).where(Reminder.id == reminder_id))
        reminder = result.scalar_one_or_none()
        if reminder:
            reminder.status = "cancelled"
            await db.commit()
        await notifications.send_message("🙈 Больше не напомню об этом.")

    elif data.startswith("pause_"):
        entity_id = int(data.split("_", 1)[1])
        from modules.entity_view import pause_entity

        await pause_entity(entity_id, db)
        await notifications.send_message(f"⏸ Объект #{entity_id} приостановлен.")

    elif data.startswith("archive_"):
        entity_id = int(data.split("_", 1)[1])
        from modules.entity_view import archive_entity

        await archive_entity(entity_id, db)
        await notifications.send_message(f"🗄 Объект #{entity_id} перемещён в архив.")

    elif data.startswith("attach_"):
        entity_id = int(data.split("_", 1)[1])
        await notifications.send_message(
            f"📎 Отправь файл или фото.\n"
            f"В подписи к файлу напиши <code>#{entity_id}</code> — прикреплю к этой записи."
        )

    elif data.startswith("edit_"):
        global _pending_edit_entity_id
        entity_id = int(data.split("_", 1)[1])
        _pending_edit_entity_id = entity_id
        await notifications.send_message(
            f"✏️ Напиши что изменить в записи #{entity_id}.\n"
            f"<i>Например: перенеси дату на 20 сентября</i>"
        )

    else:
        logger.debug("Unknown callback data: %s", data)

    await client.answer_callback_query(callback_id, text=toast_text)
