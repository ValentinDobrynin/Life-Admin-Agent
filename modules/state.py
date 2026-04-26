from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import BotState

DEFAULT_TTL_MINUTES = 30


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def get_state(db: AsyncSession, chat_id: int) -> BotState | None:
    """Return active (non-expired) state for chat or None.

    Expired states are auto-deleted on read.
    """
    result = await db.execute(select(BotState).where(BotState.chat_id == chat_id))
    state = result.scalar_one_or_none()
    if state is None:
        return None
    if _aware(state.expires_at) <= _now():
        await db.delete(state)
        await db.commit()
        return None
    return state


async def set_state(
    db: AsyncSession,
    chat_id: int,
    state: str,
    context: dict[str, Any],
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> BotState:
    """Upsert bot_state for chat_id."""
    expires = _now() + timedelta(minutes=ttl_minutes)
    existing = await db.execute(select(BotState).where(BotState.chat_id == chat_id))
    bs = existing.scalar_one_or_none()
    if bs is None:
        bs = BotState(
            chat_id=chat_id,
            state=state,
            context=context,
            expires_at=expires,
        )
        db.add(bs)
    else:
        bs.state = state
        bs.context = context
        bs.expires_at = expires
    await db.commit()
    await db.refresh(bs)
    return bs


async def clear_state(db: AsyncSession, chat_id: int) -> None:
    result = await db.execute(select(BotState).where(BotState.chat_id == chat_id))
    bs = result.scalar_one_or_none()
    if bs is not None:
        await db.delete(bs)
        await db.commit()
