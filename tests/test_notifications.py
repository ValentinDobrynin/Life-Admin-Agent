from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

from models import Entity
from modules.notifications import _format_dates, make_capture_buttons, send_confirmation


def test_make_capture_buttons_contains_ok() -> None:
    buttons = make_capture_buttons(42)
    all_data = [btn["callback_data"] for row in buttons for btn in row]
    assert "ok_42" in all_data
    assert "attach_42" in all_data
    assert "edit_42" in all_data


def test_format_dates_with_end_date() -> None:
    entity = Entity(type="certificate", name="test", end_date=date(2026, 6, 30))
    assert "30.06.2026" in _format_dates(entity)


def test_format_dates_with_start_date_only() -> None:
    entity = Entity(type="trip", name="test", start_date=date(2026, 5, 12))
    assert "12.05.2026" in _format_dates(entity)


def test_format_dates_no_dates() -> None:
    entity = Entity(type="logistics", name="test")
    assert _format_dates(entity) == ""


@patch("modules.notifications.client.send_message", new_callable=AsyncMock)
async def test_send_confirmation_calls_telegram(mock_send: AsyncMock) -> None:
    entity = Entity(id=1, type="certificate", name="SPA сертификат", end_date=date(2026, 6, 30))
    await send_confirmation(entity)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    text = call_kwargs[1]["text"] if "text" in call_kwargs[1] else call_kwargs[0][1]
    assert "SPA сертификат" in text
    assert "reply_markup" in (call_kwargs[1] or {}) or call_kwargs[0]


@patch("modules.notifications.client.send_message", new_callable=AsyncMock)
async def test_send_confirmation_includes_buttons(mock_send: AsyncMock) -> None:
    entity = Entity(id=5, type="document", name="Страховка", status="active")
    await send_confirmation(entity)

    _, kwargs = mock_send.call_args
    markup = kwargs.get("reply_markup", {})
    assert "inline_keyboard" in markup
