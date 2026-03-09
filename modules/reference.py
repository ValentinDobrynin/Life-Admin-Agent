from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Entity, EventLog, ReferenceData, Reminder

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Load prompt from /prompts, stripping metadata comment lines."""
    text = (_PROMPTS_DIR / name).read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip()


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

# Genitive → nominative normalization for relation detection from labels
_RELATION_NORMALIZATION: dict[str, str] = {
    "жены": "жена",
    "жена": "жена",
    "мужа": "муж",
    "муж": "муж",
    "сына": "сын",
    "сын": "сын",
    "дочери": "дочь",
    "дочь": "дочь",
    "мамы": "мама",
    "мама": "мама",
    "папы": "папа",
    "папа": "папа",
    "друга": "друг",
    "друг": "друг",
    "подруги": "подруга",
    "подруга": "подруга",
    "я": "я",
}


def _normalize_relation(kw: str) -> str:
    return _RELATION_NORMALIZATION.get(kw.lower(), kw.lower())


def _detect_relation_in_label(label: str) -> str | None:
    """Return the normalized relation found in label, or None."""
    label_lower = label.lower()
    for kw in _RELATION_NORMALIZATION:
        if kw in label_lower:
            return _normalize_relation(kw)
    return None


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def get_all_reference(db: AsyncSession) -> list[ReferenceData]:
    result = await db.execute(
        select(ReferenceData).order_by(ReferenceData.type, ReferenceData.label)
    )
    return list(result.scalars().all())


async def get_all_persons(db: AsyncSession) -> list[ReferenceData]:
    """Return all reference entries of type=person."""
    result = await db.execute(
        select(ReferenceData).where(ReferenceData.type == "person").order_by(ReferenceData.label)
    )
    return list(result.scalars().all())


async def find_person_by_relation(relation: str, db: AsyncSession) -> ReferenceData | None:
    """Find a person card by relation keyword (жена, муж, сын, etc.)."""
    result = await db.execute(
        select(ReferenceData).where(
            and_(
                ReferenceData.type == "person",
                ReferenceData.relation == relation.lower().strip(),
            )
        )
    )
    return result.scalar_one_or_none()


async def get_owned_items(owner_ref_id: int, db: AsyncSession) -> list[ReferenceData]:
    """Return all reference items owned by a given person."""
    result = await db.execute(
        select(ReferenceData)
        .where(ReferenceData.owner_ref_id == owner_ref_id)
        .order_by(ReferenceData.type, ReferenceData.label)
    )
    return list(result.scalars().all())


async def set_owner(ref_id: int, owner_ref_id: int, db: AsyncSession) -> bool:
    """Attach an owner to a reference item. Returns True if successful."""
    result = await db.execute(select(ReferenceData).where(ReferenceData.id == ref_id))
    item = result.scalar_one_or_none()
    if not item:
        return False
    item.owner_ref_id = owner_ref_id
    await db.commit()
    logger.info("Linked ref #%d → owner ref #%d", ref_id, owner_ref_id)
    return True


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
            line = f"  #{item.id} <b>{item.label}</b>"
            if item.owner_ref_id:
                owner_result = await db.execute(
                    select(ReferenceData).where(ReferenceData.id == item.owner_ref_id)
                )
                owner = owner_result.scalar_one_or_none()
                if owner:
                    line += f" · 👤 {owner.label}"
            lines.append(line)
            if item.type == "person":
                owned = await get_owned_items(item.id, db)
                for owned_item in owned:
                    emoji = _TYPE_EMOJI.get(owned_item.type, "📎")
                    lines.append(f"    {emoji} {owned_item.label} · #{owned_item.id}")
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

    if item.type == "person":
        owned = await get_owned_items(item.id, db)
        if owned:
            lines.append("\n<b>Документы и имущество:</b>")
            for owned_item in owned:
                owned_emoji = _TYPE_EMOJI.get(owned_item.type, "📎")
                suffix = " · 📎" if owned_item.r2_key else ""
                lines.append(f"  {owned_emoji} {owned_item.label} · #{owned_item.id}{suffix}")
    elif item.owner_ref_id:
        owner_result = await db.execute(
            select(ReferenceData).where(ReferenceData.id == item.owner_ref_id)
        )
        owner = owner_result.scalar_one_or_none()
        if owner:
            lines.append(f"\n👤 Владелец: {owner.label}")
        else:
            lines.append(f"\n👤 Владелец: не найден (ref #{item.owner_ref_id})")
    else:
        lines.append("\n👤 Владелец: не привязан")

    return "\n".join(lines)


def make_ref_card_buttons(ref_id: int, has_owner: bool = False) -> list[list[dict[str, str]]]:
    """Inline buttons for a reference card."""
    link_label = "👤 Сменить владельца" if has_owner else "👤 Привязать к человеку"
    return [
        [
            {"text": "✏️ Изменить", "callback_data": f"ref_edit_{ref_id}"},
            {"text": link_label, "callback_data": f"ref_link_{ref_id}"},
        ],
    ]


async def save_reference_item(
    ref_type: str,
    label: str,
    data: dict[str, Any],
    db: AsyncSession,
    notes: str | None = None,
    relation: str | None = None,
    owner_ref_id: int | None = None,
) -> ReferenceData:
    """Save a new reference item to DB."""
    item = ReferenceData(
        type=ref_type,
        label=label,
        data=data,
        notes=notes,
        relation=relation,
        owner_ref_id=owner_ref_id,
    )
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
        entry = f"[#{item.id}] {item.label} ({item.type})"
        if item.relation:
            entry += f" [relation: {item.relation}]"
        if fields:
            entry += f": {fields}"
        if item.type == "person":
            owned = await get_owned_items(item.id, db)
            if owned:
                owned_lines = []
                for o in owned:
                    o_fields = ", ".join(f"{k}: {v}" for k, v in o.data.items() if v)
                    owned_lines.append(f"  - {o.label}: {o_fields}")
                entry += "\n" + "\n".join(owned_lines)
        context_parts.append(entry)
    context = "\n".join(context_parts)

    system_prompt = _load_prompt("reference_generate.txt").replace("{context}", context)

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


async def _create_entity_for_reference(
    ref_item: ReferenceData,
    end_date_str: str | None,
    db: AsyncSession,
) -> Entity:
    """Create entity (and reminders) for a reference item. Caller must commit."""
    from datetime import date, timedelta

    from modules.ingestion import parse_date

    end_date = parse_date(end_date_str) if end_date_str else None
    entity = Entity(
        type="document",
        name=ref_item.label,
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
            payload={"reference_id": ref_item.id, "label": ref_item.label},
        )
    )
    return entity


async def _auto_link_owner(
    ref_item: ReferenceData,
    db: AsyncSession,
) -> ReferenceData | None:
    """Detect relation keyword in label, find person card, set owner_ref_id.

    Caller must commit after this call.
    Returns the linked person card, or None.
    """
    if ref_item.type == "person":
        return None
    detected_relation = _detect_relation_in_label(ref_item.label)
    if not detected_relation:
        return None
    person = await find_person_by_relation(detected_relation, db)
    if not person:
        return None
    ref_item.owner_ref_id = person.id
    logger.info(
        "Auto-linked ref #%d → person #%d via relation '%s'",
        ref_item.id,
        person.id,
        detected_relation,
    )
    return person


async def parse_and_save_reference_from_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    caption: str,
    db: AsyncSession,
) -> tuple[ReferenceData, Entity | None, ReferenceData | None]:
    """Parse a file (photo or PDF), save to reference_data, create entity if needed.

    Entity is always created for document and person types (even without end_date).
    For car and address — no entity.
    Returns (reference_item, entity_or_none, auto_linked_person_or_none).
    """
    from modules import storage
    from modules.ingestion import extract_text_from_file

    r2_key = storage.upload_file(file_bytes, filename, prefix="reference")
    raw_text = await extract_text_from_file(file_bytes, filename, mime_type)
    label_from_caption = extract_reference_label(caption)

    system_prompt = _load_prompt("reference_file_parse.txt")

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
    relation_raw: str | None = parsed.get("relation")
    relation = (
        _normalize_relation(relation_raw)
        if relation_raw and relation_raw.lower() not in ("null", "none", "")
        else None
    )

    ref_item = ReferenceData(
        type=ref_type,
        label=ref_label,
        data=ref_data,
        r2_key=r2_key,
        relation=relation if ref_type == "person" else None,
    )
    db.add(ref_item)
    await db.flush()
    await db.refresh(ref_item)

    entity: Entity | None = None
    if ref_type in _TYPES_WITH_ENTITY:
        entity = await _create_entity_for_reference(ref_item, end_date_str, db)

    auto_linked_person = await _auto_link_owner(ref_item, db)

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
    return ref_item, entity, auto_linked_person


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
    system_prompt = _load_prompt("reference_find.txt").replace("{context}", context)

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


async def parse_and_save_reference(
    raw_text: str,
    db: AsyncSession,
) -> tuple[ReferenceData, Entity | None, ReferenceData | None] | None:
    """Extract reference data from free-form text, save, create entity if needed.

    Mirrors parse_and_save_reference_from_file: saves relation, creates entity
    with reminders for document/person types, auto-links owner by label keyword.
    Returns (reference_item, entity_or_none, auto_linked_person_or_none) or None on error.
    """
    system_prompt = _load_prompt("reference_text_parse.txt")

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
    except Exception:
        logger.exception("Reference parsing failed for: %.100s", raw_text)
        return None

    ref_type = parsed.get("type", "document")
    label = parsed.get("label", "Новая запись")
    data = parsed.get("data", {})
    end_date_str: str | None = parsed.get("end_date")
    relation_raw: str | None = parsed.get("relation")
    relation = (
        _normalize_relation(relation_raw)
        if relation_raw and relation_raw.lower() not in ("null", "none", "")
        else None
    )

    ref_item = ReferenceData(
        type=ref_type,
        label=label,
        data=data,
        relation=relation if ref_type == "person" else None,
    )
    db.add(ref_item)
    await db.flush()
    await db.refresh(ref_item)

    entity: Entity | None = None
    if ref_type in _TYPES_WITH_ENTITY:
        entity = await _create_entity_for_reference(ref_item, end_date_str, db)

    auto_linked_person = await _auto_link_owner(ref_item, db)

    await db.commit()
    if entity:
        await db.refresh(entity)
    await db.refresh(ref_item)

    logger.info(
        "Saved reference from text id=%d type=%s label=%s entity_id=%s",
        ref_item.id,
        ref_type,
        label,
        entity.id if entity else None,
    )
    return ref_item, entity, auto_linked_person
