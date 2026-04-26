"""Telegram webhook router. Pure routing — no business logic.

Three top-level paths:

* :func:`ingest_path` — text or files headed for ingest pipeline.
* :func:`query_path` — text headed for retrieval.
* :func:`callback_router` — inline-button callbacks.

Album buffering for media_group_id is handled via an in-process buffer with
a small flush delay; tests exercise the path that bypasses buffering.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from bot import client
from config import settings
from modules import cards, ingest, notifications, search, state, storage
from modules.ingest import FileInput

logger = logging.getLogger(__name__)

ALBUM_FLUSH_DELAY_SEC = 1.5


# ---------------------------------------------------------------------------
# Album buffer (in-memory). Survives a single message-batch only.
# ---------------------------------------------------------------------------


@dataclass
class _AlbumBuffer:
    chat_id: int
    media_group_id: str
    files: list[FileInput] = field(default_factory=list)
    caption: str = ""
    flush_task: asyncio.Task[None] | None = None


_album_buffers: dict[str, _AlbumBuffer] = {}


# ---------------------------------------------------------------------------
# Public webhook entry
# ---------------------------------------------------------------------------


async def handle_update(update: dict[str, Any], db: AsyncSession) -> None:
    """Dispatch a Telegram update to the right handler."""
    if "callback_query" in update:
        await callback_router(update["callback_query"], db)
        return

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    chat_id = msg.get("chat", {}).get("id")
    if chat_id is None:
        return

    if chat_id != settings.telegram_chat_id:
        logger.warning("ignoring message from unauthorised chat=%s", chat_id)
        return

    text: str = msg.get("text") or ""
    caption: str = msg.get("caption") or ""

    if text.startswith("/"):
        await _command_router(chat_id, text, db)
        return

    media_group_id = msg.get("media_group_id")

    files = await _extract_files(msg)
    if files:
        if media_group_id:
            await _album_collect(chat_id, media_group_id, files, caption, db)
        else:
            await _route_files(chat_id, files, caption, is_album=False, db=db)
        return

    if text:
        await _route_text(chat_id, text, db)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


_HELP_TEXT = (
    "📦 <b>Хранилище личных данных</b>\n\n"
    "Просто пиши, что хочешь сохранить, или присылай фото/PDF.\n"
    "Чтобы что-то найти — пиши вопросом: «пришли паспорт жены», «когда истекает страховка».\n\n"
    "<b>Команды</b>\n"
    "• /list — что лежит в хранилище\n"
    "• /get <code>type_id</code> — выдать запись по id\n"
    "• /delete <code>type_id</code> — удалить запись и файлы\n"
    "• /cancel — сбросить текущий диалог\n"
    "• /help — это сообщение"
)


async def _command_router(chat_id: int, text: str, db: AsyncSession) -> None:
    cmd, _, rest = text.strip().partition(" ")
    cmd = cmd.lower()
    rest = rest.strip()

    if cmd in {"/start", "/help"}:
        await notifications.send_text(chat_id, _HELP_TEXT)
        return

    if cmd == "/cancel":
        await state.clear_state(db, chat_id)
        await notifications.send_text(chat_id, "Окей, сбросил.")
        return

    if cmd == "/list":
        await _command_list(chat_id, db)
        return

    if cmd == "/get":
        await _command_get(chat_id, rest, db)
        return

    if cmd == "/delete":
        await _command_delete(chat_id, rest, db)
        return

    await notifications.send_text(chat_id, "Не знаю такой команды. /help")


async def _command_list(chat_id: int, db: AsyncSession) -> None:
    index = await search.build_index(db)
    if not index:
        await notifications.send_text(chat_id, "Хранилище пустое.")
        return

    lines = [f"📦 <b>В хранилище {len(index)} записей</b>", ""]
    for item in index[:50]:
        rid = f"/{item['type']}_{item['id']}"
        lines.append(f"• {item['summary']} — <code>{rid}</code>")
    if len(index) > 50:
        lines.append(f"\n…и ещё {len(index) - 50}")
    await notifications.send_text(chat_id, "\n".join(lines))


async def _command_get(chat_id: int, rest: str, db: AsyncSession) -> None:
    parsed = _parse_record_id(rest)
    if parsed is None:
        await notifications.send_text(chat_id, "Формат: /get document_42")
        return
    rtype, rid = parsed
    rec = await search.get_record(db, rtype, rid)
    if rec is None:
        await notifications.send_text(chat_id, "Не нашёл такую запись.")
        return
    await _send_record(chat_id, rtype, rec)


async def _command_delete(chat_id: int, rest: str, db: AsyncSession) -> None:
    parsed = _parse_record_id(rest)
    if parsed is None:
        await notifications.send_text(chat_id, "Формат: /delete document_42")
        return
    rtype, rid = parsed
    rec = await search.get_record(db, rtype, rid)
    if rec is None:
        await notifications.send_text(chat_id, "Не нашёл такую запись.")
        return

    for f in rec.get("files") or []:
        try:
            storage.delete_file(f["r2_key"])
        except Exception:
            logger.exception("delete failed for %s", f.get("r2_key"))

    from modules.search import _TYPE_TO_MODEL  # type: ignore[attr-defined]

    model = _TYPE_TO_MODEL.get(rtype)
    if model is None:
        return
    obj = await db.get(model, rid)
    if obj is not None:
        await db.delete(obj)
        await db.commit()
    await notifications.send_text(chat_id, f"🗑 Удалил /{rtype}_{rid}")


def _parse_record_id(s: str) -> tuple[str, int] | None:
    if "_" not in s:
        return None
    rtype, _, rid = s.partition("_")
    rtype = rtype.lstrip("/").lower()
    if rtype not in {"person", "document", "vehicle", "address", "note"}:
        return None
    try:
        return rtype, int(rid)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Routing for non-command messages
# ---------------------------------------------------------------------------


async def _route_text(chat_id: int, text: str, db: AsyncSession) -> None:
    bs = await state.get_state(db, chat_id)

    if bs is not None and bs.state == "awaiting_ocr_edit":
        result = await ingest.apply_edit(chat_id, text, db)
        await _deliver_result(chat_id, result)
        return

    if bs is not None and bs.state in {
        "awaiting_more_photos",
        "awaiting_ocr_verification",
        "awaiting_dup_resolution",
        "awaiting_retrieve_choice",
    }:
        await notifications.send_text(
            chat_id,
            "Жду действие из текущего диалога. Нажми кнопку или /cancel.",
        )
        return

    intent, classified = await ingest.detect_intent_text(text, db)
    if intent == "ingest":
        result = await ingest.ingest_text(chat_id, text, db, classified=classified)
        await _deliver_result(chat_id, result)
    else:
        await _run_query(chat_id, text, db)


async def _route_files(
    chat_id: int,
    files: list[FileInput],
    caption: str,
    is_album: bool,
    db: AsyncSession,
) -> None:
    bs = await state.get_state(db, chat_id)
    if bs is not None and bs.state == "awaiting_more_photos":
        result = await ingest.add_more_photos(chat_id, files, db)
        await _deliver_result(chat_id, result)
        return

    if _needs_ack(files, is_album):
        await notifications.send_text(chat_id, "📥 Принял, обрабатываю…")

    result = await ingest.ingest_files(chat_id, files, caption, is_album, db)
    await _deliver_result(chat_id, result)


def _needs_ack(files: list[FileInput], is_album: bool) -> bool:
    """Heavy processing (PDF or multiple files) — show acknowledgement so the
    user knows the bot received the message even if classify takes 10+ sec."""
    if is_album or len(files) > 1:
        return True
    f = files[0]
    ct = (f.content_type or "").lower()
    return ct == "application/pdf" or f.filename.lower().endswith(".pdf")


async def _deliver_result(chat_id: int, result: ingest.IngestResult) -> None:
    if result.preamble:
        await notifications.send_text(chat_id, result.preamble)
    await notifications.send_text(chat_id, result.text, keyboard=result.keyboard)


# ---------------------------------------------------------------------------
# Query path
# ---------------------------------------------------------------------------


async def _run_query(chat_id: int, query: str, db: AsyncSession) -> None:
    r = await search.resolve_query(query, db)

    if not r.ids:
        await notifications.send_text(chat_id, "Ничего не нашёл по запросу.")
        return

    if r.action == "clarify" or len(r.ids) > 1:
        rows: list[list[dict[str, str]]] = []
        labels: list[str] = []
        for it in r.ids[:6]:
            rec = await search.get_record(db, it["type"], it["id"])
            if rec is None:
                continue
            label = rec.get("title") or it["type"]
            labels.append(str(label))
            rows.append(
                [
                    {
                        "text": str(label)[:60],
                        "callback_data": f"pick_{it['type']}_{it['id']}",
                    }
                ]
            )
        if not rows:
            await notifications.send_text(chat_id, "Ничего не нашёл по запросу.")
            return
        keyboard = client.make_inline_keyboard(rows)
        prompt = r.clarify_question or "Какую запись вернуть?"
        await state.set_state(
            db,
            chat_id,
            "awaiting_retrieve_choice",
            {"action": r.action if r.action != "clarify" else "send_both", "ids": r.ids},
        )
        await notifications.send_text(chat_id, prompt, keyboard=keyboard)
        return

    item = r.ids[0]
    rec = await search.get_record(db, item["type"], item["id"])
    if rec is None:
        await notifications.send_text(chat_id, "Запись пропала из БД.")
        return
    await _send_record(chat_id, item["type"], rec, action=r.action)


async def _send_record(
    chat_id: int,
    record_type: str,
    rec: dict[str, Any],
    action: str = "send_both",
) -> None:
    text = cards.render_record_card(record_type, rec)
    files = rec.get("files") or []
    if action == "send_text" or not files:
        await notifications.send_text(chat_id, text)
        return
    if action == "send_files":
        await notifications.send_files(chat_id, files, caption=text)
        return
    if files:
        await notifications.send_files(chat_id, files, caption=text)
    else:
        await notifications.send_text(chat_id, text)


# ---------------------------------------------------------------------------
# Callback router
# ---------------------------------------------------------------------------


async def callback_router(cb: dict[str, Any], db: AsyncSession) -> None:
    cb_id: str = cb["id"]
    data: str = cb.get("data") or ""
    chat_id = cb.get("message", {}).get("chat", {}).get("id")
    if chat_id is None or chat_id != settings.telegram_chat_id:
        await client.answer_callback_query(cb_id)
        return

    await client.answer_callback_query(cb_id)

    if data == "verify_ok":
        result = await ingest.confirm_draft(chat_id, db)
        await _deliver_result(chat_id, result)
        return
    if data == "verify_edit":
        result = await ingest.request_edit(chat_id, db)
        await _deliver_result(chat_id, result)
        return
    if data == "photos_more":
        await notifications.send_text(chat_id, "Жду следующее фото.")
        return
    if data == "photos_done":
        await notifications.send_text(chat_id, "📥 Распознаю фото…")
        result = await ingest.finish_more_photos(chat_id, db)
        await _deliver_result(chat_id, result)
        return
    if data == "dup_new":
        result = await ingest.resolve_duplicate(chat_id, "new", db)
        await notifications.send_text(chat_id, result.text)
        return
    if data == "dup_merge":
        result = await ingest.resolve_duplicate(chat_id, "merge", db)
        await notifications.send_text(chat_id, result.text)
        return
    if data == "dup_replace":
        result = await ingest.resolve_duplicate(chat_id, "replace", db)
        await notifications.send_text(chat_id, result.text)
        return
    if data.startswith("pick_"):
        await _handle_pick(chat_id, data, db)
        return
    if data.startswith("send_doc_"):
        try:
            doc_id = int(data.removeprefix("send_doc_"))
        except ValueError:
            return
        rec = await search.get_record(db, "document", doc_id)
        if rec is None:
            await notifications.send_text(chat_id, "Запись не найдена.")
            return
        await _send_record(chat_id, "document", rec, action="send_both")
        return


async def _handle_pick(chat_id: int, data: str, db: AsyncSession) -> None:
    rest = data.removeprefix("pick_")
    parts = rest.split("_", 1)
    if len(parts) != 2:
        return
    rtype = parts[0]
    try:
        rid = int(parts[1])
    except ValueError:
        return
    bs = await state.get_state(db, chat_id)
    action = "send_both"
    if bs is not None and bs.state == "awaiting_retrieve_choice":
        action = bs.context.get("action") or "send_both"
        await state.clear_state(db, chat_id)
    rec = await search.get_record(db, rtype, rid)
    if rec is None:
        await notifications.send_text(chat_id, "Запись не найдена.")
        return
    await _send_record(chat_id, rtype, rec, action=action)


# ---------------------------------------------------------------------------
# File extraction from Telegram message
# ---------------------------------------------------------------------------


async def _extract_files(msg: dict[str, Any]) -> list[FileInput]:
    """Pull all file-like content from a single Telegram message."""
    files: list[FileInput] = []

    if "photo" in msg and msg["photo"]:
        # PhotoSize array — last is the largest.
        photo = msg["photo"][-1]
        f = await _download_telegram(photo["file_id"], default_ct="image/jpeg")
        if f is not None:
            files.append(f)

    if "document" in msg and msg["document"]:
        d = msg["document"]
        f = await _download_telegram(
            d["file_id"],
            default_filename=d.get("file_name") or "file.bin",
            default_ct=d.get("mime_type") or "application/octet-stream",
        )
        if f is not None:
            files.append(f)

    return files


async def _download_telegram(
    file_id: str,
    default_filename: str | None = None,
    default_ct: str = "application/octet-stream",
) -> FileInput | None:
    try:
        info = await client.get_file(file_id)
    except Exception:
        logger.exception("get_file failed for %s", file_id)
        return None
    file_path = info.get("result", {}).get("file_path")
    if not file_path:
        return None
    try:
        data = await client.download_telegram_file(file_path)
    except Exception:
        logger.exception("download_telegram_file failed for %s", file_path)
        return None
    filename = default_filename or file_path.rsplit("/", 1)[-1]
    return FileInput(bytes_=data, filename=filename, content_type=default_ct)


# ---------------------------------------------------------------------------
# Album buffering
# ---------------------------------------------------------------------------


async def _album_collect(
    chat_id: int,
    media_group_id: str,
    files: list[FileInput],
    caption: str,
    db: AsyncSession,
) -> None:
    buf = _album_buffers.get(media_group_id)
    if buf is None:
        buf = _AlbumBuffer(chat_id=chat_id, media_group_id=media_group_id)
        _album_buffers[media_group_id] = buf
    buf.files.extend(files)
    if caption and not buf.caption:
        buf.caption = caption

    if buf.flush_task is not None and not buf.flush_task.done():
        buf.flush_task.cancel()
    buf.flush_task = asyncio.create_task(_album_flush_after_delay(media_group_id, db))


async def _album_flush_after_delay(media_group_id: str, db: AsyncSession) -> None:
    try:
        await asyncio.sleep(ALBUM_FLUSH_DELAY_SEC)
    except asyncio.CancelledError:
        return
    buf = _album_buffers.pop(media_group_id, None)
    if buf is None or not buf.files:
        return
    await _route_files(buf.chat_id, buf.files, buf.caption, is_album=True, db=db)
