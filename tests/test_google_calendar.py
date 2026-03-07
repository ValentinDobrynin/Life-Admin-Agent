from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from exceptions import GoogleAPIError
from modules.google_calendar import (
    check_conflicts,
    create_event,
    delete_event_by_id,
    list_events,
)

# ── create_event ─────────────────────────────────────────────────────────────


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_create_event_returns_link(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    mock_service = MagicMock()
    mock_service.events().insert().execute.return_value = {"htmlLink": "https://cal.google.com/1"}
    mock_build.return_value = mock_service

    link = create_event(title="Поездка в Турцию", date="2026-05-12", time="10:00")

    assert link == "https://cal.google.com/1"


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_create_event_invalid_date_raises(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    with pytest.raises(GoogleAPIError):
        create_event(title="Test", date="not-a-date", time="25:99")


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_create_event_includes_location_and_notes(
    mock_build: MagicMock, mock_creds: MagicMock
) -> None:
    mock_service = MagicMock()
    mock_service.events().insert().execute.return_value = {"htmlLink": ""}
    mock_build.return_value = mock_service

    create_event(
        title="Врач",
        date="2026-05-01",
        time="11:00",
        location="Клиника",
        notes="взять полис",
    )

    body = mock_service.events().insert.call_args.kwargs["body"]
    assert body["location"] == "Клиника"
    assert body["description"] == "взять полис"


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_create_event_http_error_raises(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    from googleapiclient.errors import HttpError

    mock_service = MagicMock()
    mock_service.events().insert().execute.side_effect = HttpError(
        resp=MagicMock(status=500), content=b"error"
    )
    mock_build.return_value = mock_service

    with pytest.raises(GoogleAPIError):
        create_event(title="Test", date="2026-05-01", time="10:00")


# ── list_events ───────────────────────────────────────────────────────────────


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_list_events_returns_items(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    items = [{"id": "1", "summary": "Meeting"}, {"id": "2", "summary": "Lunch"}]
    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": items}
    mock_build.return_value = mock_service

    result = list_events("2026-05-01", "2026-05-01")

    assert len(result) == 2


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_list_events_empty_returns_empty_list(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {}
    mock_build.return_value = mock_service

    result = list_events("2026-05-01", "2026-05-01")

    assert result == []


# ── check_conflicts ───────────────────────────────────────────────────────────


@patch("modules.google_calendar.list_events")
def test_check_conflicts_detects_overlap(mock_list: MagicMock) -> None:
    mock_list.return_value = [
        {
            "id": "evt1",
            "summary": "Existing",
            "start": {"dateTime": "2026-05-01T10:00:00+03:00"},
            "end": {"dateTime": "2026-05-01T11:00:00+03:00"},
        }
    ]

    conflicts = check_conflicts("2026-05-01", "10:30", 30)

    assert len(conflicts) == 1


@patch("modules.google_calendar.list_events")
def test_check_conflicts_no_overlap(mock_list: MagicMock) -> None:
    mock_list.return_value = [
        {
            "id": "evt1",
            "summary": "Early meeting",
            "start": {"dateTime": "2026-05-01T08:00:00+03:00"},
            "end": {"dateTime": "2026-05-01T09:00:00+03:00"},
        }
    ]

    conflicts = check_conflicts("2026-05-01", "10:00", 60)

    assert len(conflicts) == 0


@patch("modules.google_calendar.list_events")
def test_check_conflicts_skips_all_day_events(mock_list: MagicMock) -> None:
    mock_list.return_value = [
        {"id": "evt1", "summary": "Birthday", "start": {"date": "2026-05-01"}, "end": {}}
    ]

    conflicts = check_conflicts("2026-05-01", "10:00", 60)

    assert len(conflicts) == 0


# ── delete_event_by_id ────────────────────────────────────────────────────────


@patch("modules.google_calendar.get_credentials")
@patch("modules.google_calendar.build")
def test_delete_event_calls_api(mock_build: MagicMock, mock_creds: MagicMock) -> None:
    mock_service = MagicMock()
    mock_build.return_value = mock_service

    delete_event_by_id("event-123")

    mock_service.events().delete.assert_called_once_with(calendarId="primary", eventId="event-123")


# ── google_auth ───────────────────────────────────────────────────────────────


def test_get_credentials_uses_settings() -> None:
    from modules.google_auth import SCOPES, get_credentials

    creds = get_credentials()
    assert creds.client_id == "test-client-id"
    assert creds.client_secret == "test-client-secret"
    assert "https://www.googleapis.com/auth/calendar" in SCOPES
    assert "https://www.googleapis.com/auth/tasks" not in SCOPES
