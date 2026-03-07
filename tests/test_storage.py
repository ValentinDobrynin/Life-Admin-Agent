from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── upload_file ──────────────────────────────────────────────────────────────


@patch("modules.storage._get_client")
def test_upload_file_returns_r2_key(mock_get_client: MagicMock) -> None:
    mock_s3 = MagicMock()
    mock_get_client.return_value = mock_s3

    from modules.storage import upload_file

    key = upload_file(b"pdf content", "policy.pdf", entity_id=42)

    assert key.startswith("entities/42/")
    assert key.endswith(".pdf")
    mock_s3.put_object.assert_called_once()


@patch("modules.storage._get_client")
def test_upload_file_key_is_unique(mock_get_client: MagicMock) -> None:
    mock_s3 = MagicMock()
    mock_get_client.return_value = mock_s3

    from modules.storage import upload_file

    key1 = upload_file(b"data", "file.pdf", entity_id=1)
    key2 = upload_file(b"data", "file.pdf", entity_id=1)

    assert key1 != key2


@patch("modules.storage._get_client")
def test_upload_file_guesses_content_type(mock_get_client: MagicMock) -> None:
    mock_s3 = MagicMock()
    mock_get_client.return_value = mock_s3

    from modules.storage import upload_file

    upload_file(b"img", "photo.jpg", entity_id=5)

    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["ContentType"] == "image/jpeg"


@patch("modules.storage._get_client")
def test_upload_file_raises_on_s3_error(mock_get_client: MagicMock) -> None:
    from botocore.exceptions import ClientError

    mock_s3 = MagicMock()
    mock_s3.put_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": ""}}, "put_object"
    )
    mock_get_client.return_value = mock_s3

    from modules.storage import upload_file

    with pytest.raises(ClientError):
        upload_file(b"data", "file.pdf", entity_id=1)


# ── get_presigned_url ────────────────────────────────────────────────────────


@patch("modules.storage._get_client")
def test_get_presigned_url_returns_url(mock_get_client: MagicMock) -> None:
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://r2.example.com/signed"
    mock_get_client.return_value = mock_s3

    from modules.storage import get_presigned_url

    url = get_presigned_url("entities/1/abc.pdf")

    assert url == "https://r2.example.com/signed"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={
            "Bucket": mock_s3.generate_presigned_url.call_args[1]["Params"]["Bucket"],
            "Key": "entities/1/abc.pdf",
        },
        ExpiresIn=3600,
    )


@patch("modules.storage._get_client")
def test_get_presigned_url_custom_expiry(mock_get_client: MagicMock) -> None:
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://r2.example.com/short"
    mock_get_client.return_value = mock_s3

    from modules.storage import get_presigned_url

    get_presigned_url("entities/1/abc.pdf", expires=300)

    call_kwargs = mock_s3.generate_presigned_url.call_args[1]
    assert call_kwargs["ExpiresIn"] == 300
