from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from exceptions import GoogleAPIError
from modules.google_auth import get_credentials


def create_event(
    title: str,
    date: str,
    time: str,
    duration_minutes: int = 60,
    location: str | None = None,
    notes: str | None = None,
    timezone: str = "Europe/Moscow",
) -> str:
    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)

        start_dt = datetime.strptime(f"{date}T{time}", "%Y-%m-%dT%H:%M")
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
        }
        if location:
            body["location"] = location
        if notes:
            body["description"] = notes

        event = service.events().insert(calendarId="primary", body=body).execute()
        return str(event.get("htmlLink", ""))
    except (HttpError, ValueError) as exc:
        raise GoogleAPIError(str(exc)) from exc


def list_events(
    date_from: str,
    date_to: str,
    timezone: str = "Europe/Moscow",
) -> list[dict[str, Any]]:
    try:
        tz = ZoneInfo(timezone)
        time_min = datetime.strptime(date_from, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, tzinfo=tz
        )
        time_max = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=tz
        )

        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        return list(result.get("items", []))
    except (HttpError, ValueError) as exc:
        raise GoogleAPIError(str(exc)) from exc


def check_conflicts(
    date: str,
    start_time: str,
    duration_minutes: int,
    timezone: str = "Europe/Moscow",
    exclude_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return timed events that overlap [start, start+duration) on the given date."""
    try:
        tz = ZoneInfo(timezone)
        new_start = datetime.strptime(f"{date}T{start_time}", "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
    except ValueError as exc:
        raise GoogleAPIError(str(exc)) from exc
    new_end = new_start + timedelta(minutes=duration_minutes)

    events = list_events(date, date, timezone)
    conflicts: list[dict[str, Any]] = []

    for event in events:
        if event.get("id") == exclude_event_id:
            continue
        start_info = event.get("start", {})
        end_info = event.get("end", {})
        dt_str = start_info.get("dateTime")
        if not dt_str:
            continue  # all-day event — skip
        ev_start = datetime.fromisoformat(dt_str)
        if ev_start.tzinfo is None:
            ev_start = ev_start.replace(tzinfo=tz)
        end_dt_str = end_info.get("dateTime")
        if end_dt_str:
            ev_end = datetime.fromisoformat(end_dt_str)
            if ev_end.tzinfo is None:
                ev_end = ev_end.replace(tzinfo=tz)
        else:
            ev_end = ev_start + timedelta(hours=1)

        if new_start < ev_end and new_end > ev_start:
            conflicts.append(event)

    return conflicts


def _find_event_by_title(events: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    title_lower = title.lower()
    query_prefixes = [w[: max(4, len(w) - 1)] for w in title_lower.split() if len(w) >= 4]
    for event in events:
        summary = event.get("summary", "").lower()
        if title_lower in summary or summary in title_lower:
            return event
        event_words = summary.split()
        if query_prefixes and any(ew.startswith(qp) for qp in query_prefixes for ew in event_words):
            return event
    return None


def find_event_by_title(
    title: str,
    search_date: str,
    timezone: str = "Europe/Moscow",
) -> dict[str, Any] | None:
    try:
        search_dt = datetime.strptime(search_date, "%Y-%m-%d")
    except ValueError as exc:
        raise GoogleAPIError(str(exc)) from exc
    date_from = (search_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    date_to = (search_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    events = list_events(date_from, date_to, timezone)
    return _find_event_by_title(events, title)


def delete_event_by_id(event_id: str) -> None:
    try:
        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        service.events().delete(calendarId="primary", eventId=event_id).execute()
    except HttpError as exc:
        raise GoogleAPIError(str(exc)) from exc


def update_event_by_id(
    event_id: str,
    event_body: dict[str, Any],
    new_time: str | None = None,
    new_date: str | None = None,
    new_duration: int | None = None,
    new_title: str | None = None,
    new_location: str | None = None,
    new_notes: str | None = None,
    timezone: str = "Europe/Moscow",
) -> str:
    try:
        start_dt_str = event_body.get("start", {}).get("dateTime")
        if not start_dt_str:
            raise GoogleAPIError("Событие занимает весь день — изменение времени недоступно")

        start_dt = datetime.fromisoformat(start_dt_str)
        end_dt_str = event_body.get("end", {}).get("dateTime")
        end_dt = datetime.fromisoformat(end_dt_str) if end_dt_str else None
        current_duration = int((end_dt - start_dt).total_seconds() / 60) if end_dt else 60

        if new_date:
            d = datetime.strptime(new_date, "%Y-%m-%d")
            start_dt = start_dt.replace(year=d.year, month=d.month, day=d.day)
        if new_time:
            h, m = map(int, new_time.split(":"))
            start_dt = start_dt.replace(hour=h, minute=m, second=0, microsecond=0)

        final_duration = new_duration if new_duration else current_duration
        new_end_dt = start_dt + timedelta(minutes=final_duration)

        body: dict[str, Any] = dict(event_body)
        body.pop("id", None)
        body.pop("etag", None)
        body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": timezone}
        body["end"] = {"dateTime": new_end_dt.isoformat(), "timeZone": timezone}
        if new_title:
            body["summary"] = new_title
        if new_location is not None:
            body["location"] = new_location
        if new_notes is not None:
            body["description"] = new_notes

        creds = get_credentials()
        service = build("calendar", "v3", credentials=creds)
        updated = (
            service.events().update(calendarId="primary", eventId=event_id, body=body).execute()
        )
        return str(updated.get("htmlLink", ""))
    except (HttpError, ValueError) as exc:
        raise GoogleAPIError(str(exc)) from exc


def find_and_update_event(
    title: str,
    search_date: str,
    new_time: str | None = None,
    new_date: str | None = None,
    new_duration: int | None = None,
    new_title: str | None = None,
    new_location: str | None = None,
    new_notes: str | None = None,
    timezone: str = "Europe/Moscow",
) -> str:
    event = find_event_by_title(title, search_date, timezone)
    if event is None:
        raise GoogleAPIError(f"Событие «{title}» на {search_date} не найдено")
    return update_event_by_id(
        event_id=event["id"],
        event_body=event,
        new_time=new_time,
        new_date=new_date,
        new_duration=new_duration,
        new_title=new_title,
        new_location=new_location,
        new_notes=new_notes,
        timezone=timezone,
    )
