from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot import handlers
from config import settings
from database import Base
from models import Document, Person
from modules import ingest, search, state


@pytest.fixture()
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _msg(text: str) -> dict[str, Any]:
    return {
        "message": {
            "chat": {"id": settings.telegram_chat_id},
            "text": text,
        }
    }


def _cb(data: str) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "message": {"chat": {"id": settings.telegram_chat_id}},
        }
    }


# ---------------------------------------------------------------------------
# /help and /list
# ---------------------------------------------------------------------------


async def test_help_command_sends_text(session: AsyncSession) -> None:
    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    with patch("bot.handlers.notifications.send_text", fake_send):
        await handlers.handle_update(_msg("/help"), session)
    assert captured
    assert "Хранилище" in captured[0]


async def test_list_empty(session: AsyncSession) -> None:
    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    with patch("bot.handlers.notifications.send_text", fake_send):
        await handlers.handle_update(_msg("/list"), session)
    assert "пустое" in captured[0]


async def test_list_with_record(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.commit()

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    with patch("bot.handlers.notifications.send_text", fake_send):
        await handlers.handle_update(_msg("/list"), session)
    assert "Anna" in captured[0]


# ---------------------------------------------------------------------------
# /get and /delete
# ---------------------------------------------------------------------------


async def test_get_returns_record(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.commit()

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    async def fake_send_files(*args: Any, **kwargs: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.notifications.send_files", fake_send_files),
    ):
        await handlers.handle_update(_msg(f"/get person_{p.id}"), session)

    assert "Anna" in captured[0]


async def test_delete_removes_record(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.commit()
    pid = p.id

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.storage.delete_file"),
    ):
        await handlers.handle_update(_msg(f"/delete person_{pid}"), session)

    assert "Удалил" in captured[0]
    assert await session.get(Person, pid) is None


async def test_unauthorised_chat_ignored(session: AsyncSession) -> None:
    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    update = {
        "message": {
            "chat": {"id": settings.telegram_chat_id + 1},
            "text": "/help",
        }
    }
    with patch("bot.handlers.notifications.send_text", fake_send):
        await handlers.handle_update(update, session)
    assert captured == []


# ---------------------------------------------------------------------------
# Text routing — query path
# ---------------------------------------------------------------------------


async def test_text_query_path_runs_search(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(kind="passport", title="Паспорт", owner_person_id=p.id, status="active")
    session.add(d)
    await session.commit()

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    async def fake_send_files(*args: Any, **kwargs: Any) -> None:
        return None

    classified_query = {"intent": "query", "ingest": None, "query": "пришли паспорт"}
    retrieve_result = search.RetrieveResult(
        ids=[{"type": "document", "id": d.id}], action="send_text"
    )

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.notifications.send_files", fake_send_files),
        patch(
            "modules.ingest._classify",
            AsyncMock(return_value=classified_query),
        ),
        patch(
            "modules.search.resolve_query",
            AsyncMock(return_value=retrieve_result),
        ),
    ):
        await handlers.handle_update(_msg("пришли паспорт"), session)
    assert any("Паспорт" in t for t in captured)


async def test_text_query_no_results(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.commit()

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    classified = {"intent": "query", "ingest": None, "query": "x"}
    rresult = search.RetrieveResult(ids=[], action="send_text")
    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("modules.ingest._classify", AsyncMock(return_value=classified)),
        patch("modules.search.resolve_query", AsyncMock(return_value=rresult)),
    ):
        await handlers.handle_update(_msg("где паспорт"), session)
    assert any("Ничего не нашёл" in t for t in captured)


# ---------------------------------------------------------------------------
# Callback router
# ---------------------------------------------------------------------------


async def test_callback_verify_ok(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.commit()
    draft = {
        "type": "document",
        "kind": "snils",
        "owner_relation": "жена",
        "owner_full_name": "Anna",
        "fields": {},
        "tags": ["снилс"],
        "suggested_title": "СНИЛС",
        "files": [],
    }
    await state.set_state(
        session, settings.telegram_chat_id, "awaiting_ocr_verification", {"draft": draft}
    )

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    async def noop(*a: Any, **kw: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.client.answer_callback_query", AsyncMock()),
    ):
        await handlers.handle_update(_cb("verify_ok"), session)
    assert any("Сохранил" in t for t in captured)


async def test_callback_send_doc(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(kind="passport", title="Паспорт", owner_person_id=p.id, status="active")
    session.add(d)
    await session.commit()

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    async def noop(*a: Any, **kw: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.notifications.send_files", noop),
        patch("bot.handlers.client.answer_callback_query", AsyncMock()),
    ):
        await handlers.handle_update(_cb(f"send_doc_{d.id}"), session)
    assert any("Паспорт" in t for t in captured)


# ---------------------------------------------------------------------------
# Files path: photo + document
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tag editing flow via inline buttons
# ---------------------------------------------------------------------------


async def test_callback_tag_edit_sets_state(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(
        kind="passport",
        title="Паспорт",
        owner_person_id=p.id,
        status="active",
        tags=["паспорт"],
    )
    session.add(d)
    await session.commit()

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    async def fake_edit_markup(*a: Any, **kw: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.client.answer_callback_query", AsyncMock()),
        patch("bot.handlers.client.edit_message_reply_markup", fake_edit_markup),
    ):
        await handlers.handle_update(_cb(f"tag_edit_document_{d.id}"), session)

    bs = await state.get_state(session, settings.telegram_chat_id)
    assert bs is not None
    assert bs.state == "awaiting_tag_edit"
    assert bs.context["rtype"] == "document"
    assert bs.context["rid"] == d.id
    assert any("Какие теги" in t for t in captured)


async def test_text_in_tag_edit_state_updates_tags(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(
        kind="passport",
        title="Паспорт",
        owner_person_id=p.id,
        status="active",
        tags=["паспорт"],
    )
    session.add(d)
    await session.commit()
    did = d.id

    await state.set_state(
        session,
        settings.telegram_chat_id,
        "awaiting_tag_edit",
        {"rtype": "document", "rid": did},
    )

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    with patch("bot.handlers.notifications.send_text", fake_send):
        await handlers.handle_update(_msg("+загран +первый"), session)

    refreshed = await session.get(Document, did)
    assert refreshed is not None
    assert "загран" in (refreshed.tags or [])
    assert "первый" in (refreshed.tags or [])
    assert "паспорт" in (refreshed.tags or [])
    assert any("Обновил теги" in t for t in captured)
    bs = await state.get_state(session, settings.telegram_chat_id)
    assert bs is None


# ---------------------------------------------------------------------------
# Delete confirmation flow
# ---------------------------------------------------------------------------


async def test_callback_del_asks_confirmation(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(kind="passport", title="Паспорт", owner_person_id=p.id, status="active")
    session.add(d)
    await session.commit()

    captured: list[tuple[str, Any]] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append((text, keyboard))

    async def noop(*a: Any, **kw: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.client.answer_callback_query", AsyncMock()),
        patch("bot.handlers.client.edit_message_reply_markup", noop),
    ):
        await handlers.handle_update(_cb(f"del_document_{d.id}"), session)

    assert any("Точно удалить" in text for text, _ in captured)
    confirm_msg = next(((t, k) for t, k in captured if "Точно удалить" in t), None)
    assert confirm_msg is not None
    _, keyboard = confirm_msg
    assert keyboard is not None
    bs = await state.get_state(session, settings.telegram_chat_id)
    assert bs is not None and bs.state == "awaiting_delete_confirm"


async def test_callback_del_yes_deletes_record(session: AsyncSession) -> None:
    p = Person(full_name="Anna", relation="жена")
    session.add(p)
    await session.flush()
    d = Document(kind="passport", title="Паспорт", owner_person_id=p.id, status="active")
    session.add(d)
    await session.commit()
    did = d.id

    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    async def noop(*a: Any, **kw: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.client.answer_callback_query", AsyncMock()),
        patch("bot.handlers.client.edit_message_reply_markup", noop),
        patch("bot.handlers.storage.delete_file"),
    ):
        await handlers.handle_update(_cb(f"del_yes_document_{did}"), session)

    assert await session.get(Document, did) is None
    assert any("Удалил" in t for t in captured)


async def test_callback_del_no_clears_state(session: AsyncSession) -> None:
    await state.set_state(
        session,
        settings.telegram_chat_id,
        "awaiting_delete_confirm",
        {"rtype": "document", "rid": 1},
    )

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        return None

    async def noop(*a: Any, **kw: Any) -> None:
        return None

    with (
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("bot.handlers.client.answer_callback_query", AsyncMock()),
        patch("bot.handlers.client.edit_message_reply_markup", noop),
    ):
        await handlers.handle_update(_cb("del_no"), session)

    bs = await state.get_state(session, settings.telegram_chat_id)
    assert bs is None


async def test_files_route_single_photo_starts_more_photos(session: AsyncSession) -> None:
    captured: list[str] = []

    async def fake_send(chat_id: int, text: str, keyboard: Any = None) -> None:
        captured.append(text)

    file_input = ingest.FileInput(b"img", "p.jpg", "image/jpeg")

    async def fake_extract(_msg: dict[str, Any]) -> list[ingest.FileInput]:
        return [file_input]

    def fake_upload(files: list[ingest.FileInput], prefix: str) -> list[dict[str, Any]]:
        return [{"r2_key": "x/1.jpg", "filename": "p.jpg", "content_type": "image/jpeg"}]

    update = {
        "message": {
            "chat": {"id": settings.telegram_chat_id},
            "photo": [{"file_id": "ph"}],
        }
    }
    with (
        patch("bot.handlers._extract_files", fake_extract),
        patch("bot.handlers.notifications.send_text", fake_send),
        patch("modules.ingest._upload_files_now", fake_upload),
    ):
        await handlers.handle_update(update, session)
    assert any("1 фото" in t for t in captured)
    bs = await state.get_state(session, settings.telegram_chat_id)
    assert bs is not None
    assert bs.state == "awaiting_more_photos"
