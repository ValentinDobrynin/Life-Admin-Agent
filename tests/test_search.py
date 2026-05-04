from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import Address, Document, Person, Vehicle
from modules import search


@pytest.fixture()
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_build_index_collects_all_active(session: AsyncSession) -> None:
    p = Person(full_name="Анна", relation="жена")
    session.add(p)
    await session.flush()

    d_active = Document(kind="passport", title="Паспорт", owner_person_id=p.id, status="active")
    d_replaced = Document(kind="passport", title="Старый", owner_person_id=p.id, status="replaced")
    v = Vehicle(make="Toyota", model="Camry", plate="A1", owner_person_id=p.id)
    a = Address(label="дом", person_id=p.id, city="Москва", street="Тверская")
    session.add_all([d_active, d_replaced, v, a])
    await session.commit()

    index = await search.build_index(session)
    types = [item["type"] for item in index]
    assert "person" in types
    assert types.count("document") == 1
    assert "vehicle" in types
    assert "address" in types

    doc_entry = next(x for x in index if x["type"] == "document")
    assert doc_entry["owner_relation"] == "жена"


async def test_resolve_query_parses_response(session: AsyncSession) -> None:
    p = Person(full_name="Анна", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(kind="passport", title="Паспорт", owner_person_id=p.id, status="active")
    session.add(d)
    await session.commit()

    fake_resp = MagicMock()
    fake_resp.choices = [
        MagicMock(
            message=MagicMock(
                content='{"ids":[{"type":"document","id":' + str(d.id) + '}],"action":"send_files"}'
            )
        )
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch("modules.search._client", return_value=fake_client):
        r = await search.resolve_query("пришли паспорт жены", session)

    assert r.action == "send_files"
    assert r.ids == [{"type": "document", "id": d.id}]


async def test_resolve_query_handles_invalid_json(session: AsyncSession) -> None:
    p = Person(full_name="X")
    session.add(p)
    await session.commit()

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="not json"))]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch("modules.search._client", return_value=fake_client):
        r = await search.resolve_query("...", session)

    assert r.action == "send_text"
    assert r.ids == []


async def test_resolve_query_empty_index(session: AsyncSession) -> None:
    r = await search.resolve_query("anything", session)
    assert r.ids == []
    assert r.action == "send_text"


async def test_get_record_returns_dict(session: AsyncSession) -> None:
    d = Document(kind="passport", title="Паспорт")
    session.add(d)
    await session.commit()
    await session.refresh(d)

    rec = await search.get_record(session, "document", d.id)
    assert rec is not None
    assert rec["kind"] == "passport"
    assert rec["title"] == "Паспорт"


async def test_get_record_invalid_type(session: AsyncSession) -> None:
    rec = await search.get_record(session, "unknown_type", 1)
    assert rec is None


async def test_build_index_assigns_ordinals_and_passport_type(session: AsyncSession) -> None:
    p = Person(full_name="Иван", relation="я")
    session.add(p)
    await session.flush()

    older = Document(
        kind="passport",
        title="Загран старый",
        owner_person_id=p.id,
        status="active",
        issued_at=date(2018, 8, 14),
        fields={"passport_type": "foreign"},
    )
    newer = Document(
        kind="passport",
        title="Загран новый",
        owner_person_id=p.id,
        status="active",
        issued_at=date(2024, 8, 30),
        fields={"passport_type": "foreign"},
    )
    internal = Document(
        kind="passport",
        title="Внутренний",
        owner_person_id=p.id,
        status="active",
        issued_at=date(2003, 8, 25),
        fields={"passport_type": "internal"},
    )
    session.add_all([older, newer, internal])
    await session.commit()

    index = await search.build_index(session)
    by_title = {item["title"]: item for item in index if item["type"] == "document"}

    assert by_title["Загран старый"]["ordinal"] == 1
    assert by_title["Загран новый"]["ordinal"] == 2
    assert by_title["Внутренний"]["ordinal"] == 1

    assert by_title["Загран старый"]["passport_type"] == "foreign"
    assert by_title["Внутренний"]["passport_type"] == "internal"


async def test_build_index_includes_ticket_fields(session: AsyncSession) -> None:
    t = Document(
        kind="ticket",
        title="Сапсан · Москва ↔ СПб · 22–25.05.2026",
        owner_person_id=None,
        status="active",
        fields={
            "category": "transport",
            "subtype": "train",
            "carrier_or_venue": "РЖД / ДОСС",
            "from": "Москва",
            "to": "Санкт-Петербург",
            "departure_at": "2026-05-22T07:30",
            "return_arrival_at": "2026-05-25T01:08",
            "round_trip": True,
            "passengers": [
                {"full_name": "Добрынин Валентин"},
                {"full_name": "Добрынина Анастасия"},
                {"full_name": "Раскоснов Максим"},
            ],
        },
        tags=["билет", "ticket", "сапсан"],
    )
    session.add(t)
    await session.commit()

    index = await search.build_index(session)
    ticket_item = next(i for i in index if i["type"] == "document" and i["kind"] == "ticket")

    assert ticket_item["subtype"] == "train"
    assert ticket_item["from"] == "Москва"
    assert ticket_item["to"] == "Санкт-Петербург"
    assert ticket_item["departure_at"] == "2026-05-22T07:30"
    assert ticket_item["passenger_names"] == [
        "Добрынин Валентин",
        "Добрынина Анастасия",
        "Раскоснов Максим",
    ]
    assert ticket_item["ordinal"] is None
    assert "МСК" in ticket_item["summary"] and "СПб" in ticket_item["summary"]
    assert "3 пасс." in ticket_item["summary"]


async def test_compute_document_ordinals_skips_tickets() -> None:
    docs = [
        Document(
            id=10,
            kind="ticket",
            title="Билет 1",
            owner_person_id=None,
            issued_at=date(2026, 1, 1),
            fields={},
        ),
        Document(
            id=11,
            kind="ticket",
            title="Билет 2",
            owner_person_id=None,
            issued_at=date(2026, 2, 1),
            fields={},
        ),
    ]
    out = search._compute_document_ordinals(docs)
    assert 10 not in out
    assert 11 not in out


async def test_resolve_query_passes_existing_persons(session: AsyncSession) -> None:
    p = Person(full_name="Анна Иванова", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(kind="ticket", title="Билет", status="active", fields={})
    session.add(d)
    await session.commit()

    captured: dict[str, object] = {}

    async def _capture(**kwargs):
        captured["messages"] = kwargs["messages"]
        return MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"ids":[],"action":"send_text"}'))]
        )

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_capture)

    with patch("modules.search._client", return_value=fake_client):
        await search.resolve_query("пришли билет жены", session)

    user_msg = captured["messages"][-1]["content"]
    assert "existing_persons" in user_msg
    assert "Анна Иванова" in user_msg


async def test_compute_document_ordinals_handles_missing_dates() -> None:
    """Records with no issued_at should still be ordered, and the bucket key
    should separate (kind, owner, passport_type) properly."""
    docs = [
        Document(
            id=1,
            kind="passport",
            title="A",
            owner_person_id=1,
            issued_at=None,
            fields={"passport_type": "foreign"},
        ),
        Document(
            id=2,
            kind="passport",
            title="B",
            owner_person_id=1,
            issued_at=date(2020, 1, 1),
            fields={"passport_type": "foreign"},
        ),
        Document(
            id=3,
            kind="passport",
            title="C",
            owner_person_id=2,
            issued_at=date(2019, 1, 1),
            fields={"passport_type": "foreign"},
        ),
    ]
    out = search._compute_document_ordinals(docs)
    assert out[2] == 1  # earlier dated wins for owner 1
    assert out[1] == 2  # None comes after dated
    assert out[3] == 1  # different owner — own bucket
