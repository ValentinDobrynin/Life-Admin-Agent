from __future__ import annotations

from unittest.mock import MagicMock, patch

from modules import storage


def test_make_key_format() -> None:
    key = storage._make_key("scan.pdf", prefix="document")
    assert key.startswith("document/")
    assert key.endswith(".pdf")


def test_make_key_no_extension() -> None:
    key = storage._make_key("scan", prefix="document")
    assert key.startswith("document/")


def test_upload_file_calls_boto() -> None:
    fake = MagicMock()
    with patch("modules.storage._get_client", return_value=fake):
        key = storage.upload_file(b"hello", "x.txt", prefix="note")
    assert key.startswith("note/")
    fake.put_object.assert_called_once()


def test_get_presigned_url() -> None:
    fake = MagicMock()
    fake.generate_presigned_url.return_value = "https://signed/url"
    with patch("modules.storage._get_client", return_value=fake):
        url = storage.get_presigned_url("document/abc.pdf")
    assert url == "https://signed/url"


def test_delete_file_calls_boto() -> None:
    fake = MagicMock()
    with patch("modules.storage._get_client", return_value=fake):
        storage.delete_file("note/abc.txt")
    fake.delete_object.assert_called_once()
