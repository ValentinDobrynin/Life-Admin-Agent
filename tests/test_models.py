"""Smoke tests for ORM models — schema creates, basic CRUD works."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import (
    BOT_STATES,
    DOCUMENT_KINDS,
    DOCUMENT_STATUSES,
    PERSON_RELATIONS,
    Address,
    BotState,
    Document,
    Note,
    Person,
    Vehicle,
)


@pytest.fixture()
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_person_create(session: AsyncSession) -> None:
    p = Person(full_name="Anna Ivanova", relation="жена")
    session.add(p)
    await session.commit()
    await session.refresh(p)
    assert p.id is not None
    assert p.fields == {}
    assert p.tags == []
    assert p.files == []


async def test_document_with_owner_and_status(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()

    d = Document(
        kind="passport",
        title="Паспорт РФ",
        owner_person_id=p.id,
        expires_at=date(2030, 5, 12),
        fields={"series": "4514", "number": "123456"},
        tags=["паспорт", "passport"],
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    assert d.status == "active"
    assert d.fields["series"] == "4514"
    assert "паспорт" in d.tags


async def test_document_status_replaced(session: AsyncSession) -> None:
    d = Document(kind="passport", title="x", status="replaced")
    session.add(d)
    await session.commit()

    result = await session.execute(select(Document).where(Document.status == "active"))
    assert result.scalar_one_or_none() is None


async def test_vehicle_address_note(session: AsyncSession) -> None:
    v = Vehicle(make="Toyota", model="Camry", plate="A123BC")
    a = Address(label="дом", city="Москва", street="Тверская 5-12")
    n = Note(title="ключи от дачи", body="в верхнем ящике")
    session.add_all([v, a, n])
    await session.commit()
    assert v.id and a.id and n.id


async def test_bot_state_upsert(session: AsyncSession) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=10)
    bs = BotState(
        chat_id=12345, state="awaiting_more_photos", context={"draft": {}}, expires_at=expires
    )
    session.add(bs)
    await session.commit()
    await session.refresh(bs)
    assert bs.state == "awaiting_more_photos"


def test_enums_documented() -> None:
    assert "passport" in DOCUMENT_KINDS
    assert "ticket" in DOCUMENT_KINDS
    assert "active" in DOCUMENT_STATUSES
    assert "жена" in PERSON_RELATIONS
    assert "awaiting_ocr_verification" in BOT_STATES
