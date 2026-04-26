from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import PurePosixPath
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import settings

logger = logging.getLogger(__name__)


def _r2_endpoint() -> str:
    return f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"


def _get_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=_r2_endpoint(),
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _make_key(filename: str, prefix: str) -> str:
    ext = PurePosixPath(filename).suffix
    return f"{prefix}/{uuid.uuid4().hex}{ext}"


def upload_file(
    file_bytes: bytes,
    filename: str,
    prefix: str = "files",
    content_type: str | None = None,
) -> str:
    """Upload bytes to R2; return r2_key.

    Key format: ``{prefix}/{uuid}.ext``. Caller may use prefixes like
    ``person``, ``document``, ``vehicle``, ``address``, ``note`` to keep
    the bucket organised.
    """
    r2_key = _make_key(filename, prefix)
    if content_type is None:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    client = _get_client()
    try:
        client.put_object(
            Bucket=settings.r2_bucket_name,
            Key=r2_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        logger.info("Uploaded %s (%d bytes)", r2_key, len(file_bytes))
        return r2_key
    except ClientError:
        logger.exception("R2 upload failed: %s", r2_key)
        raise


def download_file(r2_key: str) -> bytes:
    client = _get_client()
    try:
        resp = client.get_object(Bucket=settings.r2_bucket_name, Key=r2_key)
        data: bytes = resp["Body"].read()
        return data
    except ClientError:
        logger.exception("R2 download failed: %s", r2_key)
        raise


def get_presigned_url(r2_key: str, expires: int = 3600) -> str:
    client = _get_client()
    try:
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket_name, "Key": r2_key},
            ExpiresIn=expires,
        )
        return url
    except ClientError:
        logger.exception("R2 presigned URL failed: %s", r2_key)
        raise


def delete_file(r2_key: str) -> None:
    client = _get_client()
    try:
        client.delete_object(Bucket=settings.r2_bucket_name, Key=r2_key)
        logger.info("Deleted %s", r2_key)
    except ClientError:
        logger.exception("R2 delete failed: %s", r2_key)
        raise
