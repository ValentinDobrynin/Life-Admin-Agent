from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules import vision


@pytest.mark.asyncio
async def test_ocr_image_returns_content() -> None:
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content=" Распознанный текст "))]
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch("modules.vision._client", return_value=fake_client):
        text = await vision.ocr_image(b"fake-image-bytes", mime="image/png")

    assert text == "Распознанный текст"
    fake_client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_ocr_image_returns_empty_on_error() -> None:
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("api down"))

    with patch("modules.vision._client", return_value=fake_client):
        text = await vision.ocr_image(b"x")

    assert text == ""
