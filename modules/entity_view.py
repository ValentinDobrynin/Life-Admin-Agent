from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import and_, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ChecklistItem, Entity, EventLog, Reminder, Resource

logger = logging.getLogger(__name__)

_CATEGORY_EMOJI = {
    "document": "📄",
    "trip": "✈️",
    "gift": "🎁",
    "certificate": "🎟",
    "subscription": "🔄",
    "payment": "💳",
    "logistics": "📦",
}

_CATEGORY_NAMES = {
    "document": "Документы",
    "trip": "Поездки",
    "gift": "Подарки и даты",
    "certificate": "Сертификаты",
    "subscription": "Подписки",
    "payment": "Счета и платежи",
    "logistics": "Разное",
}

_STATUS_LABELS = {
    "active": "активно",
    "expiring_soon": "⚡ скоро истекает",
    "expired": "просрочено",
    "paused": "приостановлено",
    "closed": "закрыто",
    "archived": "архив",
}

EXPIRING_SOON_DAYS = 14


async def get_status_text(db: AsyncSession) -> str:
    """Build /status overview grouped by entity type."""
    today = date.today()

    result = await db.execute(
        select(Entity)
        .where(Entity.status.in_(["active", "expiring_soon"]))
        .order_by(Entity.type, nullslast(Entity.end_date))
    )
    entities = list(result.scalars().all())

    if not entities:
        return "📭 Пока ничего не отслеживается.\n\nДобавь первый объект — просто напиши мне."

    grouped: dict[str, list[Entity]] = {}
    for entity in entities:
        grouped.setdefault(entity.type, []).append(entity)

    lines = ["📊 <b>Что сейчас в агенте</b>\n"]

    for type_key in _CATEGORY_NAMES:
        items = grouped.get(type_key, [])
        if not items:
            continue

        lines.append(
            f"{_CATEGORY_EMOJI[type_key]} <b>{_CATEGORY_NAMES[type_key]}</b> ({len(items)})"
        )

        for entity in items:
            line = f"  #{entity.id} {entity.name}"

            if entity.end_date:
                delta = (entity.end_date - today).days
                if delta < 0:
                    line += f" — <i>просрочено {abs(delta)}д</i>"
                elif delta <= EXPIRING_SOON_DAYS:
                    line += f" — ⚡ через {delta}д"
                else:
                    line += f" — до {entity.end_date.strftime('%d.%m.%Y')}"
            elif entity.start_date:
                line += f" — {entity.start_date.strftime('%d.%m.%Y')}"

            lines.append(line)

        lines.append("")

    lines.append("<i>Напиши /entity &lt;id&gt; чтобы открыть карточку объекта</i>")
    return "\n".join(lines)


async def get_entity_card_text(entity_id: int, db: AsyncSession) -> str:
    """Build drill-down card for a single entity."""
    today = date.today()

    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()

    if entity is None:
        return f"❌ Объект #{entity_id} не найден."

    emoji = _CATEGORY_EMOJI.get(entity.type, "📌")
    lines = [f"{emoji} <b>{entity.name}</b>  <i>#{entity.id}</i>"]

    if entity.start_date and entity.end_date:
        lines.append(
            f"📅 {entity.start_date.strftime('%d.%m.%Y')} — {entity.end_date.strftime('%d.%m.%Y')}"
        )
    elif entity.end_date:
        delta = (entity.end_date - today).days
        suffix = f" (через {delta}д)" if delta >= 0 else f" (просрочено {abs(delta)}д)"
        lines.append(f"📅 До {entity.end_date.strftime('%d.%m.%Y')}{suffix}")
    elif entity.start_date:
        lines.append(f"📅 {entity.start_date.strftime('%d.%m.%Y')}")

    lines.append(f"Статус: {_STATUS_LABELS.get(entity.status, entity.status)}")

    if entity.notes:
        lines.append(f"\n📝 {entity.notes}")

    checklist_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.entity_id == entity_id)
        .order_by(ChecklistItem.position)
    )
    checklist = list(checklist_result.scalars().all())

    if checklist:
        lines.append("\n<b>Чеклист:</b>")
        for item in checklist:
            mark = "✅" if item.status == "done" else "☐"
            lines.append(f"  {mark} {item.text}")

    resources_result = await db.execute(select(Resource).where(Resource.entity_id == entity_id))
    resources = list(resources_result.scalars().all())

    if resources:
        lines.append("\n<b>Файлы и ссылки:</b>")
        for res in resources:
            if res.type == "link" and res.url:
                lines.append(f"  🔗 <a href='{res.url}'>{res.filename or res.url}</a>")
            elif res.type == "file" and res.filename:
                lines.append(f"  📎 {res.filename}")

    reminders_result = await db.execute(
        select(Reminder)
        .where(
            and_(
                Reminder.entity_id == entity_id,
                Reminder.status == "pending",
            )
        )
        .order_by(Reminder.trigger_date)
    )
    reminders = list(reminders_result.scalars().all())

    if reminders:
        lines.append("\n<b>Напоминания:</b>")
        for rem in reminders:
            lines.append(f"  🔔 {rem.trigger_date.strftime('%d.%m.%Y')}")

    return "\n".join(lines)


async def archive_entity(entity_id: int, db: AsyncSession) -> bool:
    """Set entity status to archived, cancel pending reminders, log the action."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if entity is None:
        return False

    entity.status = "archived"

    pending = await db.execute(
        select(Reminder).where(and_(Reminder.entity_id == entity_id, Reminder.status == "pending"))
    )
    for reminder in pending.scalars().all():
        reminder.status = "cancelled"

    db.add(
        EventLog(
            entity_id=entity_id,
            action="entity_archived",
            payload={"entity_id": entity_id, "source": "user"},
        )
    )
    await db.commit()
    return True


async def pause_entity(entity_id: int, db: AsyncSession) -> bool:
    """Set entity status to paused and log the action."""
    result = await db.execute(select(Entity).where(Entity.id == entity_id))
    entity = result.scalar_one_or_none()
    if entity is None:
        return False

    entity.status = "paused"
    db.add(
        EventLog(
            entity_id=entity_id,
            action="entity_paused",
            payload={"entity_id": entity_id},
        )
    )
    await db.commit()
    return True


def make_entity_card_buttons(entity_id: int) -> list[list[dict[str, str]]]:
    """Inline buttons for entity card."""
    return [
        [
            {"text": "✅ Закрыть", "callback_data": f"done_{entity_id}"},
            {"text": "⏸ Пауза", "callback_data": f"pause_{entity_id}"},
        ],
        [
            {"text": "📎 Добавить файл", "callback_data": f"attach_{entity_id}"},
            {"text": "🗄 Архив", "callback_data": f"archive_{entity_id}"},
        ],
    ]
