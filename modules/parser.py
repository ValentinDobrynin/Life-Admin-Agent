from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "entity_parser.txt"
_SOUL_PATH = Path(__file__).parent.parent / "prompts" / "soul.txt"


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _load_soul() -> str:
    return _SOUL_PATH.read_text(encoding="utf-8")


class EntityData:
    def __init__(
        self,
        type: str,
        name: str,
        start_date: str | None,
        end_date: str | None,
        notes: str | None,
        checklist_items: list[dict[str, Any]],
        reminder_rules: list[dict[str, Any]],
    ) -> None:
        self.type = type
        self.name = name
        self.start_date = start_date
        self.end_date = end_date
        self.notes = notes
        self.checklist_items = checklist_items
        self.reminder_rules = reminder_rules


async def extract_entity(raw_text: str) -> EntityData:
    """Extract structured entity data from raw user text via OpenAI."""
    system_prompt = _load_soul() + "\n\n---\n\n" + _load_prompt()

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
        data = json.loads(content)
        return _parse_response(data)

    except Exception:
        logger.exception("Entity parsing failed for input: %.100s", raw_text)
        return _fallback_entity(raw_text)


def _parse_response(data: dict[str, Any]) -> EntityData:
    return EntityData(
        type=data.get("type", "logistics"),
        name=data.get("name", "Новый объект"),
        start_date=data.get("start_date"),
        end_date=data.get("end_date"),
        notes=data.get("notes"),
        checklist_items=data.get("checklist_items", []),
        reminder_rules=data.get("reminder_rules", []),
    )


def _fallback_entity(raw_text: str) -> EntityData:
    """Fallback when OpenAI fails: save as logistics with original text."""
    return EntityData(
        type="logistics",
        name=raw_text[:100],
        start_date=None,
        end_date=None,
        notes=raw_text,
        checklist_items=[],
        reminder_rules=[{"rule": "digest_only"}],
    )


def detect_intent(text: str) -> str:
    """Detect user intent from message text.

    Returns:
        'reference_add'   — user wants to add data to the reference directory
        'generate'        — user wants to generate a text using reference data
        'checklist_trip'  — user wants a packing checklist for a trip
        'weather_query'   — user asks about weather in a destination
        'entity'          — default: create a life admin entity
    """
    text_lower = text.lower()

    # weather_query — before entity to avoid saving as life admin item
    weather_triggers = [
        "какая погода",
        "какая будет погода",
        "погода в ",
        "погода на ",
        "будет дождь",
        "температура в ",
        "прогноз погоды",
    ]
    if any(t in text_lower for t in weather_triggers):
        return "weather_query"

    # checklist_trip — must check before entity to avoid misclassification
    checklist_triggers = [
        "собери чеклист",
        "составь чеклист",
        "список вещей",
        "что взять",
        "что упаковать",
        "что брать в поездку",
        "собери список вещей",
        "помоги собрать чемодан",
    ]
    if any(t in text_lower for t in checklist_triggers):
        return "checklist_trip"

    generate_keywords = [
        "сделай сообщение",
        "напиши сообщение",
        "составь сообщение",
        "сделай текст",
        "напиши текст",
        "составь текст",
        "сделай заявление",
        "напиши заявление",
        "сгенерируй",
        "подготовь текст",
    ]
    if any(kw in text_lower for kw in generate_keywords):
        return "generate"

    # Only explicit imperatives — avoids false positives on entity messages
    # like "загранпаспорт истекает через 3 месяца"
    reference_keywords = [
        "добавь в справочник",
        "сохрани в справочник",
        "добавь машину",
        "добавь адрес",
        "добавь паспорт",
        "добавь человека",
    ]
    if any(kw in text_lower for kw in reference_keywords):
        return "reference_add"

    return "entity"


async def parse_trip_checklist_request(text: str) -> dict[str, str]:
    """Extract trip parameters from user checklist request.

    Returns dict with keys: destination, dates, trip_type, travelers.
    Values may be empty strings if not detected.
    """
    system = (
        "Извлеки параметры поездки из текста пользователя. "
        "Верни JSON:\n"
        "{\n"
        '  "destination": "город или страна",\n'
        '  "dates": "даты или количество дней (пустая строка если не указано)",\n'
        '  "trip_type": "море | горы | город | командировка | отдых",\n'
        '  "travelers": "один | с женой | с семьёй | с детьми"\n'
        "}\n"
        "Отвечай ТОЛЬКО JSON. Если параметр не упомянут — пустая строка."
    )
    try:
        response = await _get_client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw: dict[str, str] = json.loads(response.choices[0].message.content or "{}")
        return raw
    except Exception:
        logger.exception("parse_trip_checklist_request failed for: %.100s", text)
        return {}


def is_send_file_request(text: str) -> bool:
    """Return True if user is asking the bot to send a file from the reference directory.

    Uses narrow triggers only to avoid false positives on general imperatives.
    """
    lower = text.lower().strip()
    triggers = [
        "пришли ",
        "скинь ",
        "дай скан",
        "дай фото",
        "где мои права",
        "где мой паспорт",
    ]
    return any(lower.startswith(t) or t in lower for t in triggers)
