from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from sqlalchemy import or_, select
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


_TRIP_CHECKLIST_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "trip_checklist.txt"

# WMO weather code → Russian description
_WMO_DESCRIPTIONS: dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "лёгкая морось",
    53: "морось",
    55: "сильная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    80: "ливневые дожди",
    81: "дожди",
    82: "сильные ливни",
    95: "гроза",
    99: "гроза с градом",
}


def _wmo_to_text(code: int) -> str:
    for threshold, desc in sorted(_WMO_DESCRIPTIONS.items(), reverse=True):
        if code >= threshold:
            return desc
    return "переменная погода"


async def _fetch_trip_context(destination: str, dates: str) -> dict[str, str]:
    """Fetch weather (Open-Meteo) and destination notes (DuckDuckGo).

    Returns dict with keys: weather, destination_notes.
    Falls back gracefully on any error.
    """
    weather = "данные о погоде недоступны"
    destination_notes = ""

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Geocoding via Open-Meteo
        try:
            geo_resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": destination, "count": 1, "language": "ru", "format": "json"},
            )
            geo_data = geo_resp.json()
            results = geo_data.get("results", [])
            if results:
                lat = results[0]["latitude"]
                lon = results[0]["longitude"]

                # 2. Weather forecast (7 days from today)
                wx_resp = await client.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "daily": "temperature_2m_max,temperature_2m_min,weathercode",
                        "timezone": "auto",
                        "forecast_days": 7,
                    },
                )
                wx_data = wx_resp.json()
                daily = wx_data.get("daily", {})
                temps_max = daily.get("temperature_2m_max", [])
                temps_min = daily.get("temperature_2m_min", [])
                codes = daily.get("weathercode", [])

                if temps_max and codes:
                    avg_max = round(sum(temps_max) / len(temps_max))
                    avg_min = round(sum(temps_min) / len(temps_min)) if temps_min else avg_max - 5
                    dominant_code = max(set(codes), key=codes.count)
                    weather_desc = _wmo_to_text(dominant_code)
                    weather = (
                        f"{weather_desc}, температура {avg_min}–{avg_max}°C"
                        f"{' (прогноз на период поездки)' if dates else ''}"
                    )
        except Exception:
            logger.warning("Open-Meteo fetch failed for destination=%s", destination)

        # 3. Destination notes via DuckDuckGo Instant Answer
        try:
            ddg_query = f"что нужно знать туристу {destination} особенности"
            ddg_resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": ddg_query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            )
            ddg_data = ddg_resp.json()
            abstract = ddg_data.get("AbstractText", "")
            topics = ddg_data.get("RelatedTopics", [])
            snippets = [abstract] + [t.get("Text", "") for t in topics[:3] if isinstance(t, dict)]
            destination_notes = " ".join(s for s in snippets if s)[:600]
        except Exception:
            logger.warning("DuckDuckGo fetch failed for destination=%s", destination)

    return {"weather": weather, "destination_notes": destination_notes}


async def generate_trip_checklist(
    trip_params: dict[str, str],
    db: AsyncSession,
) -> tuple[list[str], Entity | None]:
    """Generate packing checklist for a trip.

    Steps:
    1. Fetch weather + destination notes from external APIs
    2. Find matching trip entity in DB (by destination name)
    3. Generate checklist via OpenAI using trip_checklist.txt prompt
    4. Save checklist items to entity (prefixed [авто]) if entity found

    Returns (checklist_items, entity_or_none).
    """
    destination = trip_params.get("destination", "")
    dates = trip_params.get("dates", "")
    trip_type = trip_params.get("trip_type") or "отдых"
    travelers = trip_params.get("travelers") or "один"

    context = await _fetch_trip_context(destination, dates)

    entity: Entity | None = None
    if destination:
        result = await db.execute(
            select(Entity)
            .where(
                Entity.type == "trip",
                Entity.status == "active",
                or_(
                    Entity.name.ilike(f"%{destination}%"),
                    Entity.notes.ilike(f"%{destination}%"),
                ),
            )
            .order_by(Entity.start_date.asc())
            .limit(1)
        )
        entity = result.scalar_one_or_none()

    prompt_template = _TRIP_CHECKLIST_PROMPT_PATH.read_text(encoding="utf-8")
    # Strip comment header lines (lines starting with #)
    prompt_lines = [ln for ln in prompt_template.splitlines() if not ln.startswith("#")]
    prompt = "\n".join(prompt_lines).strip()
    prompt = (
        prompt.replace("{destination}", destination or "неизвестно")
        .replace("{dates}", dates or "неизвестно")
        .replace("{trip_type}", trip_type)
        .replace("{weather}", context["weather"])
        .replace("{destination_notes}", context["destination_notes"] or "нет данных")
        .replace("{travelers}", travelers)
    )

    try:
        response = await _client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = response.choices[0].message.content or ""
        items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    except Exception:
        logger.exception("generate_trip_checklist OpenAI call failed")
        return [], entity

    if entity and items:
        # Remove previously auto-generated items to avoid duplicates
        existing = await db.execute(
            select(ChecklistItem).where(
                ChecklistItem.entity_id == entity.id,
                ChecklistItem.text.like("[авто]%"),  # noqa: E501
            )
        )
        for old_item in existing.scalars().all():
            await db.delete(old_item)

        for i, item_text in enumerate(items):
            db.add(
                ChecklistItem(
                    entity_id=entity.id,
                    text=f"[авто] {item_text}",
                    position=1000 + i,
                    status="open",
                )
            )
        await db.commit()
        logger.info("Saved %d checklist items to entity #%d", len(items), entity.id)

    return items, entity


async def ensure_trip_checklist(entity: Entity, db: AsyncSession) -> bool:
    """Generate and attach checklist to trip entity if not already present.

    Called proactively from check_reminders_job before sending trip reminder.
    Returns True if a new checklist was generated.
    """
    existing = await db.execute(
        select(ChecklistItem).where(ChecklistItem.entity_id == entity.id).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False

    trip_params = {
        "destination": entity.name,
        "dates": str(entity.start_date) if entity.start_date else "",
        "trip_type": "отдых",
        "travelers": "один",
    }
    items, _ = await generate_trip_checklist(trip_params, db)
    return len(items) > 0


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
