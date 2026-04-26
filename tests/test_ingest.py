from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import Document, Note, Person
from modules import ingest, state


@pytest.fixture()
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _stub_classify(payload: dict[str, Any]) -> Any:
    async def fake(text: str, ocr_text: str, db: AsyncSession) -> dict[str, Any]:
        return payload

    return fake


def _stub_patch(payload: dict[str, Any]) -> Any:
    async def fake(draft: dict[str, Any], phrase: str) -> dict[str, Any]:
        return payload

    return fake


def _stub_upload() -> Any:
    counter = {"n": 0}

    def fake(files: list[ingest.FileInput], prefix: str) -> list[dict[str, Any]]:
        out = []
        for f in files:
            counter["n"] += 1
            out.append(
                {
                    "r2_key": f"{prefix}/{counter['n']}.bin",
                    "filename": f.filename,
                    "content_type": f.content_type,
                }
            )
        return out

    return fake


# ---------------------------------------------------------------------------
# ingest_text — note shortcut
# ---------------------------------------------------------------------------


async def test_ingest_text_note_skips_verification(session: AsyncSession) -> None:
    payload = {
        "intent": "ingest",
        "ingest": {
            "type": "note",
            "kind": None,
            "owner_relation": None,
            "owner_full_name": None,
            "fields": {"body": "Ключи под ковриком"},
            "tags": ["ключи"],
            "suggested_title": "Ключи",
        },
        "query": None,
    }
    with patch("modules.ingest._classify", _stub_classify(payload)):
        result = await ingest.ingest_text(123, "Ключи под ковриком", session)

    assert "Записал заметку" in result.text
    assert result.keyboard is None
    assert await state.get_state(session, 123) is None

    rows = (await session.execute(select(Note))).scalars().all()
    assert len(rows) == 1
    assert rows[0].title == "Ключи"


async def test_ingest_text_query_returns_clarification(session: AsyncSession) -> None:
    payload = {"intent": "query", "ingest": None, "query": "пришли паспорт"}
    with patch("modules.ingest._classify", _stub_classify(payload)):
        result = await ingest.ingest_text(123, "пришли паспорт", session)
    assert "Не понял" in result.text


# ---------------------------------------------------------------------------
# ingest_files — single photo branches into awaiting_more_photos
# ---------------------------------------------------------------------------


async def test_ingest_files_single_photo_starts_more_photos(session: AsyncSession) -> None:
    files = [ingest.FileInput(b"img", "p.jpg", "image/jpeg")]
    with patch("modules.ingest._upload_files_now", _stub_upload()):
        result = await ingest.ingest_files(
            chat_id=10, files=files, caption="", is_album=False, db=session
        )
    assert "1 фото" in result.text
    assert result.keyboard is not None

    bs = await state.get_state(session, 10)
    assert bs is not None
    assert bs.state == "awaiting_more_photos"
    assert len(bs.context["files"]) == 1


async def test_add_more_photos_appends(session: AsyncSession) -> None:
    files = [ingest.FileInput(b"img", "p.jpg", "image/jpeg")]
    with patch("modules.ingest._upload_files_now", _stub_upload()):
        await ingest.ingest_files(chat_id=10, files=files, caption="", is_album=False, db=session)
        more = [ingest.FileInput(b"img2", "p2.jpg", "image/jpeg")]
        result = await ingest.add_more_photos(10, more, session)

    assert "2 фото" in result.text
    bs = await state.get_state(session, 10)
    assert bs is not None
    assert len(bs.context["files"]) == 2


# ---------------------------------------------------------------------------
# ingest_files — album: classify → verification card
# ---------------------------------------------------------------------------


async def test_ingest_files_album_shows_verification(session: AsyncSession) -> None:
    files = [
        ingest.FileInput(b"a", "1.jpg", "image/jpeg"),
        ingest.FileInput(b"b", "2.jpg", "image/jpeg"),
    ]
    classified = {
        "intent": "ingest",
        "ingest": {
            "type": "document",
            "kind": "passport",
            "owner_relation": "я",
            "owner_full_name": "Иван Иванов",
            "fields": {"series": "4514", "number": "123456"},
            "tags": ["паспорт", "passport"],
            "suggested_title": "Паспорт",
        },
    }

    async def fake_ocr(files: Any) -> ingest._OcrResult:
        return ingest._OcrResult(text="паспорт распознан")

    with (
        patch("modules.ingest._upload_files_now", _stub_upload()),
        patch("modules.ingest._ocr_files", fake_ocr),
        patch("modules.ingest._classify", _stub_classify(classified)),
    ):
        result = await ingest.ingest_files(
            chat_id=20, files=files, caption="мой паспорт", is_album=True, db=session
        )

    assert "Паспорт" in result.text
    assert "Сохранить?" in result.text
    assert result.keyboard is not None

    bs = await state.get_state(session, 20)
    assert bs is not None
    assert bs.state == "awaiting_ocr_verification"


# ---------------------------------------------------------------------------
# confirm_draft — saves document, no duplicate
# ---------------------------------------------------------------------------


async def test_confirm_draft_saves_document(session: AsyncSession) -> None:
    p = Person(full_name="Иван", relation="я")
    session.add(p)
    await session.commit()

    draft = {
        "type": "document",
        "kind": "passport",
        "owner_relation": "я",
        "owner_full_name": "Иван",
        "fields": {"series": "4514", "number": "123456"},
        "tags": ["паспорт"],
        "suggested_title": "Паспорт",
        "files": [{"r2_key": "k", "filename": "x.jpg", "content_type": "image/jpeg"}],
    }
    await state.set_state(session, 30, "awaiting_ocr_verification", {"draft": draft})
    result = await ingest.confirm_draft(30, session)

    assert "Сохранил" in result.text
    docs = (await session.execute(select(Document))).scalars().all()
    assert len(docs) == 1
    assert docs[0].kind == "passport"
    assert "passport" in docs[0].tags


async def test_confirm_draft_detects_duplicate(session: AsyncSession) -> None:
    p = Person(full_name="Анна", relation="жена")
    session.add(p)
    await session.flush()
    existing = Document(kind="passport", title="Старый паспорт", owner_person_id=p.id)
    session.add(existing)
    await session.commit()

    draft = {
        "type": "document",
        "kind": "passport",
        "owner_relation": "жена",
        "owner_full_name": "Анна",
        "fields": {},
        "tags": ["паспорт"],
        "suggested_title": "Новый паспорт",
        "files": [],
    }
    await state.set_state(session, 40, "awaiting_ocr_verification", {"draft": draft})
    result = await ingest.confirm_draft(40, session)

    assert "уже есть" in result.text
    assert result.keyboard is not None
    bs = await state.get_state(session, 40)
    assert bs is not None
    assert bs.state == "awaiting_dup_resolution"


# ---------------------------------------------------------------------------
# resolve_duplicate — three branches
# ---------------------------------------------------------------------------


async def test_resolve_duplicate_new(session: AsyncSession) -> None:
    p = Person(full_name="Анна", relation="жена")
    session.add(p)
    await session.flush()
    existing = Document(kind="passport", title="Старый", owner_person_id=p.id)
    session.add(existing)
    await session.commit()

    draft = {
        "type": "document",
        "kind": "passport",
        "owner_relation": "жена",
        "owner_full_name": "Анна",
        "fields": {},
        "tags": ["паспорт"],
        "suggested_title": "Новый",
        "files": [],
    }
    await state.set_state(
        session,
        50,
        "awaiting_dup_resolution",
        {"draft": draft, "existing_id": existing.id, "owner_id": p.id},
    )

    result = await ingest.resolve_duplicate(50, "new", session)
    assert "Сохранил" in result.text

    docs = (await session.execute(select(Document))).scalars().all()
    actives = [d for d in docs if d.status == "active"]
    assert len(actives) == 2


async def test_resolve_duplicate_merge(session: AsyncSession) -> None:
    p = Person(full_name="Анна", relation="жена")
    session.add(p)
    await session.flush()
    existing = Document(
        kind="passport",
        title="Старый",
        owner_person_id=p.id,
        files=[{"r2_key": "old/a.jpg", "filename": "a.jpg", "content_type": "image/jpeg"}],
    )
    session.add(existing)
    await session.commit()

    draft = {
        "type": "document",
        "kind": "passport",
        "owner_relation": "жена",
        "fields": {},
        "tags": ["паспорт"],
        "suggested_title": "Старый",
        "files": [{"r2_key": "new/b.jpg", "filename": "b.jpg", "content_type": "image/jpeg"}],
    }
    await state.set_state(
        session,
        60,
        "awaiting_dup_resolution",
        {"draft": draft, "existing_id": existing.id, "owner_id": p.id},
    )

    result = await ingest.resolve_duplicate(60, "merge", session)
    assert "Дополнил" in result.text

    refreshed = (
        (await session.execute(select(Document).where(Document.id == existing.id)))
        .scalars()
        .first()
    )
    assert refreshed is not None
    assert len(refreshed.files) == 2


async def test_resolve_duplicate_replace(session: AsyncSession) -> None:
    p = Person(full_name="Анна", relation="жена")
    session.add(p)
    await session.flush()
    existing = Document(kind="passport", title="Старый", owner_person_id=p.id)
    session.add(existing)
    await session.commit()
    eid = existing.id

    draft = {
        "type": "document",
        "kind": "passport",
        "owner_relation": "жена",
        "fields": {},
        "tags": ["паспорт"],
        "suggested_title": "Новый",
        "files": [],
    }
    await state.set_state(
        session,
        70,
        "awaiting_dup_resolution",
        {"draft": draft, "existing_id": eid, "owner_id": p.id},
    )

    result = await ingest.resolve_duplicate(70, "replace", session)
    assert "Заменил" in result.text

    old = (await session.execute(select(Document).where(Document.id == eid))).scalars().first()
    assert old is not None
    assert old.status == "replaced"


# ---------------------------------------------------------------------------
# request_edit + apply_edit
# ---------------------------------------------------------------------------


async def test_apply_edit_runs_patch_and_returns_card(session: AsyncSession) -> None:
    draft = {
        "type": "document",
        "kind": "passport",
        "fields": {"series": "4514", "number": "999999"},
        "tags": ["паспорт"],
        "suggested_title": "Паспорт",
        "files": [],
    }
    await state.set_state(session, 80, "awaiting_ocr_verification", {"draft": draft})
    r = await ingest.request_edit(80, session)
    assert "Что исправить" in r.text
    bs2 = await state.get_state(session, 80)
    assert bs2 is not None and bs2.state == "awaiting_ocr_edit"

    patched = {
        "type": "document",
        "kind": "passport",
        "fields": {"series": "4515", "number": "999999"},
        "tags": ["паспорт"],
        "suggested_title": "Паспорт",
    }
    with patch("modules.ingest._patch", _stub_patch(patched)):
        r2 = await ingest.apply_edit(80, "серия 4515", session)
    assert "Сохранить?" in r2.text
    bs = await state.get_state(session, 80)
    assert bs is not None
    assert bs.state == "awaiting_ocr_verification"
    assert bs.context["draft"]["fields"]["series"] == "4515"


# ---------------------------------------------------------------------------
# detect_intent_text
# ---------------------------------------------------------------------------


async def test_detect_intent_text_query(session: AsyncSession) -> None:
    payload = {"intent": "query", "ingest": None, "query": "пришли паспорт"}
    with patch("modules.ingest._classify", _stub_classify(payload)):
        intent, _ = await ingest.detect_intent_text("пришли паспорт", session)
    assert intent == "query"


async def test_detect_intent_text_ingest(session: AsyncSession) -> None:
    payload = {
        "intent": "ingest",
        "ingest": {
            "type": "person",
            "kind": None,
            "owner_relation": "друг",
            "owner_full_name": "Саша Балахнин",
            "fields": {},
            "tags": ["друг"],
            "suggested_title": "Саша Балахнин",
        },
    }
    with patch("modules.ingest._classify", _stub_classify(payload)):
        intent, _ = await ingest.detect_intent_text("Саша Балахнин это друг", session)
    assert intent == "ingest"
