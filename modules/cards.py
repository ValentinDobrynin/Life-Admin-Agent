"""Render Telegram HTML cards for verification and retrieval flows."""

from __future__ import annotations

from datetime import date
from typing import Any

# Field labels per record type/kind, in display order.
_LABELS_PASSPORT: list[tuple[str, str]] = [
    ("series", "Серия"),
    ("number", "Номер"),
    ("full_name", "ФИО"),
    ("birthday", "Дата рожд."),
    ("issued_at", "Выдан"),
    ("issued_by", "Кем выдан"),
    ("expires_at", "Срок до"),
    ("country", "Страна"),
]

_PASSPORT_TYPE_LABEL: dict[str, str] = {
    "internal": "Внутренний паспорт",
    "foreign": "Загранпаспорт",
}

_LABELS_DRIVER_LICENSE: list[tuple[str, str]] = [
    ("number", "Номер"),
    ("full_name", "ФИО"),
    ("birthday", "Дата рожд."),
    ("categories", "Категории"),
    ("issued_at", "Выдан"),
    ("expires_at", "Срок до"),
]

_LABELS_INSURANCE: list[tuple[str, str]] = [
    ("policy_number", "Полис"),
    ("company", "Страховая"),
    ("insured_name", "Застрахован"),
    ("vehicle_plate", "Авто"),
    ("valid_from", "С"),
    ("valid_until", "До"),
]

_LABELS_VISA: list[tuple[str, str]] = [
    ("country", "Страна"),
    ("number", "Номер"),
    ("type", "Тип"),
    ("valid_from", "С"),
    ("valid_until", "До"),
]

_LABELS_VEHICLE: list[tuple[str, str]] = [
    ("make", "Марка"),
    ("model", "Модель"),
    ("plate", "Номер"),
    ("vin", "VIN"),
    ("year", "Год"),
    ("color", "Цвет"),
]

_LABELS_PERSON: list[tuple[str, str]] = [
    ("phone", "Телефон"),
    ("email", "E-mail"),
    ("telegram", "Telegram"),
    ("birthday", "Дата рожд."),
]

_LABELS_ADDRESS: list[tuple[str, str]] = [
    ("country", "Страна"),
    ("city", "Город"),
    ("street", "Улица"),
    ("building", "Дом"),
    ("apartment", "Кв."),
    ("postcode", "Индекс"),
]

_KIND_TITLE: dict[str, str] = {
    "passport": "Паспорт",
    "driver_license": "Водительские права",
    "insurance": "Страховой полис",
    "visa": "Виза",
    "certificate": "Сертификат",
    "contract": "Договор",
    "snils": "СНИЛС",
    "inn": "ИНН",
    "medical": "Медицинский документ",
    "other": "Документ",
}


def _emoji(record_type: str, kind: str | None = None) -> str:
    if record_type == "person":
        return "👤"
    if record_type == "vehicle":
        return "🚗"
    if record_type == "address":
        return "🏠"
    if record_type == "note":
        return "📝"
    if record_type == "document":
        if kind in ("passport", "visa"):
            return "🛂"
        if kind == "driver_license":
            return "🚙"
        if kind == "insurance":
            return "🛡️"
        if kind == "medical":
            return "🩺"
        return "📄"
    return "•"


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _labels_for(record_type: str, kind: str | None) -> list[tuple[str, str]]:
    if record_type == "document":
        if kind == "passport":
            return _LABELS_PASSPORT
        if kind == "driver_license":
            return _LABELS_DRIVER_LICENSE
        if kind == "insurance":
            return _LABELS_INSURANCE
        if kind == "visa":
            return _LABELS_VISA
        return [("number", "Номер"), ("issued_at", "Выдан"), ("expires_at", "Срок до")]
    if record_type == "vehicle":
        return _LABELS_VEHICLE
    if record_type == "person":
        return _LABELS_PERSON
    if record_type == "address":
        return _LABELS_ADDRESS
    return []


def _file_summary(files: list[dict[str, Any]]) -> str:
    if not files:
        return "Файлы: —"
    photos = sum(1 for f in files if (f.get("content_type") or "").startswith("image/"))
    pdfs = sum(1 for f in files if (f.get("content_type") or "") == "application/pdf")
    others = len(files) - photos - pdfs
    parts: list[str] = []
    if photos:
        parts.append(f"{photos} фото")
    if pdfs:
        parts.append(f"{pdfs} PDF")
    if others:
        parts.append(f"{others} файл(ов)")
    return "Файлы: " + ", ".join(parts) if parts else "Файлы: —"


def _passport_kind_label(fields: dict[str, Any] | None) -> str:
    """Return a human label for a passport, taking passport_type into account.

    Used both as a default `suggested_title` and for the search index.
    """
    pt = (fields or {}).get("passport_type")
    if pt in _PASSPORT_TYPE_LABEL:
        return _PASSPORT_TYPE_LABEL[pt]
    return _KIND_TITLE["passport"]


def _draft_title(draft: dict[str, Any]) -> str:
    record_type = draft.get("type") or "note"
    kind = draft.get("kind")
    title = draft.get("suggested_title")
    if title:
        return str(title)
    if record_type == "document" and kind == "passport":
        return _passport_kind_label(draft.get("fields"))
    if record_type == "document" and kind in _KIND_TITLE:
        return _KIND_TITLE[kind]
    if record_type == "person":
        name = draft.get("owner_full_name") or "Контакт"
        return str(name)
    if record_type == "vehicle":
        f = draft.get("fields") or {}
        return f"{f.get('make') or 'Машина'} {f.get('model') or ''}".strip()
    if record_type == "address":
        f = draft.get("fields") or {}
        return f.get("label") or f.get("city") or "Адрес"
    return "Заметка"


def render_verification_card(draft: dict[str, Any]) -> str:
    """Render a draft (output of classify.txt 'ingest' branch) as HTML card.

    Used in `awaiting_ocr_verification` state.
    """
    record_type = draft.get("type") or "note"
    kind = draft.get("kind")
    fields = draft.get("fields") or {}
    files = draft.get("files") or []

    lines: list[str] = []
    title = _draft_title(draft)
    lines.append(f"{_emoji(record_type, kind)} <b>{_esc(title)}</b>")

    owner_relation = draft.get("owner_relation")
    owner_name = draft.get("owner_full_name")
    if owner_relation or owner_name:
        owner_str = _esc(owner_relation or "—")
        if owner_name:
            owner_str += f" ({_esc(owner_name)})"
        lines.append(f"Владелец: {owner_str}")

    for key, label in _labels_for(record_type, kind):
        if key in fields and fields[key] not in (None, ""):
            lines.append(f"{label}: {_esc(fields[key])}")

    if record_type == "note":
        body = fields.get("body") or draft.get("body")
        if body:
            lines.append(_esc(body))

    tags = draft.get("tags") or []
    if tags:
        lines.append("Теги: " + ", ".join(_esc(t) for t in tags))

    lines.append("")
    lines.append(_file_summary(files))
    lines.append("")
    lines.append("Сохранить?")

    return "\n".join(lines)


def render_record_card(record_type: str, record: dict[str, Any]) -> str:
    """Render a saved record (db row → dict) as HTML card. Used in retrieval."""
    kind = record.get("kind")
    fields = record.get("fields") or {}
    files = record.get("files") or []

    lines: list[str] = []
    if record_type == "person":
        title = record.get("full_name") or "Контакт"
    elif record_type == "address":
        title = record.get("label") or record.get("city") or "Адрес"
    elif record_type == "vehicle":
        title = f"{record.get('make') or ''} {record.get('model') or ''}".strip() or "Машина"
    else:
        title = record.get("title") or _draft_title({**record, "type": record_type})
    lines.append(f"{_emoji(record_type, kind)} <b>{_esc(title)}</b>")

    if record_type == "document":
        if record.get("expires_at"):
            lines.append(f"Срок до: {_esc(record['expires_at'])}")
        if record.get("issued_at"):
            lines.append(f"Выдан: {_esc(record['issued_at'])}")

    for key, label in _labels_for(record_type, kind):
        if key in fields and fields[key] not in (None, ""):
            lines.append(f"{label}: {_esc(fields[key])}")

    if record_type == "person":
        if record.get("relation"):
            lines.append(f"Роль: {_esc(record['relation'])}")
        if record.get("notes"):
            lines.append(_esc(record["notes"]))
    if record_type == "address" and record.get("street"):
        lines.append(_esc(record["street"]))
    if record_type == "note":
        body = record.get("body") or fields.get("body")
        if body:
            lines.append(_esc(body))

    tags = record.get("tags") or []
    if tags:
        lines.append("Теги: " + ", ".join(_esc(t) for t in tags))

    if files:
        lines.append("")
        lines.append(_file_summary(files))

    rid = record.get("id")
    if rid is not None:
        lines.append(f"\nID: <code>/{record_type}_{rid}</code>")

    return "\n".join(lines)
