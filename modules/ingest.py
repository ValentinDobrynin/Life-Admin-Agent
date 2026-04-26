"""Ingest pipeline: classify → verify → save.

Public entrypoints:

* :func:`ingest_text` — text-only message; may dispatch to query path.
* :func:`ingest_files` — files (with optional caption); always ingest.
* :func:`add_more_photos` / :func:`finish_more_photos` — handles the
  "📎 Ещё / ✅ Готово" loop for single-photo flow.
* :func:`confirm_draft` — `✅ Всё верно` after verification card.
* :func:`request_edit` / :func:`apply_edit` — `✏️ Исправить` flow.
* :func:`resolve_duplicate` — `🆕 / 📎 / ♻️` after duplicate detected.
* :func:`detect_intent_text` — small wrapper for handlers to decide
  between ingest and query for text-only messages.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Address, Document, Note, Person, Vehicle
from modules import cards, pdf, state, storage, vision


def _kb(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class FileInput:
    bytes_: bytes
    filename: str
    content_type: str


@dataclass
class IngestResult:
    text: str
    keyboard: dict[str, Any] | None = None
    preamble: str | None = None


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _load_soul() -> str:
    soul_path = PROMPTS_DIR / "soul.txt"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# OpenAI calls
# ---------------------------------------------------------------------------


async def _existing_persons_brief(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(Person.id, Person.full_name, Person.relation))
    return [
        {"id": pid, "full_name": name, "relation": relation} for pid, name, relation in result.all()
    ]


async def _classify(text: str, ocr_text: str, db: AsyncSession) -> dict[str, Any]:
    """Run classify.txt over (text + OCR), return parsed JSON."""
    soul = _load_soul()
    classify = _load_prompt("classify.txt")
    persons = await _existing_persons_brief(db)

    system = classify
    if soul:
        system = soul + "\n\n---\n\n" + classify

    user_payload = {
        "text": text or "",
        "ocr_text": ocr_text or "",
        "existing_persons": persons,
    }

    client = _client()
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        parsed = cast(dict[str, Any], json.loads(content))
    except json.JSONDecodeError:
        logger.exception("classify returned non-JSON: %s", content[:200])
        return {"intent": "query", "ingest": None, "query": text}
    return _normalise_ingest(parsed)


async def _patch(draft: dict[str, Any], phrase: str) -> dict[str, Any]:
    """Run patch.txt over (draft, phrase), return updated draft (ingest dict)."""
    patch_prompt = _load_prompt("patch.txt")
    user_payload = {"DRAFT": draft, "PATCH": phrase}

    client = _client()
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": patch_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        patched = cast(dict[str, Any], json.loads(content))
    except json.JSONDecodeError:
        logger.exception("patch returned non-JSON: %s", content[:200])
        return draft
    return _normalise_ingest(patched)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ALLOWED_RELATIONS: frozenset[str] = frozenset(
    {"я", "жена", "муж", "сын", "дочь", "мама", "папа", "брат", "сестра", "друг", "коллега", "иное"}
)

# LLM occasionally returns English/colloquial labels for owner_relation
# (e.g. "self", "me", "wife", "mom"). We normalise to the canonical Russian
# enum used everywhere in the system, otherwise the verification card looks
# wrong and the search index gets garbage values.
_RELATION_SYNONYMS: dict[str, str] = {
    "я": "я",
    "мой": "я",
    "моё": "я",
    "мое": "я",
    "моя": "я",
    "мои": "я",
    "себя": "я",
    "self": "я",
    "me": "я",
    "i": "я",
    "my": "я",
    "myself": "я",
    "owner": "я",
    "жена": "жена",
    "супруга": "жена",
    "wife": "жена",
    "муж": "муж",
    "супруг": "муж",
    "husband": "муж",
    "сын": "сын",
    "son": "сын",
    "дочь": "дочь",
    "дочка": "дочь",
    "daughter": "дочь",
    "мама": "мама",
    "мать": "мама",
    "mom": "мама",
    "mum": "мама",
    "mother": "мама",
    "папа": "папа",
    "отец": "папа",
    "dad": "папа",
    "father": "папа",
    "брат": "брат",
    "brother": "брат",
    "сестра": "сестра",
    "sister": "сестра",
    "друг": "друг",
    "подруга": "друг",
    "friend": "друг",
    "коллега": "коллега",
    "colleague": "коллега",
    "coworker": "коллега",
    "иное": "иное",
    "other": "иное",
}


def _normalise_owner_relation(value: Any) -> str | None:
    """Map free-form/English owner_relation to the canonical Russian enum.

    Returns the canonical value, or ``None`` if the value is unknown or empty.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in {"null", "none", "—", "-"}:
        return None
    if s in _ALLOWED_RELATIONS:
        return s
    if s in _RELATION_SYNONYMS:
        return _RELATION_SYNONYMS[s]
    return None


_ALLOWED_PASSPORT_TYPES: frozenset[str] = frozenset({"internal", "foreign"})

_PASSPORT_TYPE_SYNONYMS: dict[str, str] = {
    "internal": "internal",
    "общегражданский": "internal",
    "внутренний": "internal",
    "ru_internal": "internal",
    "domestic": "internal",
    "foreign": "foreign",
    "загран": "foreign",
    "загранпаспорт": "foreign",
    "international": "foreign",
    "biometric": "foreign",
    "ru_foreign": "foreign",
}


def _normalise_passport_type(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in {"null", "none", "—", "-"}:
        return None
    if s in _ALLOWED_PASSPORT_TYPES:
        return s
    return _PASSPORT_TYPE_SYNONYMS.get(s)


def _detect_passport_type_from_number(raw: Any) -> str | None:
    """Heuristic: RF internal passport is 4-digit series + 6-digit number
    (i.e. 10 digits total). RF foreign biometric is 9 digits. Other formats
    (alphanumeric, 8 digits, etc.) likely foreign too."""
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    has_letters = any(ch.isalpha() for ch in str(raw))
    if has_letters:
        return "foreign"
    if len(digits) == 10:
        return "internal"
    if len(digits) == 9:
        return "foreign"
    return None


def _augment_passport_fields(ingest: dict[str, Any]) -> None:
    """Auto-detect passport_type from series/number when LLM left it empty."""
    if ingest.get("type") != "document" or ingest.get("kind") != "passport":
        return
    fields = ingest.get("fields")
    if not isinstance(fields, dict):
        return
    raw_type = fields.get("passport_type")
    norm = _normalise_passport_type(raw_type)
    if norm is None:
        series = fields.get("series")
        number = fields.get("number")
        candidate = f"{series or ''}{number or ''}".strip()
        norm = _detect_passport_type_from_number(candidate)
        if norm is not None:
            logger.info("auto-detected passport_type=%s from number=%r", norm, candidate)
    fields["passport_type"] = norm


def _normalise_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitise an ingest dict in place (and return it). Currently:

    * normalises ``owner_relation`` to canonical Russian enum,
    * normalises ``fields.passport_type`` to ``"internal"|"foreign"|null``,
    * if passport_type is missing, auto-detects from series/number format.
    """
    if not isinstance(payload, dict):
        return payload
    ingest = payload.get("ingest") if "ingest" in payload else payload
    if isinstance(ingest, dict):
        if "owner_relation" in ingest:
            before = ingest.get("owner_relation")
            after = _normalise_owner_relation(before)
            if before != after:
                logger.info("normalised owner_relation: %r -> %r", before, after)
            ingest["owner_relation"] = after
        _augment_passport_fields(ingest)
    return payload


@dataclass
class _OcrResult:
    text: str
    truncated_pdf: tuple[int, int] | None = None


async def _ocr_files(files: list[FileInput]) -> _OcrResult:
    """Run OCR/text-extract over a batch of files.

    Returns ``_OcrResult`` with concatenated text and an optional
    ``truncated_pdf=(processed, total)`` if any scanned PDF exceeded the
    OCR page cap.
    """
    chunks: list[str] = []
    truncated: tuple[int, int] | None = None

    for f in files:
        ct = (f.content_type or "").lower()
        if ct.startswith("image/"):
            text = await vision.ocr_image(f.bytes_, mime=ct)
            if text:
                chunks.append(text)
        elif ct == "application/pdf" or f.filename.lower().endswith(".pdf"):
            text = pdf.extract_text_layer(f.bytes_)
            if not text:
                images, total_pages = pdf.render_pages_to_images(f.bytes_)
                if images and total_pages > len(images):
                    truncated = (len(images), total_pages)
                parts: list[str] = []
                for img in images:
                    page_text = await vision.ocr_image(img, mime="image/png")
                    if page_text:
                        parts.append(page_text)
                text = "\n\n".join(parts)
            if text:
                chunks.append(text)
    return _OcrResult(text="\n\n".join(chunks).strip(), truncated_pdf=truncated)


def _upload_files_now(files: list[FileInput], prefix: str) -> list[dict[str, Any]]:
    """Upload to R2 immediately; return list of dicts for storage in draft/record."""
    uploaded: list[dict[str, Any]] = []
    for f in files:
        try:
            r2_key = storage.upload_file(
                f.bytes_, f.filename, prefix=prefix, content_type=f.content_type
            )
        except Exception:
            logger.exception("Upload failed for %s", f.filename)
            continue
        uploaded.append({"r2_key": r2_key, "filename": f.filename, "content_type": f.content_type})
    return uploaded


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def _resolve_owner_id(
    db: AsyncSession,
    draft_ingest: dict[str, Any],
) -> int | None:
    """Match draft owner_relation/owner_full_name to an existing person.

    Strategy: prefer match by relation; if absent, by full_name. Does NOT
    auto-create a person — user must add explicitly via classify.
    """
    relation = draft_ingest.get("owner_relation")
    full_name = draft_ingest.get("owner_full_name")
    if not relation and not full_name:
        return None

    if relation:
        result = await db.execute(select(Person).where(Person.relation == relation))
        for p in result.scalars().all():
            if not full_name or p.full_name == full_name:
                return cast(int, p.id)
    if full_name:
        result = await db.execute(select(Person).where(Person.full_name == full_name))
        match = result.scalars().first()
        if match is not None:
            return cast(int, match.id)
    return None


# ---------------------------------------------------------------------------
# Save / duplicate detection
# ---------------------------------------------------------------------------


async def _save_record(
    db: AsyncSession, draft: dict[str, Any], owner_person_id: int | None
) -> tuple[str, int]:
    """Persist the draft as a typed record. Returns (type, id)."""
    record_type = draft.get("type") or "note"
    files = draft.get("files") or []
    fields = draft.get("fields") or {}
    tags = draft.get("tags") or []
    title = draft.get("suggested_title") or ""

    if record_type == "document":
        kind = draft.get("kind") or "other"
        kind_set = {kind, "паспорт"} if kind == "passport" else {kind}
        merged_tags = list({*tags, *kind_set, _eng_to_ru(kind), _ru_to_eng(kind)})
        merged_tags = [t for t in merged_tags if t]

        d = Document(
            kind=kind,
            title=title or kind,
            owner_person_id=owner_person_id,
            issued_at=_parse_date(fields.get("issued_at")),
            expires_at=_parse_date(fields.get("expires_at")),
            status="active",
            fields=fields,
            tags=merged_tags,
            files=files,
        )
        db.add(d)
        await db.commit()
        await db.refresh(d)
        return ("document", cast(int, d.id))

    if record_type == "person":
        p = Person(
            full_name=draft.get("owner_full_name") or title or "Контакт",
            birthday=_parse_date(fields.get("birthday")),
            relation=draft.get("owner_relation"),
            notes=fields.get("notes"),
            fields=fields,
            tags=tags,
            files=files,
        )
        db.add(p)
        await db.commit()
        await db.refresh(p)
        return ("person", cast(int, p.id))

    if record_type == "vehicle":
        v = Vehicle(
            make=fields.get("make"),
            model=fields.get("model"),
            plate=fields.get("plate"),
            vin=fields.get("vin"),
            owner_person_id=owner_person_id,
            fields=fields,
            tags=tags,
            files=files,
        )
        db.add(v)
        await db.commit()
        await db.refresh(v)
        return ("vehicle", cast(int, v.id))

    if record_type == "address":
        a = Address(
            label=fields.get("label") or title or None,
            person_id=owner_person_id,
            country=fields.get("country"),
            city=fields.get("city"),
            street=fields.get("street"),
            fields=fields,
            tags=tags,
            files=files,
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        return ("address", cast(int, a.id))

    n = Note(
        title=title or "Заметка",
        body=fields.get("body") or draft.get("body"),
        fields=fields,
        tags=tags,
        files=files,
    )
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return ("note", cast(int, n.id))


_RU_KIND_MAP = {
    "passport": "паспорт",
    "driver_license": "права",
    "insurance": "страховка",
    "visa": "виза",
    "certificate": "сертификат",
    "contract": "договор",
    "snils": "снилс",
    "inn": "инн",
    "medical": "медкарта",
}


def _eng_to_ru(kind: str) -> str:
    return _RU_KIND_MAP.get(kind, "")


def _ru_to_eng(kind: str) -> str:
    return kind if kind in _RU_KIND_MAP else ""


async def _find_duplicate(
    db: AsyncSession, kind: str, owner_person_id: int | None
) -> Document | None:
    if not kind or owner_person_id is None:
        return None
    result = await db.execute(
        select(Document).where(
            Document.kind == kind,
            Document.owner_person_id == owner_person_id,
            Document.status == "active",
        )
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# UI builders (keyboards)
# ---------------------------------------------------------------------------


def _kb_verify() -> dict[str, Any]:
    return _kb(
        [
            [
                {"text": "✅ Всё верно", "callback_data": "verify_ok"},
                {"text": "✏️ Исправить", "callback_data": "verify_edit"},
            ]
        ]
    )


def _kb_more_photos() -> dict[str, Any]:
    return _kb(
        [
            [
                {"text": "📎 Ещё страница", "callback_data": "photos_more"},
                {"text": "✅ Готово", "callback_data": "photos_done"},
            ]
        ]
    )


def _kb_dup_resolution() -> dict[str, Any]:
    return _kb(
        [
            [
                {"text": "🆕 Новый", "callback_data": "dup_new"},
                {"text": "📎 Дополнить", "callback_data": "dup_merge"},
                {"text": "♻️ Заменить", "callback_data": "dup_replace"},
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Public flow
# ---------------------------------------------------------------------------


async def _proceed_after_classify(
    db: AsyncSession,
    chat_id: int,
    classified: dict[str, Any],
    files: list[dict[str, Any]],
) -> IngestResult:
    """Common path after classification: handle note shortcut or show verification card."""
    if classified.get("intent") != "ingest" or not classified.get("ingest"):
        await state.clear_state(db, chat_id)
        return IngestResult(
            text="Не понял что сохранить. Попробуй описать подробнее или приложи документ."
        )

    draft: dict[str, Any] = dict(classified["ingest"])
    draft["files"] = files

    if draft.get("type") == "note":
        owner_id = await _resolve_owner_id(db, draft)
        record_type, rid = await _save_record(db, draft, owner_id)
        await state.clear_state(db, chat_id)
        return IngestResult(text=f"📝 Записал заметку · /{record_type}_{rid}")

    await state.set_state(
        db,
        chat_id,
        "awaiting_ocr_verification",
        {"draft": draft},
    )
    card = cards.render_verification_card(draft)
    return IngestResult(text=card, keyboard=_kb_verify())


async def detect_intent_text(text: str, db: AsyncSession) -> tuple[str, dict[str, Any]]:
    """Return (intent, classified_payload) for a text-only message.

    Caller decides whether to ingest or query based on intent.
    """
    classified = await _classify(text=text, ocr_text="", db=db)
    return classified.get("intent", "query"), classified


async def ingest_text(
    chat_id: int, text: str, db: AsyncSession, *, classified: dict[str, Any] | None = None
) -> IngestResult:
    """Text-only ingest: caller already knows intent=ingest."""
    if classified is None:
        classified = await _classify(text=text, ocr_text="", db=db)
    return await _proceed_after_classify(db, chat_id, classified, files=[])


async def ingest_files(
    chat_id: int,
    files: list[FileInput],
    caption: str,
    is_album: bool,
    db: AsyncSession,
) -> IngestResult:
    """Ingest files. If single non-PDF photo without album → start awaiting_more_photos."""
    if not files:
        return IngestResult(text="Не получил файлов.")

    is_single_photo = (
        not is_album and len(files) == 1 and (files[0].content_type or "").startswith("image/")
    )
    if is_single_photo:
        uploaded = _upload_files_now(files, prefix="document")
        await state.set_state(
            db,
            chat_id,
            "awaiting_more_photos",
            {"files": uploaded, "caption": caption or ""},
        )
        return IngestResult(
            text="Принял 1 фото. Будут ещё страницы или это всё?",
            keyboard=_kb_more_photos(),
        )

    prefix = "document"
    uploaded = _upload_files_now(files, prefix=prefix)
    ocr = await _ocr_files(files)
    # Free file bytes once OCR + upload are done; the rest of the pipeline
    # only needs metadata. Important on Render Starter where 5 MB PDFs +
    # rendered pages can push us past the 512 MB memory limit.
    for f in files:
        f.bytes_ = b""
    classified = await _classify(text=caption, ocr_text=ocr.text, db=db)
    result = await _proceed_after_classify(db, chat_id, classified, files=uploaded)
    if ocr.truncated_pdf is not None:
        processed, total = ocr.truncated_pdf
        result.preamble = (
            f"⚠️ PDF на {total} стр. — распознал только первые {processed}. "
            "Если данные на других страницах, пришли их отдельно как фото."
        )
    return result


async def add_more_photos(chat_id: int, files: list[FileInput], db: AsyncSession) -> IngestResult:
    """Append another photo to the awaiting_more_photos draft."""
    bs = await state.get_state(db, chat_id)
    if bs is None or bs.state != "awaiting_more_photos":
        return await ingest_files(chat_id, files, caption="", is_album=False, db=db)

    uploaded = _upload_files_now(files, prefix="document")
    ctx = dict(bs.context)
    ctx["files"] = (ctx.get("files") or []) + uploaded
    await state.set_state(db, chat_id, "awaiting_more_photos", ctx)
    n = len(ctx["files"])
    return IngestResult(
        text=f"Принял {n} фото. Ещё или хватит?",
        keyboard=_kb_more_photos(),
    )


async def finish_more_photos(chat_id: int, db: AsyncSession) -> IngestResult:
    """User pressed ✅ Готово in awaiting_more_photos. Run classify and proceed."""
    bs = await state.get_state(db, chat_id)
    if bs is None or bs.state != "awaiting_more_photos":
        return IngestResult(text="Не нашёл активный черновик. Пришли файлы заново.")

    ctx = bs.context
    files_meta: list[dict[str, Any]] = ctx.get("files") or []
    caption: str = ctx.get("caption") or ""

    ocr_chunks: list[str] = []
    for fmeta in files_meta:
        try:
            data = storage.download_file(fmeta["r2_key"])
        except Exception:
            logger.exception("Failed to re-download for OCR: %s", fmeta.get("r2_key"))
            continue
        text = await vision.ocr_image(data, mime=fmeta.get("content_type") or "image/jpeg")
        if text:
            ocr_chunks.append(text)
    ocr_text = "\n\n".join(ocr_chunks)

    classified = await _classify(text=caption, ocr_text=ocr_text, db=db)
    return await _proceed_after_classify(db, chat_id, classified, files=files_meta)


async def confirm_draft(chat_id: int, db: AsyncSession) -> IngestResult:
    """User pressed ✅ Всё верно on the verification card."""
    bs = await state.get_state(db, chat_id)
    if bs is None or bs.state != "awaiting_ocr_verification":
        return IngestResult(
            text=("Черновик уже неактивен — TTL истёк или был сброшен. Пришли документ заново.")
        )

    draft: dict[str, Any] = bs.context.get("draft") or {}
    owner_id = await _resolve_owner_id(db, draft)

    if draft.get("type") == "document" and draft.get("kind"):
        existing = await _find_duplicate(db, draft["kind"], owner_id)
        if existing is not None:
            await state.set_state(
                db,
                chat_id,
                "awaiting_dup_resolution",
                {"draft": draft, "existing_id": existing.id, "owner_id": owner_id},
            )
            return IngestResult(
                text=(
                    f"У этого владельца уже есть {draft['kind']} · "
                    f"<b>{existing.title}</b> (id /{existing.id}).\n\n"
                    "Что делаем?"
                ),
                keyboard=_kb_dup_resolution(),
            )

    record_type, rid = await _save_record(db, draft, owner_id)
    await state.clear_state(db, chat_id)
    title = draft.get("suggested_title") or record_type
    return IngestResult(text=f"✅ Сохранил <b>{title}</b> · /{record_type}_{rid}")


async def request_edit(chat_id: int, db: AsyncSession) -> IngestResult:
    """User pressed ✏️ Исправить — switch to awaiting_ocr_edit, ask for phrase."""
    bs = await state.get_state(db, chat_id)
    if bs is None or bs.state != "awaiting_ocr_verification":
        logger.warning(
            "request_edit: no active draft for chat=%s (state=%s)",
            chat_id,
            None if bs is None else bs.state,
        )
        return IngestResult(
            text=("Черновик уже неактивен — TTL истёк или был сброшен. Пришли документ заново.")
        )
    draft = bs.context.get("draft") or {}
    await state.set_state(db, chat_id, "awaiting_ocr_edit", {"draft": draft})
    return IngestResult(
        text=(
            "✏️ <b>Что исправить?</b>\n\n"
            "Напиши одной фразой, например:\n"
            "• <i>серия 4515, ФИО Иванова Анна Сергеевна</i>\n"
            "• <i>выдан 2020-05-12, кем выдан УФМС 770-001</i>\n\n"
            "Я обновлю карточку и снова спрошу подтверждение."
        )
    )


async def apply_edit(chat_id: int, phrase: str, db: AsyncSession) -> IngestResult:
    """User sent free-form patch text in awaiting_ocr_edit state."""
    bs = await state.get_state(db, chat_id)
    if bs is None or bs.state != "awaiting_ocr_edit":
        return IngestResult(text="Сейчас нечего редактировать.")
    draft = bs.context.get("draft") or {}
    files_meta = draft.get("files") or []

    patched = await _patch(draft, phrase)
    patched["files"] = files_meta

    await state.set_state(db, chat_id, "awaiting_ocr_verification", {"draft": patched})
    card = cards.render_verification_card(patched)
    return IngestResult(text=card, keyboard=_kb_verify())


async def resolve_duplicate(
    chat_id: int,
    choice: Literal["new", "merge", "replace"],
    db: AsyncSession,
) -> IngestResult:
    """User pressed 🆕 / 📎 / ♻️ in awaiting_dup_resolution state."""
    bs = await state.get_state(db, chat_id)
    if bs is None or bs.state != "awaiting_dup_resolution":
        return IngestResult(text="Сейчас нечего разрешать.")

    ctx = bs.context
    draft: dict[str, Any] = ctx.get("draft") or {}
    existing_id = cast(int, ctx.get("existing_id"))
    owner_id: int | None = ctx.get("owner_id")

    if choice == "new":
        record_type, rid = await _save_record(db, draft, owner_id)
        await state.clear_state(db, chat_id)
        return IngestResult(text=f"🆕 Сохранил как новый объект · /{record_type}_{rid}")

    if choice == "merge":
        result = await db.execute(select(Document).where(Document.id == existing_id))
        existing = result.scalar_one_or_none()
        if existing is None:
            await state.clear_state(db, chat_id)
            return IngestResult(text="Не нашёл существующий документ. Сохранил как новый.")
        new_files = (existing.files or []) + (draft.get("files") or [])
        existing.files = new_files
        await db.commit()
        await state.clear_state(db, chat_id)
        return IngestResult(text=f"📎 Дополнил <b>{existing.title}</b> · /document_{existing.id}")

    # choice == "replace"
    result = await db.execute(select(Document).where(Document.id == existing_id))
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.status = "replaced"
        await db.commit()
    record_type, rid = await _save_record(db, draft, owner_id)
    await state.clear_state(db, chat_id)
    return IngestResult(
        text=f"♻️ Заменил. Старая запись помечена как replaced · /{record_type}_{rid}"
    )
