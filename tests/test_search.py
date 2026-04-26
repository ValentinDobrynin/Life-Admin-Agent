from __future__ import annotations

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
