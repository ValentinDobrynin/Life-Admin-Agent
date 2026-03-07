from __future__ import annotations

from typing import Any

import httpx

from config import settings

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


async def download_file(file_path: str) -> bytes:
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
    """Send a file to a Telegram chat."""
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


def make_inline_keyboard(buttons: list[list[dict[str, str]]]) -> dict[str, Any]:
    """Build an inline_keyboard reply_markup from a list of button rows."""
    return {"inline_keyboard": buttons}
