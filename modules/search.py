"""Retrieval over the personal storage. LLM-only, no embeddings."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Address, Document, Note, Person, Vehicle

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

Action = Literal["send_files", "send_text", "send_both", "clarify"]


@dataclass
class RetrieveResult:
    ids: list[dict[str, Any]]
    action: Action
    clarify_question: str | None = None


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _load_soul() -> str:
    soul_path = PROMPTS_DIR / "soul.txt"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


def _trim(value: Any, length: int = 60) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s if len(s) <= length else s[: length - 1] + "…"


_TICKET_CITY_SHORTS: dict[str, str] = {
    "москва": "МСК",
    "санкт-петербург": "СПб",
    "санкт петербург": "СПб",
    "спб": "СПб",
    "питер": "СПб",
}


def _short_city(value: Any) -> str:
    if not value:
        return ""
    s = str(value).split(",")[0].strip()
    return _TICKET_CITY_SHORTS.get(s.lower(), s)


def _fmt_short_date(value: Any) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) < 10:
        return ""
    try:
        y, m, d = s[:10].split("-")
        return f"{int(d):02d}.{int(m):02d}"
    except ValueError:
        return s[:10]


def _passenger_names(fields: dict[str, Any]) -> list[str]:
    passengers = fields.get("passengers")
    if not isinstance(passengers, list):
        return []
    names: list[str] = []
    for p in passengers:
        if isinstance(p, dict):
            name = p.get("full_name") or p.get("name")
            if name:
                names.append(str(name))
    return names


def _summary_ticket(d: Document) -> str:
    f = d.fields or {}
    category = f.get("category")
    subtype = f.get("subtype")
    carrier = f.get("carrier_or_venue")
    passengers = _passenger_names(f)
    pax_part = f"{len(passengers)} пасс." if passengers else ""
    if category == "transport":
        head = str(carrier or "Билет")
        route_parts = [_short_city(f.get("from")), _short_city(f.get("to"))]
        arrow = "↔" if f.get("round_trip") else "→"
        route = arrow.join(p for p in route_parts if p)
        when = _fmt_short_date(f.get("departure_at"))
        ret = _fmt_short_date(f.get("return_arrival_at") or f.get("return_departure_at"))
        if ret and when and ret != when:
            when = f"{when}-{ret}"
        parts = [head]
        if route:
            parts.append(route)
        if when:
            parts.append(when)
        if pax_part:
            parts.append(pax_part)
        return " · ".join(parts)
    if category == "event":
        head = str(carrier or f.get("venue") or "Событие")
        when = _fmt_short_date(f.get("event_at"))
        parts = [head]
        if when:
            parts.append(when)
        if pax_part:
            parts.append(pax_part)
        return " · ".join(parts)
    parts = [str(d.title or subtype or "Билет")]
    if pax_part:
        parts.append(pax_part)
    return " · ".join(parts)


def _summary_document(d: Document) -> str:
    if d.kind == "ticket":
        return _summary_ticket(d)
    f = d.fields or {}
    parts = [str(d.title)]
    if d.expires_at:
        parts.append(f"до {d.expires_at}")
    if "number" in f:
        parts.append(f"№ {f['number']}")
    if "series" in f:
        parts.insert(1, f"сер.{f['series']}")
    return " · ".join(parts)


def _summary_person(p: Person) -> str:
    parts = [p.full_name or "—"]
    if p.relation:
        parts.append(p.relation)
    if p.fields and p.fields.get("phone"):
        parts.append(str(p.fields["phone"]))
    return " · ".join(parts)


def _summary_vehicle(v: Vehicle) -> str:
    parts = [f"{v.make or ''} {v.model or ''}".strip() or "Машина"]
    if v.plate:
        parts.append(v.plate)
    if v.vin:
        parts.append(f"VIN {v.vin}")
    return " · ".join(parts)


def _summary_address(a: Address) -> str:
    parts: list[str] = []
    if a.label:
        parts.append(str(a.label))
    if a.city:
        parts.append(str(a.city))
    if a.street:
        parts.append(str(a.street))
    return ", ".join(parts) if parts else "Адрес"


def _summary_note(n: Note) -> str:
    body = n.body or (n.fields or {}).get("body") or ""
    return _trim(f"{n.title} · {body}", length=120) if body else str(n.title)


async def _person_lookup(db: AsyncSession) -> dict[int, Person]:
    result = await db.execute(select(Person))
    return {p.id: p for p in result.scalars().all()}


def _compute_document_ordinals(docs: Sequence[Document]) -> dict[int, int]:
    """For each (kind, owner_person_id, passport_type) bucket, assign 1..N ordered
    by issued_at ascending (None last). 1 = oldest = "первый".

    Tickets are intentionally skipped: each trip/event is a standalone record and
    ordinal numbering over tickets does not match user mental model.
    """
    buckets: dict[tuple[str | None, int | None, str | None], list[Document]] = {}
    for d in docs:
        if d.kind == "ticket":
            continue
        passport_type = (d.fields or {}).get("passport_type") if d.kind == "passport" else None
        key = (d.kind, d.owner_person_id, passport_type)
        buckets.setdefault(key, []).append(d)

    ordinals: dict[int, int] = {}
    for bucket_docs in buckets.values():
        bucket_docs.sort(
            key=lambda x: (x.issued_at is None, x.issued_at or 0, x.id),
        )
        for i, d in enumerate(bucket_docs, start=1):
            ordinals[d.id] = i
    return ordinals


async def build_index(db: AsyncSession) -> list[dict[str, Any]]:
    """Build a compact index of all active records for retrieval LLM."""
    persons = await _person_lookup(db)
    index: list[dict[str, Any]] = []

    for p in persons.values():
        index.append(
            {
                "type": "person",
                "id": p.id,
                "title": p.full_name,
                "kind": None,
                "owner_relation": p.relation,
                "owner_full_name": p.full_name,
                "tags": p.tags or [],
                "summary": _summary_person(p),
            }
        )

    docs = (
        (
            await db.execute(
                select(Document).where(Document.status == "active").order_by(Document.id)
            )
        )
        .scalars()
        .all()
    )
    ordinals = _compute_document_ordinals(docs)
    for d in docs:
        owner = persons.get(d.owner_person_id) if d.owner_person_id else None
        fields = d.fields or {}
        item: dict[str, Any] = {
            "type": "document",
            "id": d.id,
            "title": d.title,
            "kind": d.kind,
            "owner_relation": owner.relation if owner else None,
            "owner_full_name": owner.full_name if owner else None,
            "tags": d.tags or [],
            "summary": _summary_document(d),
            "passport_type": fields.get("passport_type") if d.kind == "passport" else None,
            "country": fields.get("country"),
            "ordinal": ordinals.get(d.id),
            "issued_at": d.issued_at.isoformat() if d.issued_at else None,
        }
        if d.kind == "ticket":
            item["subtype"] = fields.get("subtype")
            item["from"] = fields.get("from")
            item["to"] = fields.get("to")
            item["departure_at"] = fields.get("departure_at")
            item["event_at"] = fields.get("event_at")
            item["passenger_names"] = _passenger_names(fields)
        index.append(item)

    vehs = (await db.execute(select(Vehicle).order_by(Vehicle.id))).scalars().all()
    for v in vehs:
        owner = persons.get(v.owner_person_id) if v.owner_person_id else None
        index.append(
            {
                "type": "vehicle",
                "id": v.id,
                "title": f"{v.make or ''} {v.model or ''}".strip() or "Машина",
                "kind": None,
                "owner_relation": owner.relation if owner else None,
                "owner_full_name": owner.full_name if owner else None,
                "tags": v.tags or [],
                "summary": _summary_vehicle(v),
            }
        )

    addrs = (await db.execute(select(Address).order_by(Address.id))).scalars().all()
    for a in addrs:
        person = persons.get(a.person_id) if a.person_id else None
        index.append(
            {
                "type": "address",
                "id": a.id,
                "title": a.label or a.city or "Адрес",
                "kind": None,
                "owner_relation": person.relation if person else None,
                "owner_full_name": person.full_name if person else None,
                "tags": a.tags or [],
                "summary": _summary_address(a),
            }
        )

    notes = (await db.execute(select(Note).order_by(Note.id))).scalars().all()
    for n in notes:
        index.append(
            {
                "type": "note",
                "id": n.id,
                "title": n.title,
                "kind": None,
                "owner_relation": None,
                "owner_full_name": None,
                "tags": n.tags or [],
                "summary": _summary_note(n),
            }
        )

    return index


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


async def _existing_persons_for_retrieve(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(Person))
    out: list[dict[str, Any]] = []
    for p in result.scalars().all():
        out.append(
            {
                "id": p.id,
                "full_name": p.full_name,
                "relation": p.relation,
            }
        )
    return out


async def resolve_query(query: str, db: AsyncSession) -> RetrieveResult:
    """Run retrieve.txt over (query, index) and return the parsed RetrieveResult."""
    index = await build_index(db)
    if not index:
        return RetrieveResult(ids=[], action="send_text")

    soul = _load_soul()
    retrieve_prompt = _load_prompt("retrieve.txt")
    system = retrieve_prompt
    if soul:
        system = soul + "\n\n---\n\n" + retrieve_prompt

    existing_persons = await _existing_persons_for_retrieve(db)
    payload = {"query": query, "existing_persons": existing_persons, "index": index}

    client = _client()
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("retrieve LLM call failed")
        return RetrieveResult(ids=[], action="send_text")

    content = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.exception("retrieve returned non-JSON: %s", content[:200])
        return RetrieveResult(ids=[], action="send_text")

    raw_ids = parsed.get("ids") or []
    ids: list[dict[str, Any]] = []
    for item in raw_ids:
        if (
            isinstance(item, dict)
            and "type" in item
            and "id" in item
            and item["type"] in {"person", "document", "vehicle", "address", "note"}
        ):
            ids.append({"type": item["type"], "id": int(item["id"])})

    action = parsed.get("action") or "send_text"
    if action not in {"send_files", "send_text", "send_both", "clarify"}:
        action = "send_text"

    return RetrieveResult(
        ids=ids,
        action=cast(Action, action),
        clarify_question=parsed.get("clarify_question"),
    )


# ---------------------------------------------------------------------------
# Record fetch
# ---------------------------------------------------------------------------


_TYPE_TO_MODEL: dict[str, type[Any]] = {
    "person": Person,
    "document": Document,
    "vehicle": Vehicle,
    "address": Address,
    "note": Note,
}


async def get_record(db: AsyncSession, record_type: str, record_id: int) -> dict[str, Any] | None:
    """Fetch a record by (type, id) and return as a dict for cards.render_record_card."""
    model = _TYPE_TO_MODEL.get(record_type)
    if model is None:
        return None
    result = await db.execute(select(model).where(model.id == record_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        return None
    return _to_dict(obj)


def _to_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in obj.__table__.columns:
        out[col.name] = getattr(obj, col.name)
    return out
