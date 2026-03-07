from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import ReferenceData

logger = logging.getLogger(__name__)

_TYPE_EMOJI = {
    "person": "👤",
    "car": "🚗",
    "address": "📍",
    "document": "🪪",
}

_TYPE_NAMES = {
    "person": "Люди",
    "car": "Машины",
    "address": "Адреса",
    "document": "Документы",
}


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def get_all_reference(db: AsyncSession) -> list[ReferenceData]:
    result = await db.execute(
        select(ReferenceData).order_by(ReferenceData.type, ReferenceData.label)
    )
    return list(result.scalars().all())


async def get_profile_text(db: AsyncSession) -> str:
    """Format all reference data for /profile command."""
    items = await get_all_reference(db)

    if not items:
        return (
            "📭 Справочник пуст.\n\n"
            "Добавь данные — например:\n"
            "— <i>Добавь в справочник: Тойота Камри, гос.номер А123БВ777</i>\n"
            "— <i>Добавь в справочник: адрес — Москва, ул. Пушкина, д. 1</i>\n"
            "— <i>Добавь в справочник: загранпаспорт РФ, серия 71 №1234567, до 2029-05-01</i>"
        )

    grouped: dict[str, list[ReferenceData]] = {}
    for item in items:
        grouped.setdefault(item.type, []).append(item)

    lines = ["🗂 <b>Справочник личных данных</b>\n"]

    for type_key in _TYPE_NAMES:
        type_items = grouped.get(type_key, [])
        if not type_items:
            continue

        lines.append(f"{_TYPE_EMOJI[type_key]} <b>{_TYPE_NAMES[type_key]}</b>")
        for item in type_items:
            lines.append(f"  #{item.id} <b>{item.label}</b>")
            for key, value in item.data.items():
                if value:
                    lines.append(f"    {key}: {value}")
        lines.append("")

    lines.append("<i>Используй /ref &lt;id&gt; чтобы открыть запись</i>")
    return "\n".join(lines)


async def get_ref_card_text(ref_id: int, db: AsyncSession) -> str:
    """Build drill-down card for a single reference item."""
    result = await db.execute(select(ReferenceData).where(ReferenceData.id == ref_id))
    item = result.scalar_one_or_none()

    if item is None:
        return f"❌ Запись справочника #{ref_id} не найдена."

    emoji = _TYPE_EMOJI.get(item.type, "🗂")
    lines = [f"{emoji} <b>{item.label}</b>  <i>#{item.id}</i>"]
    lines.append(f"Тип: {_TYPE_NAMES.get(item.type, item.type)}")

    if item.data:
        lines.append("\n<b>Данные:</b>")
        for key, value in item.data.items():
            if value:
                lines.append(f"  {key}: {value}")

    if item.notes:
        lines.append(f"\n📝 {item.notes}")

    return "\n".join(lines)


async def save_reference_item(
    ref_type: str,
    label: str,
    data: dict[str, Any],
    db: AsyncSession,
    notes: str | None = None,
) -> ReferenceData:
    """Save a new reference item to DB."""
    item = ReferenceData(type=ref_type, label=label, data=data, notes=notes)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    logger.info("Saved reference item id=%d type=%s label=%s", item.id, ref_type, label)
    return item


async def generate_text(user_request: str, db: AsyncSession) -> str:
    """Generate a ready-to-use text using reference data as context."""
    items = await get_all_reference(db)

    if not items:
        return (
            "❌ Справочник пуст — нечего подставлять.\n"
            "Сначала добавь данные: машину, паспорт, адрес."
        )

    context_parts = []
    for item in items:
        fields = ", ".join(f"{k}: {v}" for k, v in item.data.items() if v)
        context_parts.append(f"[#{item.id}] {item.label} ({item.type}): {fields}")
    context = "\n".join(context_parts)

    system_prompt = (
        "Ты помощник пользователя. У тебя есть его личные данные:\n\n"
        f"{context}\n\n"
        "ВАЖНО: у пользователя может быть несколько объектов одного типа "
        "(например, две машины, три паспорта).\n\n"
        "Правила:\n"
        "1. Если запрос однозначен (пользователь указал конкретную машину/паспорт/адрес) "
        "— сразу составь готовый текст.\n"
        "2. Если запрос неоднозначен (непонятно какую именно запись использовать) "
        "— НЕ генерируй текст. Вместо этого задай уточняющий вопрос "
        "и перечисли варианты нумерованным списком с ключевыми полями.\n"
        "3. Если данных не хватает — укажи что именно нужно добавить.\n\n"
        "Примеры уточняющего вопроса:\n"
        "«У тебя несколько машин:\n1. Тойота Камри · А123БВ777\n2. BMW X5 · В456ГД777\n"
        "Для какой делаем пропуск?»\n\n"
        "Язык ответа — русский."
    )

    try:
        response = await _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_request},
            ],
            max_tokens=600,
            temperature=0.3,
        )
        return response.choices[0].message.content or "❌ Не удалось сгенерировать текст."
    except Exception:
        logger.exception("Text generation failed")
        return "❌ Ошибка при генерации текста. Попробуй ещё раз."


async def parse_and_save_reference(raw_text: str, db: AsyncSession) -> ReferenceData | None:
    """Use OpenAI to extract reference data from free-form text and save it."""
    system_prompt = """Ты — система извлечения данных для личного справочника.

Из текста пользователя извлеки данные и верни JSON строго по схеме:

{
  "type": "person | car | address | document",
  "label": "краткое название (например: Загранпаспорт РФ, Тойота Камри, Дом)",
  "data": {
    "ключ": "значение"
  }
}

Для type=person поля data: full_name, birth_date, passport_rf, passport_foreign, inn, snils
Для type=car поля data: brand, model, year, plate, vin, color
Для type=address поля data: full_address, comment
Для type=document поля data: doc_type, series, number, issued_by, issue_date, expiry_date

Правила:
- Заполняй только те поля, которые есть в тексте
- Пустые поля не включай в data
- label должен быть конкретным и понятным
- Отвечай ТОЛЬКО JSON, без пояснений"""

    try:
        response = await _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)

        ref_type = parsed.get("type", "document")
        label = parsed.get("label", "Новая запись")
        data = parsed.get("data", {})

        return await save_reference_item(ref_type, label, data, db)
    except Exception:
        logger.exception("Reference parsing failed for: %.100s", raw_text)
        return None
