from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=settings.openai_api_key)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "entity_parser.txt"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


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
    system_prompt = _load_prompt()

    try:
        response = await _client.chat.completions.create(
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
