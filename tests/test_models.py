from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import ChecklistItem, Contact, Entity, EventLog, Reminder, Resource


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def test_create_entity(db_session: AsyncSession) -> None:
    entity = Entity(type="certificate", name="SPA сертификат", status="active")
    db_session.add(entity)
    await db_session.commit()
    await db_session.refresh(entity)

    assert entity.id is not None
    assert entity.name == "SPA сертификат"
    assert entity.status == "active"
    assert entity.created_at is not None


async def test_entity_with_reminder(db_session: AsyncSession) -> None:
    from datetime import date

    entity = Entity(type="document", name="Страховка", status="active")
    db_session.add(entity)
    await db_session.commit()

    reminder = Reminder(
        entity_id=entity.id,
        trigger_date=date(2026, 6, 1),
        rule="before_N_days",
        status="pending",
    )
    db_session.add(reminder)
    await db_session.commit()
    await db_session.refresh(reminder)

    assert reminder.id is not None
    assert reminder.entity_id == entity.id


async def test_entity_with_checklist(db_session: AsyncSession) -> None:
    entity = Entity(type="trip", name="Поездка в Турцию", status="active")
    db_session.add(entity)
    await db_session.commit()

    item = ChecklistItem(entity_id=entity.id, text="Оформить страховку", position=0)
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    assert item.id is not None
    assert item.status == "open"


async def test_entity_with_resource(db_session: AsyncSession) -> None:
    entity = Entity(type="document", name="Полис", status="active")
    db_session.add(entity)
    await db_session.commit()

    resource = Resource(entity_id=entity.id, type="file", filename="policy.pdf", r2_key="abc/123")
    db_session.add(resource)
    await db_session.commit()
    await db_session.refresh(resource)

    assert resource.id is not None
    assert resource.r2_key == "abc/123"


async def test_event_log_without_entity(db_session: AsyncSession) -> None:
    log = EventLog(action="raw_input", payload={"text": "тест"})
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)

    assert log.id is not None
    assert log.entity_id is None
    assert log.payload == {"text": "тест"}


async def test_contact_with_gift_history(db_session: AsyncSession) -> None:
    from datetime import date

    contact = Contact(
        name="Настя",
        birthday=date(1990, 5, 15),
        gift_history=[{"year": 2025, "gift": "книга"}],
        entity_ids=[],
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)

    assert contact.id is not None
    assert len(contact.gift_history) == 1


async def test_all_entity_types_valid(db_session: AsyncSession) -> None:
    types = ["document", "trip", "gift", "certificate", "subscription", "payment", "logistics"]
    for t in types:
        entity = Entity(type=t, name=f"Test {t}", status="active")
        db_session.add(entity)
    await db_session.commit()
