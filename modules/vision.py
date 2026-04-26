from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)

_PROMPT = (
    "Извлеки весь видимый текст с изображения максимально точно. "
    "Сохраняй построчное расположение там, где это смыслово важно. "
    "Не комментируй, не интерпретируй — только распознанный текст. "
    "Если изображение не содержит текста — верни пустую строку."
)


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def ocr_image(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Run OpenAI Vision OCR on an image. Returns plain text (may be empty)."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    client = _client()
    try:
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            temperature=0.0,
        )
    except Exception:
        logger.exception("OpenAI Vision OCR failed")
        return ""

    content = resp.choices[0].message.content or ""
    return content.strip()
