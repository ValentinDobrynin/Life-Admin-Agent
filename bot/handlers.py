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
        from sqlalchemy import select as sa_select

        from models import ReferenceData
        from modules.reference import get_ref_card_text, make_ref_card_buttons

        card_text = await get_ref_card_text(ref_id, db)
        item_result = await db.execute(sa_select(ReferenceData).where(ReferenceData.id == ref_id))
        ref_item = item_result.scalar_one_or_none()
        ref_buttons: list[list[dict[str, str]]] | None = (
            make_ref_card_buttons(ref_id, has_owner=bool(ref_item and ref_item.owner_ref_id))
            if ref_item and ref_item.type != "person"
            else None
        )
        await notifications.send_message(card_text, buttons=ref_buttons)
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
            from modules.reference import (
                find_reference_item,
                format_ref_data_text,
                get_reference_filename,
            )
            from modules.storage import download_file

            ref_item = await find_reference_item(text, db)
            if ref_item:
                data_text = format_ref_data_text(ref_item)
                if ref_item.r2_keys:
                    try:
                        for idx, r2_key in enumerate(ref_item.r2_keys):
                            file_bytes = download_file(r2_key)
                            filename = get_reference_filename(r2_key)
                            # caption only on the first file
                            caption = (
                                (data_text if len(data_text) <= 1024 else None)
                                if idx == 0
                                else None
                            )
                            await tg.send_document(
                                chat_id=settings.telegram_chat_id,
                                file_bytes=file_bytes,
                                filename=filename,
                                caption=caption,
                            )
                        if len(data_text) > 1024:
                            await notifications.send_message(data_text)
                    except Exception:
                        logger.exception("Failed to send reference file")
                        await notifications.send_message("❌ Не удалось отправить файл.")
                else:
                    await notifications.send_message(data_text + "\n\n<i>📎 Скан не прикреплён</i>")
            else:
                await notifications.send_message(
                    "❌ Не нашёл подходящий документ в справочнике.\n"
                    "Проверь /profile — возможно запись не добавлена."
                )
            return

        intent = detect_intent(text)

        if intent == "weather_query":
            from modules.parser import parse_trip_checklist_request
            from modules.suggestions import _fetch_trip_context

            params = await parse_trip_checklist_request(text)
            destination = params.get("destination", "")
            dates = params.get("dates", "")
            if not destination:
                await notifications.send_message(
                    "🌤 Укажи город или страну.\nНапример: «Какая погода в Дубае 20-25 апреля?»"
                )
                return

            ctx = await _fetch_trip_context(destination, dates)
            weather = ctx.get("weather", "нет данных")
            period = f" ({dates})" if dates else ""
            await notifications.send_message(
                f"🌤 <b>Погода в {destination}{period}</b>\n{weather}\n\n"
                f"<i>Хочешь соберу чеклист вещей для поездки?</i>"
            )
            return

        if intent == "checklist_trip":
            from modules.parser import parse_trip_checklist_request
            from modules.suggestions import generate_trip_checklist

            trip_params = await parse_trip_checklist_request(text)
            if not trip_params.get("destination"):
                await notifications.send_message(
                    "✈️ Укажи направление и примерные даты.\n"
                    "Например: «Собери чеклист для поездки в Дубай 20-25 апреля»"
                )
                return

            items, entity = await generate_trip_checklist(trip_params, db)

            if not items:
                await notifications.send_message("❌ Не удалось сгенерировать чеклист.")
                return

            checklist_text = "\n".join(f"• {item}" for item in items)
            destination = trip_params.get("destination", "поездки")

            if entity:
                text_out = (
                    f"✈️ <b>Чеклист для {destination}</b> · /entity {entity.id}\n\n"
                    f"{checklist_text}\n\n"
                    f"📎 Сохранил в чеклист поездки."
                )
                await notifications.send_message(text_out)
            else:
                text_out = f"✈️ <b>Чеклист для {destination}</b>\n\n{checklist_text}"
                trip_buttons: list[list[dict[str, str]]] = [
                    [
                        {
                            "text": "✈️ Создать поездку",
                            "callback_data": f"create_trip_{destination}",
                        }
                    ]
                ]
                await notifications.send_message(text_out, buttons=trip_buttons)
            return

        if intent == "generate":
            from modules.reference import generate_text

            result = await generate_text(text, db)
            await notifications.send_message(result)
            return

        if intent == "reference_add":
            from modules.reference import get_all_persons, parse_and_save_reference

            ref_result = await parse_and_save_reference(text, db)
            if ref_result:
                ref_item, ref_entity, auto_linked_person = ref_result
                msg = (
                    f"🗂 Сохранил в справочник: <b>{ref_item.label}</b>\n"
                    f"Тип: {ref_item.type} · #{ref_item.id}"
                )
                if auto_linked_person:
                    msg += f"\n👤 Привязал к: {auto_linked_person.label}"
                if ref_entity and ref_entity.end_date:
                    msg += (
                        f"\n📅 Напомню до {ref_entity.end_date.strftime('%d.%m.%Y')}"
                        f" · /entity {ref_entity.id}"
                    )
                elif ref_entity:
                    msg += f"\n📋 Создал запись без срока · /entity {ref_entity.id}"
                msg += "\n\n/profile — посмотреть весь справочник"
                ref_add_buttons: list[list[dict[str, str]]] | None = None
                if ref_item.type != "person" and not auto_linked_person:
                    persons = await get_all_persons(db)
                    if persons:
                        ref_add_buttons = [
                            [
                                {
                                    "text": "👤 Привязать к человеку",
                                    "callback_data": f"ref_link_{ref_item.id}",
                                }
                            ]
                        ]
                await notifications.send_message(msg, buttons=ref_add_buttons)
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

    from modules.reference import (
        append_file_to_reference,
        extract_reference_append_id,
        get_all_persons,
        is_reference_caption,
        parse_and_save_reference_from_file,
    )

    append_id = extract_reference_append_id(caption)
    if append_id is not None:
        updated = await append_file_to_reference(append_id, file_bytes, filename, db)
        if updated is None:
            await notifications.send_message(f"❌ Запись справочника #{append_id} не найдена.")
        else:
            n = len(updated.r2_keys)
            await notifications.send_message(
                f"📎 Добавил страницу к <b>{updated.label}</b> · #{updated.id} (всего файлов: {n})"
            )
        return

    if is_reference_caption(caption):
        ref_item, entity, auto_linked_person = await parse_and_save_reference_from_file(
            file_bytes, filename, "image/jpeg", caption, db
        )
        msg = f"🗂 Сохранил в справочник: <b>{ref_item.label}</b> · #{ref_item.id}"
        if auto_linked_person:
            msg += f"\n👤 Привязал к: {auto_linked_person.label}"
        if entity and entity.end_date:
            msg += f"\n📅 Напомню до {entity.end_date.strftime('%d.%m.%Y')} · /entity {entity.id}"
        elif entity:
            msg += f"\n📋 Создал запись без срока · /entity {entity.id}"
        msg += f"\n\n<i>Есть ещё страницы? Отправь с подписью: справочник #{ref_item.id}</i>"
        buttons: list[list[dict[str, str]]] | None = None
        if ref_item.type != "person" and not auto_linked_person:
            persons = await get_all_persons(db)
            if persons:
                buttons = [
                    [
                        {
                            "text": "👤 Привязать к человеку",
                            "callback_data": f"ref_link_{ref_item.id}",
                        }
                    ]
                ]
        await notifications.send_message(msg, buttons=buttons)
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

    from modules.reference import (
        append_file_to_reference,
        extract_reference_append_id,
        get_all_persons,
        is_reference_caption,
        parse_and_save_reference_from_file,
    )

    append_id = extract_reference_append_id(caption)
    if append_id is not None:
        updated = await append_file_to_reference(append_id, file_bytes, filename, db)
        if updated is None:
            await notifications.send_message(f"❌ Запись справочника #{append_id} не найдена.")
        else:
            n = len(updated.r2_keys)
            await notifications.send_message(
                f"📎 Добавил страницу к <b>{updated.label}</b> · #{updated.id} (всего файлов: {n})"
            )
        return

    if is_reference_caption(caption):
        ref_item, entity, auto_linked_person = await parse_and_save_reference_from_file(
            file_bytes, filename, mime_type, caption, db
        )
        msg = f"🗂 Сохранил в справочник: <b>{ref_item.label}</b> · #{ref_item.id}"
        if auto_linked_person:
            msg += f"\n👤 Привязал к: {auto_linked_person.label}"
        if entity and entity.end_date:
            msg += f"\n📅 Напомню до {entity.end_date.strftime('%d.%m.%Y')} · /entity {entity.id}"
        elif entity:
            msg += f"\n📋 Создал запись без срока · /entity {entity.id}"
        msg += f"\n\n<i>Есть ещё страницы? Отправь с подписью: справочник #{ref_item.id}</i>"
        doc_buttons: list[list[dict[str, str]]] | None = None
        if ref_item.type != "person" and not auto_linked_person:
            persons = await get_all_persons(db)
            if persons:
                doc_buttons = [
                    [
                        {
                            "text": "👤 Привязать к человеку",
                            "callback_data": f"ref_link_{ref_item.id}",
                        }
                    ]
                ]
        await notifications.send_message(msg, buttons=doc_buttons)
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

    elif data.startswith("checklist_"):
        entity_id = int(data.split("_", 1)[1])
        from modules.entity_view import get_entity_card_text, make_entity_card_buttons

        card_text = await get_entity_card_text(entity_id, db)
        buttons = make_entity_card_buttons(entity_id)
        await notifications.send_message(card_text, buttons=buttons)

    elif data.startswith("ref_link_confirm_"):
        parts = data.split("_")
        ref_id = int(parts[3])
        owner_id = int(parts[4])
        from sqlalchemy import select as sa_select

        from models import ReferenceData
        from modules.reference import set_owner

        success = await set_owner(ref_id, owner_id, db)
        if success:
            owner_result = await db.execute(
                sa_select(ReferenceData).where(ReferenceData.id == owner_id)
            )
            owner = owner_result.scalar_one_or_none()
            owner_label = owner.label if owner else f"#{owner_id}"
            await notifications.send_message(f"✅ Привязал к: <b>{owner_label}</b>")
        else:
            await notifications.send_message("❌ Не удалось привязать.")

    elif data == "ref_link_cancel":
        await notifications.send_message("Отмена.")

    elif data.startswith("create_trip_"):
        destination = data[len("create_trip_") :]
        await notifications.send_message(
            f"✈️ Чтобы создать поездку, напиши мне:\n"
            f"<code>Поездка в {destination}</code>\n"
            f"— и я создам запись с напоминаниями."
        )

    elif data.startswith("ref_link_"):
        ref_id = int(data.split("_", 2)[2])
        from modules.reference import get_all_persons

        persons = await get_all_persons(db)
        if not persons:
            await notifications.send_message(
                "👤 В справочнике нет карточек людей.\n"
                "Добавь: «Добавь в справочник: жена Анастасия Добрынина»"
            )
        else:
            buttons = [
                [{"text": p.label, "callback_data": f"ref_link_confirm_{ref_id}_{p.id}"}]
                for p in persons
            ]
            buttons.append([{"text": "❌ Отмена", "callback_data": "ref_link_cancel"}])
            await notifications.send_message("👤 Выбери владельца:", buttons=buttons)

    else:
        logger.debug("Unknown callback data: %s", data)

    await client.answer_callback_query(callback_id, text=toast_text)
