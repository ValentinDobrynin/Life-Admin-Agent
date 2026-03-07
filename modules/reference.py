from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Entity, EventLog, ReferenceData, Reminder

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

# These types always get an entity created (for tracking and reminders)
_TYPES_WITH_ENTITY = {"document", "person"}

# Caption trigger words for reference flow
_REFERENCE_TRIGGERS = ["справочник", "в справочник", "сохрани в справочник"]


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
        "1. Если нужного типа объект только один — используй его без вопросов, "
        "составь готовый текст.\n"
        "2. Если запрос однозначен (пользователь назвал конкретную машину/паспорт/адрес) "
        "— сразу составь готовый текст.\n"
        "3. Если нужного типа объектов несколько И непонятно какой использовать "
        "— НЕ генерируй текст. Задай уточняющий вопрос "
        "и перечисли варианты нумерованным списком с ключевыми полями.\n"
        "4. Если данных не хватает — укажи что именно нужно добавить.\n\n"
        "Пример уточняющего вопроса (только когда вариантов действительно несколько):\n"
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


def is_reference_caption(caption: str) -> bool:
    """Return True if file caption indicates reference storage intent."""
    if not caption:
        return False
    lower = caption.lower().strip()
    return any(
        lower == t or lower.startswith(t + ":") or lower.startswith(t + " ")
        for t in _REFERENCE_TRIGGERS
    )


def extract_reference_label(caption: str) -> str | None:
    """Extract label from caption like 'справочник: мой загранпаспорт'.

    Returns the label after the colon, or None if only a trigger word was given.
    """
    lower = caption.lower().strip()
    for trigger in _REFERENCE_TRIGGERS:
        if lower.startswith(trigger + ":"):
            label = caption[len(trigger) + 1 :].strip()
            return label if label else None
    return None


async def parse_and_save_reference_from_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    caption: str,
    db: AsyncSession,
) -> tuple[ReferenceData, Entity | None]:
    """Parse a file (photo or PDF), save to reference_data, create entity if needed.

    Entity is always created for document and person types (even without end_date).
    For car and address — no entity.
    Returns (reference_item, entity_or_none).
    """
    from datetime import date, timedelta

    from modules import storage
    from modules.ingestion import extract_text_from_file, parse_date

    r2_key = storage.upload_file(file_bytes, filename, prefix="reference")
    raw_text = await extract_text_from_file(file_bytes, filename, mime_type)
    label_from_caption = extract_reference_label(caption)

    system_prompt = """Ты — система извлечения данных из документов для личного справочника.

Из текста или описания документа извлеки данные и верни JSON строго по схеме:

{
  "type": "person | car | address | document",
  "label": "краткое название (Загранпаспорт РФ, Паспорт жены, Водительские права, Полис ОСАГО)",
  "data": {
    "ключ": "значение"
  },
  "end_date": "YYYY-MM-DD или null"
}

Для type=document: doc_type, series, number, issued_by, issue_date, expiry_date, full_name
Для type=person: full_name, birth_date, passport_rf, passport_foreign, inn, snils
Для type=car: brand, model, year, plate, vin, color
Для type=address: full_address, comment

Правила:
- Если label передан отдельно — используй его, не придумывай свой
- Заполняй только поля которые есть в тексте
- end_date — только дата истечения (не дата выдачи)
- Отвечай ТОЛЬКО JSON, без пояснений"""

    user_content = raw_text if raw_text else f"Документ: {filename}"
    if label_from_caption:
        user_content = f"Тип документа: {label_from_caption}\n\n{user_content}"

    try:
        response = await _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
    except Exception:
        logger.exception("Reference file parsing failed")
        parsed = {}

    ref_type = parsed.get("type", "document")
    ref_label = label_from_caption or parsed.get("label") or filename
    ref_data = parsed.get("data", {})
    end_date_str: str | None = parsed.get("end_date")

    ref_item = ReferenceData(
        type=ref_type,
        label=ref_label,
        data=ref_data,
        r2_key=r2_key,
    )
    db.add(ref_item)
    await db.flush()
    await db.refresh(ref_item)

    entity: Entity | None = None
    if ref_type in _TYPES_WITH_ENTITY:
        end_date = parse_date(end_date_str) if end_date_str else None
        entity = Entity(
            type="document",
            name=ref_label,
            end_date=end_date,
            status="active",
            priority="normal",
            notes=f"reference_id:{ref_item.id}",
        )
        db.add(entity)
        await db.flush()

        if end_date:
            today = date.today()
            for days in [30, 14, 7]:
                trigger_date = end_date - timedelta(days=days)
                if trigger_date >= today:
                    db.add(
                        Reminder(
                            entity_id=entity.id,
                            rule="before_N_days",
                            trigger_date=trigger_date,
                            status="pending",
                            channel="telegram",
                        )
                    )

        db.add(
            EventLog(
                entity_id=entity.id,
                action="entity_created_from_reference",
                payload={"reference_id": ref_item.id, "label": ref_label},
            )
        )

    await db.commit()
    if entity:
        await db.refresh(entity)
    await db.refresh(ref_item)

    logger.info(
        "Saved reference from file id=%d type=%s label=%s r2_key=%s entity_id=%s",
        ref_item.id,
        ref_type,
        ref_label,
        r2_key,
        entity.id if entity else None,
    )
    return ref_item, entity


def format_ref_data_text(item: ReferenceData) -> str:
    """Format reference item as readable HTML text for Telegram (caption or message)."""
    emoji = _TYPE_EMOJI.get(item.type, "🗂")
    lines = [f"{emoji} <b>{item.label}</b>  <i>#{item.id}</i>"]
    for key, value in item.data.items():
        if value:
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


async def find_reference_item(
    user_request: str,
    db: AsyncSession,
) -> ReferenceData | None:
    """Find a reference item matching the user's natural-language request.

    Searches all reference entries (with or without attached files).
    Returns None if no match found.
    """
    items = await get_all_reference(db)
    if not items:
        return None

    context = "\n".join(f"#{item.id} {item.label} (тип: {item.type})" for item in items)
    system_prompt = (
        "Пользователь ищет запись в личном справочнике.\n"
        f"Доступные записи:\n{context}\n\n"
        "Верни ТОЛЬКО id нужной записи (просто число) или 0 если ничего не подходит.\n"
        "Без пояснений."
    )

    try:
        response = await _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_request},
            ],
            max_tokens=10,
            temperature=0,
        )
        result_id = int((response.choices[0].message.content or "0").strip())
    except Exception:
        logger.exception("Reference item search failed")
        return None

    if result_id == 0:
        return None

    return next((i for i in items if i.id == result_id), None)


def get_reference_filename(r2_key: str) -> str:
    """Extract original filename from r2_key."""
    return r2_key.split("/")[-1] if "/" in r2_key else r2_key


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
