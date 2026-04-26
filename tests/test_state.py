from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import BotState
from modules import state


@pytest.fixture()
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_set_and_get_state(session: AsyncSession) -> None:
    bs = await state.set_state(session, 111, "awaiting_more_photos", {"x": 1})
    assert bs.state == "awaiting_more_photos"

    fetched = await state.get_state(session, 111)
    assert fetched is not None
    assert fetched.context == {"x": 1}


async def test_set_state_overwrites(session: AsyncSession) -> None:
    await state.set_state(session, 222, "awaiting_more_photos", {"a": 1})
    await state.set_state(session, 222, "awaiting_ocr_verification", {"a": 2})

    fetched = await state.get_state(session, 222)
    assert fetched is not None
    assert fetched.state == "awaiting_ocr_verification"
    assert fetched.context == {"a": 2}


async def test_clear_state(session: AsyncSession) -> None:
    await state.set_state(session, 333, "awaiting_more_photos", {})
    await state.clear_state(session, 333)
    assert await state.get_state(session, 333) is None


async def test_expired_state_returns_none(session: AsyncSession) -> None:
    expires = datetime.now(UTC) - timedelta(minutes=1)
    bs = BotState(chat_id=444, state="awaiting_more_photos", context={}, expires_at=expires)
    session.add(bs)
    await session.commit()

    assert await state.get_state(session, 444) is None
    result = await session.execute(select(BotState).where(BotState.chat_id == 444))
    assert result.scalar_one_or_none() is None
