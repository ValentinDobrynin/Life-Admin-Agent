from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@patch("bot.handlers.notifications.send_message", new_callable=AsyncMock)
async def test_start_command_sends_greeting(
    mock_send: AsyncMock,
    db_session: AsyncSession,
) -> None:
    from bot.handlers import handle_update

    update = {"message": {"text": "/start", "from": {"id": 123}}}
    await handle_update(update, db_session)

    mock_send.assert_called_once()
    call_args = mock_send.call_args[0][0]
    assert "Life Admin Agent" in call_args


@patch("bot.handlers.ingestion.process_text", new_callable=AsyncMock)
async def test_text_message_calls_ingestion(
    mock_process: AsyncMock,
    db_session: AsyncSession,
) -> None:
    from bot.handlers import handle_update

    mock_process.return_value = MagicMock(id=1)
    update = {"message": {"text": "Сертификат на массаж"}}
    await handle_update(update, db_session)

    mock_process.assert_called_once_with("Сертификат на массаж", db_session)


@patch("bot.client.answer_callback_query", new_callable=AsyncMock)
async def test_ok_callback_answered(
    mock_answer: AsyncMock,
    db_session: AsyncSession,
) -> None:
    from bot.handlers import handle_update

    update = {
        "callback_query": {
            "id": "abc123",
            "data": "ok_42",
            "from": {"id": 123},
        }
    }
    await handle_update(update, db_session)
    mock_answer.assert_called_once_with("abc123", text="✅ Сохранено")


@patch("bot.handlers.notifications.send_message", new_callable=AsyncMock)
@patch("bot.client.answer_callback_query", new_callable=AsyncMock)
async def test_attach_callback_sends_prompt(
    mock_answer: AsyncMock,
    mock_send: AsyncMock,
    db_session: AsyncSession,
) -> None:
    from bot.handlers import handle_update

    update = {
        "callback_query": {
            "id": "cb1",
            "data": "attach_7",
            "from": {"id": 123},
        }
    }
    await handle_update(update, db_session)
    mock_answer.assert_called_once()
    mock_send.assert_called_once()
    assert "#7" in mock_send.call_args[0][0]


@patch("bot.handlers.notifications.send_message", new_callable=AsyncMock)
@patch("bot.client.answer_callback_query", new_callable=AsyncMock)
async def test_edit_callback_sets_pending_state(
    mock_answer: AsyncMock,
    mock_send: AsyncMock,
    db_session: AsyncSession,
) -> None:
    import bot.handlers as handlers

    handlers._pending_edit_entity_id = None
    update = {
        "callback_query": {
            "id": "cb_edit",
            "data": "edit_99",
            "from": {"id": 123},
        }
    }
    from bot.handlers import handle_update

    await handle_update(update, db_session)
    assert handlers._pending_edit_entity_id == 99
    mock_send.assert_called_once()
    assert "#99" in mock_send.call_args[0][0]


@patch("bot.handlers.ingestion.process_edit", new_callable=AsyncMock)
@patch("bot.handlers.ingestion.process_text", new_callable=AsyncMock)
async def test_text_after_edit_callback_calls_process_edit(
    mock_process_text: AsyncMock,
    mock_process_edit: AsyncMock,
    db_session: AsyncSession,
) -> None:
    import bot.handlers as handlers

    handlers._pending_edit_entity_id = 99
    update = {"message": {"text": "перенеси на 20 сентября"}}
    from bot.handlers import handle_update

    await handle_update(update, db_session)
    mock_process_edit.assert_called_once_with(99, "перенеси на 20 сентября", db_session)
    mock_process_text.assert_not_called()
    assert handlers._pending_edit_entity_id is None


@patch("bot.client.get_file", new_callable=AsyncMock)
@patch("bot.client.download_file", new_callable=AsyncMock)
@patch("modules.ingestion.process_file", new_callable=AsyncMock)
@patch("bot.handlers.notifications.send_message", new_callable=AsyncMock)
async def test_photo_message_triggers_process_file(
    mock_send: AsyncMock,
    mock_process_file: AsyncMock,
    mock_download: AsyncMock,
    mock_get_file: AsyncMock,
    db_session: AsyncSession,
) -> None:
    from bot.handlers import handle_update

    mock_get_file.return_value = {"result": {"file_path": "photos/file_abc.jpg"}}
    mock_download.return_value = b"fake-image-bytes"
    mock_process_file.return_value = None

    update = {"message": {"photo": [{"file_id": "abc", "file_size": 1024}]}}
    await handle_update(update, db_session)

    mock_process_file.assert_called_once()
    mock_send.assert_called_once()
