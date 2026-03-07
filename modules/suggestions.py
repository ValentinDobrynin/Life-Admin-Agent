from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import ChecklistItem, Entity, Reminder, Resource

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.openai_api_key)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "suggestion.txt"

_DEFAULT_NEXT_ACTION: dict[str, str] = {
    "document": "Проверить срок и начать процедуру продления",
    "trip": "Проверить чеклист поездки",
    "gift": "Выбрать подарок",
    "certificate": "Запланировать использование сертификата",
    "subscription": "Проверить актуальность подписки",
    "payment": "Оплатить до дедлайна",
    "logistics": "Выполнить задачу",
}


@dataclass
class EnrichedReminder:
    reminder: Reminder
    entity: Entity
    next_action: str
    shortlist: list[str] = field(default_factory=list)
    note: str | None = None
    resources: list[Resource] = field(default_factory=list)
    missing_checklist: list[str] = field(default_factory=list)


async def enrich_reminder(
    reminder: Reminder,
    entity: Entity,
    db: AsyncSession,
) -> EnrichedReminder:
    """Add next_action, resources, missing checklist items and shortlist to a reminder."""
    resources = await _get_resources(entity.id, db)
    missing_checklist = await _get_open_checklist(entity.id, db)

    today = date.today()
    days_left = (entity.end_date - today).days if entity.end_date else None

    need_openai = entity.type in ("gift", "certificate") or days_left is not None
    if need_openai:
        suggestion = await _call_openai(entity, days_left, missing_checklist, resources)
    else:
        suggestion = {
            "next_action": _DEFAULT_NEXT_ACTION.get(entity.type, "Выполнить задачу"),
            "shortlist": [],
            "note": None,
        }

    return EnrichedReminder(
        reminder=reminder,
        entity=entity,
        next_action=suggestion.get("next_action", _DEFAULT_NEXT_ACTION.get(entity.type, "")),
        shortlist=suggestion.get("shortlist", []),
        note=suggestion.get("note"),
        resources=resources,
        missing_checklist=missing_checklist,
    )


async def build_proactive_hints(db: AsyncSession) -> list[str]:
    """Scan active entities and return proactive hint strings."""
    hints: list[str] = []
    today = date.today()

    result = await db.execute(select(Entity).where(Entity.status.in_(["active", "expiring_soon"])))
    entities = result.scalars().all()

    # Hint: multiple certificates, earliest is closest to expiry
    certificates = [e for e in entities if e.type == "certificate" and e.end_date]
    if len(certificates) > 1:
        certificates.sort(key=lambda e: e.end_date)  # type: ignore[arg-type, return-value]
        earliest = certificates[0]
        days = (earliest.end_date - today).days  # type: ignore[operator]
        hints.append(
            f"У тебя {len(certificates)} сертификата. "
            f"«{earliest.name}» сгорает раньше всех — через {days}д."
        )

    # Hint: subscription not recently updated
    for entity in entities:
        if entity.type == "subscription":
            updated = entity.updated_at
            if updated:
                import datetime

                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=datetime.UTC)
                stale_days = (datetime.datetime.now(tz=datetime.UTC) - updated).days
                if stale_days > 60:
                    hints.append(f"Подписка «{entity.name}» не обновлялась {stale_days}д.")

    return hints


async def _get_resources(entity_id: int, db: AsyncSession) -> list[Resource]:
    result = await db.execute(select(Resource).where(Resource.entity_id == entity_id))
    return list(result.scalars().all())


async def _get_open_checklist(entity_id: int, db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.entity_id == entity_id,
            ChecklistItem.status == "open",
        )
    )
    return [item.text for item in result.scalars().all()]


async def _call_openai(
    entity: Entity,
    days_left: int | None,
    missing_checklist: list[str],
    resources: list[Resource],
) -> dict[str, Any]:
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    context: dict[str, Any] = {
        "type": entity.type,
        "name": entity.name,
        "end_date": entity.end_date.isoformat() if entity.end_date else None,
        "days_left": days_left,
        "notes": entity.notes,
        "checklist_open": missing_checklist,
        "resources": [{"filename": r.filename, "url": r.url, "type": r.type} for r in resources],
    }

    try:
        response = await _client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)  # type: ignore[no-any-return]
    except Exception:
        logger.exception("Suggestion OpenAI call failed for entity %d", entity.id)
        return {
            "next_action": _DEFAULT_NEXT_ACTION.get(entity.type, "Выполнить задачу"),
            "shortlist": [],
            "note": None,
        }
