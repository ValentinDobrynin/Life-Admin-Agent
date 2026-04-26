"""Thin wrapper over Telegram Bot HTTP API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _url(method: str) -> str:
    return TELEGRAM_API_BASE.format(token=settings.telegram_bot_token, method=method)


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        resp = await client.post(_url("sendMessage"), json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        resp = await client.post(_url("editMessageText"), json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    async with httpx.AsyncClient() as client:
        resp = await client.post(_url("answerCallbackQuery"), json=payload, timeout=10)
        resp.raise_for_status()


async def set_webhook(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(_url("setWebhook"), json={"url": url}, timeout=10)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def get_file(file_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(_url("getFile"), json={"file_id": file_id}, timeout=10)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def download_telegram_file(file_path: str) -> bytes:
    url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content


async def send_document(
    chat_id: int,
    file_bytes: bytes,
    filename: str,
    caption: str | None = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    data: dict[str, str] = {"chat_id": str(chat_id), "parse_mode": parse_mode}
    if caption:
        data["caption"] = caption
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("sendDocument"),
            data=data,
            files={"document": (filename, file_bytes)},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def send_photo(
    chat_id: int,
    file_bytes: bytes,
    filename: str = "photo.jpg",
    caption: str | None = None,
    parse_mode: str = "HTML",
) -> dict[str, Any]:
    data: dict[str, str] = {"chat_id": str(chat_id), "parse_mode": parse_mode}
    if caption:
        data["caption"] = caption
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("sendPhoto"),
            data=data,
            files={"photo": (filename, file_bytes)},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def send_media_group(
    chat_id: int,
    photos: list[tuple[bytes, str]],
    caption: str | None = None,
) -> dict[str, Any]:
    """Send 2-10 photos as a single album.

    `photos` is a list of (bytes, filename). The caption (if any) is attached
    to the first photo.
    """
    if not photos:
        return {}
    media: list[dict[str, Any]] = []
    files: dict[str, tuple[str, bytes]] = {}
    for idx, (data, name) in enumerate(photos[:10]):
        attach_name = f"photo{idx}"
        files[attach_name] = (name, data)
        item: dict[str, Any] = {"type": "photo", "media": f"attach://{attach_name}"}
        if idx == 0 and caption:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)

    data_payload = {"chat_id": str(chat_id), "media": _json_dumps(media)}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _url("sendMediaGroup"),
            data=data_payload,
            files=files,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def make_inline_keyboard(buttons: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": buttons}
