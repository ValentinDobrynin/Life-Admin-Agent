from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import Document, Person
from modules import notifications


@pytest.fixture()
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_send_text_calls_client() -> None:
    with patch("modules.notifications.client.send_message", AsyncMock()) as m:
        await notifications.send_text(123, "hello")
    m.assert_awaited_once()


async def test_send_files_two_photos_uses_media_group() -> None:
    files = [
        {"r2_key": "a.jpg", "filename": "a.jpg", "content_type": "image/jpeg"},
        {"r2_key": "b.jpg", "filename": "b.jpg", "content_type": "image/jpeg"},
    ]

    with (
        patch("modules.notifications.storage.download_file", return_value=b"x"),
        patch("modules.notifications.client.send_media_group", AsyncMock()) as mg,
        patch("modules.notifications.client.send_photo", AsyncMock()) as sp,
    ):
        await notifications.send_files(1, files, caption="cap")
    mg.assert_awaited_once()
    sp.assert_not_called()


async def test_send_files_pdf_falls_through_to_send_document() -> None:
    files = [{"r2_key": "x.pdf", "filename": "x.pdf", "content_type": "application/pdf"}]
    with (
        patch("modules.notifications.storage.download_file", return_value=b"x"),
        patch("modules.notifications.client.send_document", AsyncMock()) as sd,
    ):
        await notifications.send_files(1, files, caption="cap")
    sd.assert_awaited_once()


async def test_send_expiry_digest_sends_only_active_in_window(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()

    today = date.today()
    soon = Document(
        kind="passport",
        title="Скоро",
        owner_person_id=p.id,
        expires_at=today + timedelta(days=10),
        status="active",
    )
    far = Document(
        kind="visa",
        title="Не скоро",
        owner_person_id=p.id,
        expires_at=today + timedelta(days=400),
        status="active",
    )
    replaced = Document(
        kind="passport",
        title="Старый",
        owner_person_id=p.id,
        expires_at=today + timedelta(days=5),
        status="replaced",
    )
    session.add_all([soon, far, replaced])
    await session.commit()

    captured: list[tuple[str, Any]] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append((text, keyboard))

    with patch("modules.notifications.send_text", fake_send):
        await notifications.send_expiry_digest(session)

    assert len(captured) == 1
    text, keyboard = captured[0]
    assert "Скоро" in text
    assert "Не скоро" not in text
    assert "Старый" not in text
    assert keyboard is not None


async def test_send_expiry_digest_skips_when_empty(session: AsyncSession) -> None:
    captured: list[Any] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    with patch("modules.notifications.send_text", fake_send):
        await notifications.send_expiry_digest(session)
    assert captured == []
