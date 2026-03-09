from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from bot import client
from config import settings
from models import Entity, Reminder

if TYPE_CHECKING:
    from modules.suggestions import EnrichedReminder

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

MAX_DIGEST_ITEMS = 7


async def send_confirmation(entity: Entity, extra_text: str = "") -> None:
    """Send capture confirmation to the user with action buttons."""
    emoji = _CATEGORY_EMOJI.get(entity.type, "📌")
    dates_str = _format_dates(entity)
    text = f"{emoji} Сохранил.\n<b>{entity.name}</b>{dates_str}"
    if extra_text:
        text += f"\n{extra_text}"

    buttons = make_capture_buttons(entity.id)
    await client.send_message(
        chat_id=settings.telegram_chat_id,
        text=text,
        reply_markup=client.make_inline_keyboard(buttons),
    )


async def send_enriched_reminder(enriched: EnrichedReminder) -> None:
    """Send a point-push reminder with next_action, resources and snooze buttons."""
    entity = enriched.entity
    reminder = enriched.reminder
    emoji = _CATEGORY_EMOJI.get(entity.type, "📌")

    lines = [f"{emoji} <b>{entity.name}</b>{_format_dates(entity)}"]

    if enriched.next_action:
        lines.append(f"➡️ {enriched.next_action}")

    if enriched.missing_checklist:
        missing = enriched.missing_checklist[:3]
        lines.append("❌ <b>Не хватает:</b>")
        for item in missing:
            lines.append(f"  • {item}")

    if enriched.note:
        lines.append(f"⚠️ {enriched.note}")

    if enriched.shortlist:
        lines.append("💡 Идеи: " + " · ".join(enriched.shortlist[:3]))

    today = date.today()
    days_left: int | None = None
    if entity.start_date:
        days_left = (entity.start_date - today).days
    elif entity.end_date:
        days_left = (entity.end_date - today).days

    buttons = _make_reminder_buttons(reminder.id, entity.id, entity.type, days_left)
    await client.send_message(
        chat_id=settings.telegram_chat_id,
        text="\n".join(lines),
        reply_markup=client.make_inline_keyboard(buttons),
    )


async def send_reminder(reminder: Reminder, entity: Entity) -> None:
    """Send a basic reminder (without suggestion enrichment)."""
    emoji = _CATEGORY_EMOJI.get(entity.type, "📌")
    text = f"{emoji} <b>{entity.name}</b>{_format_dates(entity)}"

    today = date.today()
    days_left: int | None = None
    if entity.start_date:
        days_left = (entity.start_date - today).days
    elif entity.end_date:
        days_left = (entity.end_date - today).days

    buttons = _make_reminder_buttons(reminder.id, entity.id, entity.type, days_left)
    await client.send_message(
        chat_id=settings.telegram_chat_id,
        text=text,
        reply_markup=client.make_inline_keyboard(buttons),
    )


async def send_digest(items: list[tuple[Reminder, Entity]]) -> None:
    """Send the daily digest — max 7 items grouped by urgency."""
    if not items:
        return

    from datetime import date

    today = date.today()
    urgent: list[tuple[Reminder, Entity]] = []
    this_week: list[tuple[Reminder, Entity]] = []

    for reminder, entity in items:
        delta = (reminder.trigger_date - today).days
        if delta <= 2:
            urgent.append((reminder, entity))
        else:
            this_week.append((reminder, entity))

    lines = ["📋 <b>Life Admin Brief</b>"]

    urgent_shown = urgent[:3]
    week_slots = MAX_DIGEST_ITEMS - len(urgent_shown)
    week_shown = this_week[:week_slots]

    if urgent_shown:
        lines.append("\n🔴 <b>Срочно:</b>")
        for reminder, entity in urgent_shown:
            lines.append(_digest_line(reminder, entity, today))

    if week_shown:
        lines.append("\n📅 <b>На этой неделе:</b>")
        for reminder, entity in week_shown:
            lines.append(_digest_line(reminder, entity, today))

    await client.send_message(
        chat_id=settings.telegram_chat_id,
        text="\n".join(lines),
    )


async def send_proactive_hints(hints: list[str]) -> None:
    """Send proactive suggestion hints (if any)."""
    if not hints:
        return
    text = "💡 <b>Подсказки:</b>\n" + "\n".join(f"• {h}" for h in hints)
    await client.send_message(chat_id=settings.telegram_chat_id, text=text)


async def send_message(
    text: str,
    buttons: list[list[dict[str, str]]] | None = None,
) -> None:
    """Send a plain message to the configured chat."""
    reply_markup: dict[str, Any] | None = None
    if buttons:
        reply_markup = client.make_inline_keyboard(buttons)
    await client.send_message(
        chat_id=settings.telegram_chat_id,
        text=text,
        reply_markup=reply_markup,
    )


def make_capture_buttons(entity_id: int) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "✅ OK", "callback_data": f"ok_{entity_id}"},
            {"text": "📎 Добавить файл", "callback_data": f"attach_{entity_id}"},
        ],
        [
            {"text": "✏️ Изменить", "callback_data": f"edit_{entity_id}"},
        ],
    ]


def _make_reminder_buttons(
    reminder_id: int,
    entity_id: int,
    entity_type: str = "",
    days_left: int | None = None,
) -> list[list[dict[str, str]]]:
    is_urgent_trip = entity_type == "trip" and days_left is not None and days_left <= 2
    if is_urgent_trip:
        return [
            [
                {"text": "✅ Готово", "callback_data": f"done_{entity_id}"},
                {"text": "📋 Чеклист", "callback_data": f"checklist_{entity_id}"},
            ],
            [
                {"text": "🙈 Игнор", "callback_data": f"ignore_{reminder_id}"},
            ],
        ]
    return [
        [
            {"text": "✅ Готово", "callback_data": f"done_{entity_id}"},
            {"text": "⏰ +7 дней", "callback_data": f"later_7d_{reminder_id}"},
        ],
        [
            {"text": "⏰ +3 дня", "callback_data": f"later_3d_{reminder_id}"},
            {"text": "🙈 Игнор", "callback_data": f"ignore_{reminder_id}"},
        ],
    ]


def _format_dates(entity: Entity) -> str:
    if entity.end_date:
        return f" — до {entity.end_date.strftime('%d.%m.%Y')}"
    if entity.start_date:
        return f" — {entity.start_date.strftime('%d.%m.%Y')}"
    return ""


def _digest_line(reminder: Reminder, entity: Entity, today: date) -> str:
    emoji = _CATEGORY_EMOJI.get(entity.type, "📌")
    if entity.end_date:
        delta = (entity.end_date - today).days
        if delta == 0:
            suffix = " — сегодня!"
        elif delta < 0:
            suffix = f" — просрочено {abs(delta)}д"
        else:
            suffix = f" — через {delta}д"
    else:
        suffix = ""
    return f"  {emoji} {entity.name}{suffix}"
