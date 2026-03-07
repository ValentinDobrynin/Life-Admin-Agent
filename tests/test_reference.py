from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import ReferenceData
from modules.parser import detect_intent
from modules.reference import (
    generate_text,
    get_profile_text,
    get_ref_card_text,
    parse_and_save_reference,
    save_reference_item,
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


async def _make_ref(
    db: AsyncSession,
    ref_type: str = "car",
    label: str = "Тойота Камри",
    data: dict | None = None,
) -> ReferenceData:
    return await save_reference_item(
        ref_type=ref_type,
        label=label,
        data=data or {"plate": "А123БВ777"},
        db=db,
    )


# ── get_profile_text ──────────────────────────────────────────────────────────


async def test_profile_empty_db(db: AsyncSession) -> None:
    text = await get_profile_text(db)
    assert "Справочник пуст" in text


async def test_profile_shows_items(db: AsyncSession) -> None:
    await _make_ref(db, ref_type="car", label="Тойота Камри")
    await _make_ref(db, ref_type="address", label="Дом", data={"full_address": "Москва"})

    text = await get_profile_text(db)

    assert "Тойота Камри" in text
    assert "Дом" in text
    assert "Машины" in text
    assert "Адреса" in text


async def test_profile_groups_by_type(db: AsyncSession) -> None:
    await _make_ref(db, ref_type="car", label="BMW")
    await _make_ref(db, ref_type="car", label="Тойота")
    await _make_ref(db, ref_type="document", label="Паспорт РФ", data={"series": "4510"})

    text = await get_profile_text(db)

    assert text.index("Машины") < text.index("Документы")
    assert "BMW" in text
    assert "Тойота" in text


# ── get_ref_card_text ─────────────────────────────────────────────────────────


async def test_ref_card_not_found(db: AsyncSession) -> None:
    text = await get_ref_card_text(9999, db)
    assert "не найдена" in text


async def test_ref_card_shows_data(db: AsyncSession) -> None:
    item = await _make_ref(
        db,
        ref_type="car",
        label="BMW X5",
        data={"plate": "В456ГД777", "brand": "BMW", "model": "X5"},
    )
    text = await get_ref_card_text(item.id, db)

    assert "BMW X5" in text
    assert f"#{item.id}" in text
    assert "В456ГД777" in text
    assert "Машины" in text


# ── save_reference_item ───────────────────────────────────────────────────────


async def test_save_reference_item(db: AsyncSession) -> None:
    item = await save_reference_item(
        ref_type="address",
        label="Офис",
        data={"full_address": "Москва, ул. Арбат, д. 1"},
        db=db,
    )
    assert item.id is not None
    assert item.type == "address"
    assert item.label == "Офис"
    assert item.data["full_address"] == "Москва, ул. Арбат, д. 1"


# ── detect_intent ─────────────────────────────────────────────────────────────


def test_detect_intent_entity_default() -> None:
    assert detect_intent("Страховка авто истекает 1 августа") == "entity"


def test_detect_intent_entity_passport_expiry() -> None:
    # Must NOT trigger reference_add — this is an entity with a deadline
    assert detect_intent("Загранпаспорт истекает через 3 месяца") == "entity"
    assert detect_intent("Мой паспорт нужно продлить до июня") == "entity"


def test_detect_intent_reference_add() -> None:
    assert (
        detect_intent("Добавь в справочник: Тойота Камри, гос.номер А123БВ777") == "reference_add"
    )
    assert detect_intent("Сохрани в справочник мой адрес: Москва") == "reference_add"
    assert detect_intent("Добавь машину: BMW X5") == "reference_add"
    assert detect_intent("Добавь адрес: ул. Тверская, д. 1") == "reference_add"


def test_detect_intent_generate() -> None:
    assert detect_intent("Сделай сообщение для пропуска на BMW") == "generate"
    assert detect_intent("Напиши заявление на отпуск") == "generate"
    assert detect_intent("Составь текст для страховой") == "generate"
    assert detect_intent("Сгенерируй письмо в управу") == "generate"


# ── generate_text ─────────────────────────────────────────────────────────────


async def test_generate_text_empty_db(db: AsyncSession) -> None:
    text = await generate_text("Сделай пропуск на машину", db)
    assert "Справочник пуст" in text


async def test_generate_text_calls_openai(db: AsyncSession) -> None:
    await _make_ref(db, ref_type="car", label="Тойота Камри", data={"plate": "А123БВ777"})

    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = "Прошу оформить пропуск на а/м Тойота Камри А123БВ777"

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        result = await generate_text("Сделай пропуск на мою машину", db)

    assert "А123БВ777" in result


async def test_generate_text_openai_error(db: AsyncSession) -> None:
    await _make_ref(db)

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        mock_client.return_value = mock_instance

        result = await generate_text("Сделай текст", db)

    assert "Ошибка" in result


# ── parse_and_save_reference ──────────────────────────────────────────────────


async def test_parse_and_save_reference_success(db: AsyncSession) -> None:
    mock_response = MagicMock()
    mock_response.choices[
        0
    ].message.content = '{"type": "car", "label": "Тойота Камри", "data": {"plate": "А123БВ777"}}'

    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance

        item = await parse_and_save_reference(
            "Добавь в справочник: Тойота Камри, гос.номер А123БВ777", db
        )

    assert item is not None
    assert item.type == "car"
    assert item.label == "Тойота Камри"
    assert item.data["plate"] == "А123БВ777"


async def test_parse_and_save_reference_openai_error(db: AsyncSession) -> None:
    with patch("modules.reference._get_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(side_effect=Exception("API error"))
        mock_client.return_value = mock_instance

        result = await parse_and_save_reference("Добавь в справочник что-то", db)

    assert result is None
