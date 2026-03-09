"""Tests for file-based reference functions and parser helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from modules.parser import is_send_file_request
from modules.reference import (
    extract_reference_label,
    find_person_by_relation,
    find_reference_item,
    format_ref_data_text,
    get_owned_items,
    get_reference_filename,
    is_reference_caption,
    parse_and_save_reference,
    parse_and_save_reference_from_file,
    save_reference_item,
    set_owner,
)


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── is_reference_caption ──────────────────────────────────────────────────────


def test_is_reference_caption_empty() -> None:
    assert is_reference_caption("") is False


def test_is_reference_caption_trigger_only() -> None:
    assert is_reference_caption("справочник") is True
    assert is_reference_caption("в справочник") is True
    assert is_reference_caption("Справочник") is True


def test_is_reference_caption_with_label() -> None:
    assert is_reference_caption("справочник: мой загранпаспорт") is True
    assert is_reference_caption("в справочник: водительские права") is True
    assert is_reference_caption("сохрани в справочник: паспорт жены") is True


def test_is_reference_caption_unrelated() -> None:
    assert is_reference_caption("загранпаспорт истекает через 3 месяца") is False
    assert is_reference_caption("вот мой паспорт") is False
    assert is_reference_caption("просто фото") is False


# ── extract_reference_label ───────────────────────────────────────────────────


def test_extract_reference_label_with_label() -> None:
    assert extract_reference_label("справочник: мой загранпаспорт") == "мой загранпаспорт"
    assert extract_reference_label("в справочник: водительские права") == "водительские права"


def test_extract_reference_label_no_label() -> None:
    assert extract_reference_label("справочник") is None
    assert extract_reference_label("в справочник") is None


def test_extract_reference_label_preserves_case() -> None:
    result = extract_reference_label("справочник: Загранпаспорт РФ")
    assert result == "Загранпаспорт РФ"


# ── is_send_file_request ──────────────────────────────────────────────────────


def test_is_send_file_request_true() -> None:
    assert is_send_file_request("пришли права") is True
    assert is_send_file_request("скинь загранпаспорт") is True
    assert is_send_file_request("дай скан паспорта") is True
    assert is_send_file_request("дай фото прав") is True
    assert is_send_file_request("где мои права") is True
    assert is_send_file_request("где мой паспорт") is True


def test_is_send_file_request_false() -> None:
    assert is_send_file_request("покажи мне список задач") is False
    assert is_send_file_request("отправь уведомление") is False
    assert is_send_file_request("загранпаспорт истекает через 3 месяца") is False
    assert is_send_file_request("добавь в справочник") is False


# ── get_reference_filename ────────────────────────────────────────────────────


def test_get_reference_filename_with_path() -> None:
    assert get_reference_filename("reference/abc123.jpg") == "abc123.jpg"


def test_get_reference_filename_no_path() -> None:
    assert get_reference_filename("abc123.jpg") == "abc123.jpg"


# ── parse_and_save_reference_from_file ────────────────────────────────────────


async def test_parse_and_save_creates_document_entity(db: AsyncSession) -> None:
    """Document type → entity created, r2_key stored."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "document", "label": "Водительские права",'
        ' "data": {"doc_type": "rights"}, "end_date": null}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="OCR текст")),
        patch("modules.storage.upload_file", return_value="reference/abc.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, entity, auto_linked = await parse_and_save_reference_from_file(
            b"bytes", "rights.jpg", "image/jpeg", "справочник: Водительские права", db
        )

    assert ref_item.type == "document"
    assert ref_item.label == "Водительские права"
    assert ref_item.r2_key == "reference/abc.jpg"
    assert entity is not None
    assert entity.end_date is None
    assert auto_linked is None


async def test_parse_and_save_with_end_date_creates_reminders(db: AsyncSession) -> None:
    """Document with end_date → entity and reminders created."""
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = (
        '{"type": "document", "label": "Загранпаспорт", "data": {}, "end_date": "2030-01-01"}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/pass.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, entity, auto_linked = await parse_and_save_reference_from_file(
            b"bytes", "pass.jpg", "image/jpeg", "справочник: загранпаспорт", db
        )

    assert entity is not None
    assert entity.end_date is not None
    assert auto_linked is None

    from sqlalchemy import select as sa_select

    from models import Reminder

    reminders = (
        (await db.execute(sa_select(Reminder).where(Reminder.entity_id == entity.id)))
        .scalars()
        .all()
    )
    assert len(reminders) > 0


async def test_parse_and_save_car_no_entity(db: AsyncSession) -> None:
    """Car type → no entity created."""
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = (
        '{"type": "car", "label": "Тойота Камри", "data": {"plate": "А123БВ777"}, "end_date": null}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/car.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, entity, auto_linked = await parse_and_save_reference_from_file(
            b"bytes", "car.jpg", "image/jpeg", "справочник: моя машина", db
        )

    assert ref_item.type == "car"
    assert entity is None
    assert auto_linked is None


async def test_parse_and_save_label_from_caption_takes_priority(db: AsyncSession) -> None:
    """Label from caption overrides OpenAI label."""
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = '{"type": "document", "label": "Паспорт", "data": {}, "end_date": null}'

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/x.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, _, _2 = await parse_and_save_reference_from_file(
            b"bytes", "x.jpg", "image/jpeg", "справочник: мой загранпаспорт", db
        )

    assert ref_item.label == "мой загранпаспорт"


# ── format_ref_data_text ──────────────────────────────────────────────────────


async def test_format_ref_data_text(db: AsyncSession) -> None:
    item = await save_reference_item(
        "document", "Загранпаспорт", {"series": "71 01", "number": "1234567"}, db
    )
    text = format_ref_data_text(item)
    assert "Загранпаспорт" in text
    assert "71 01" in text
    assert "1234567" in text
    assert f"#{item.id}" in text


# ── find_reference_item ───────────────────────────────────────────────────────


async def test_find_reference_item_no_items(db: AsyncSession) -> None:
    result = await find_reference_item("пришли права", db)
    assert result is None


async def test_find_reference_item_no_match(db: AsyncSession) -> None:
    """Returns None when OpenAI says id=0."""
    await save_reference_item("document", "Права", {}, db)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = "0"

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await find_reference_item("пришли несуществующий документ", db)

    assert result is None


async def test_find_reference_item_found_with_file(db: AsyncSession) -> None:
    item = await save_reference_item("document", "Водительские права", {}, db)
    item.r2_key = "reference/rights.jpg"
    await db.commit()

    mock_response = MagicMock()
    mock_response.choices[0].message.content = str(item.id)

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await find_reference_item("пришли права", db)

    assert result is not None
    assert result.id == item.id
    assert result.r2_key == "reference/rights.jpg"


async def test_find_reference_item_found_without_file(db: AsyncSession) -> None:
    """Items without r2_key are still returned (data shown without file)."""
    item = await save_reference_item("document", "Паспорт РФ", {"series": "45 05"}, db)

    mock_response = MagicMock()
    mock_response.choices[0].message.content = str(item.id)

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await find_reference_item("пришли паспорт", db)

    assert result is not None
    assert result.id == item.id
    assert result.r2_key is None


# ── find_person_by_relation ───────────────────────────────────────────────────


async def test_find_person_by_relation_found(db: AsyncSession) -> None:
    await save_reference_item("person", "Добрынина Анастасия", {}, db, relation="жена")
    person = await find_person_by_relation("жена", db)
    assert person is not None
    assert person.relation == "жена"


async def test_find_person_by_relation_not_found(db: AsyncSession) -> None:
    await save_reference_item("person", "Иванов", {}, db, relation="друг")
    person = await find_person_by_relation("жена", db)
    assert person is None


async def test_find_person_by_relation_no_persons(db: AsyncSession) -> None:
    person = await find_person_by_relation("жена", db)
    assert person is None


# ── get_owned_items / set_owner ───────────────────────────────────────────────


async def test_get_owned_items_returns_linked(db: AsyncSession) -> None:
    person = await save_reference_item("person", "Анастасия", {}, db, relation="жена")
    doc = await save_reference_item(
        "document", "Загранпаспорт жены", {}, db, owner_ref_id=person.id
    )
    owned = await get_owned_items(person.id, db)
    assert len(owned) == 1
    assert owned[0].id == doc.id


async def test_get_owned_items_empty(db: AsyncSession) -> None:
    person = await save_reference_item("person", "Иван", {}, db)
    owned = await get_owned_items(person.id, db)
    assert owned == []


async def test_set_owner_success(db: AsyncSession) -> None:
    person = await save_reference_item("person", "Анастасия", {}, db, relation="жена")
    doc = await save_reference_item("document", "Права", {}, db)
    ok = await set_owner(doc.id, person.id, db)
    assert ok is True
    await db.refresh(doc)
    assert doc.owner_ref_id == person.id


async def test_set_owner_missing_ref(db: AsyncSession) -> None:
    ok = await set_owner(9999, 1, db)
    assert ok is False


# ── auto-linking in parse_and_save_reference_from_file ───────────────────────


async def test_file_auto_links_owner_when_relation_found(db: AsyncSession) -> None:
    """'загранпаспорт жены' in label → auto-links to person with relation='жена'."""
    person = await save_reference_item("person", "Анастасия Добрынина", {}, db, relation="жена")

    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "document", "label": "Загранпаспорт жены",'
        ' "data": {}, "end_date": null, "relation": null}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/z.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        ref_item, _, auto_linked = await parse_and_save_reference_from_file(
            b"bytes", "z.jpg", "image/jpeg", "справочник: загранпаспорт жены", db
        )

    assert auto_linked is not None
    assert auto_linked.id == person.id
    assert ref_item.owner_ref_id == person.id


async def test_file_no_auto_link_when_no_person_card(db: AsyncSession) -> None:
    """Label has 'жены' but no person with relation='жена' → auto_linked is None."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "document", "label": "Загранпаспорт жены",'
        ' "data": {}, "end_date": null, "relation": null}'
    )

    with (
        patch("modules.reference._get_client") as mock_client,
        patch("modules.ingestion.extract_text_from_file", new=AsyncMock(return_value="")),
        patch("modules.storage.upload_file", return_value="reference/z2.jpg"),
    ):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        _, _, auto_linked = await parse_and_save_reference_from_file(
            b"bytes", "z2.jpg", "image/jpeg", "справочник: загранпаспорт жены", db
        )

    assert auto_linked is None


# ── parse_and_save_reference (text path) ─────────────────────────────────────


async def test_text_saves_relation_for_person(db: AsyncSession) -> None:
    """Text path: person type → relation field saved to DB."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "person", "label": "Добрынина Анастасия",'
        ' "relation": "жена", "data": {"full_name": "Добрынина Анастасия Сергеевна"},'
        ' "end_date": null}'
    )

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await parse_and_save_reference(
            "Добавь в справочник: жена Добрынина Анастасия Сергеевна", db
        )

    assert result is not None
    ref_item, entity, auto_linked = result
    assert ref_item.relation == "жена"
    assert entity is not None  # person type → entity created
    assert auto_linked is None  # person itself has no owner


async def test_text_creates_entity_with_end_date(db: AsyncSession) -> None:
    """Text path: document with end_date → entity + reminders created."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "document", "label": "Загранпаспорт РФ",'
        ' "relation": null, "data": {}, "end_date": "2030-06-01"}'
    )

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await parse_and_save_reference(
            "Добавь в справочник: загранпаспорт РФ, истекает 01.06.2030", db
        )

    assert result is not None
    ref_item, entity, _ = result
    assert entity is not None
    assert entity.end_date is not None

    from sqlalchemy import select as sa_select

    from models import Reminder

    reminders = (
        (await db.execute(sa_select(Reminder).where(Reminder.entity_id == entity.id)))
        .scalars()
        .all()
    )
    assert len(reminders) > 0


async def test_text_auto_links_owner(db: AsyncSession) -> None:
    """Text path: 'загранпаспорт жены' → auto-links to person with relation='жена'."""
    person = await save_reference_item("person", "Анастасия", {}, db, relation="жена")

    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"type": "document", "label": "Загранпаспорт жены",'
        ' "relation": null, "data": {}, "end_date": null}'
    )

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await parse_and_save_reference(
            "Добавь в справочник: загранпаспорт жены серия 71 №9876543", db
        )

    assert result is not None
    ref_item, _, auto_linked = result
    assert auto_linked is not None
    assert auto_linked.id == person.id
    assert ref_item.owner_ref_id == person.id


async def test_text_returns_none_on_parse_error(db: AsyncSession) -> None:
    """Returns None when OpenAI call fails."""
    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        mock_client.return_value = mock_instance

        result = await parse_and_save_reference("что-то непонятное", db)

    assert result is None
