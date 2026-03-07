from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from config import settings

logger = logging.getLogger(__name__)

_R2_ENDPOINT = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"


def _get_client() -> object:
    return boto3.client(
        "s3",
        endpoint_url=_R2_ENDPOINT,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_file(
    file_bytes: bytes,
    filename: str,
    entity_id: int = 0,
    content_type: str | None = None,
    prefix: str = "entities",
) -> str:
    """Upload bytes to R2 and return the r2_key.

    When prefix='entities' and entity_id is set, key is entities/{entity_id}/{uuid}.ext.
    Otherwise key is {prefix}/{uuid}.ext (e.g. reference/uuid.jpg).
    """
    ext = PurePosixPath(filename).suffix
    unique_name = f"{uuid.uuid4().hex}{ext}"
    if prefix == "entities" and entity_id:
        r2_key = f"entities/{entity_id}/{unique_name}"
    else:
        r2_key = f"{prefix}/{unique_name}"

    if content_type is None:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    client = _get_client()
    try:
        client.put_object(  # type: ignore[attr-defined]
            Bucket=settings.r2_bucket_name,
            Key=r2_key,
            Body=file_bytes,
            ContentType=content_type,
        )
        logger.info("Uploaded file to R2: %s (%d bytes)", r2_key, len(file_bytes))
        return r2_key
    except ClientError:
        logger.exception("R2 upload failed for key %s, file %s", r2_key, filename)
        raise


def download_file(r2_key: str) -> bytes:
    """Download file from R2 by key. Returns raw bytes."""
    client = _get_client()
    try:
        response = client.get_object(Bucket=settings.r2_bucket_name, Key=r2_key)  # type: ignore[attr-defined]
        data: bytes = response["Body"].read()
        logger.info("Downloaded file from R2: %s (%d bytes)", r2_key, len(data))
        return data
    except ClientError:
        logger.exception("R2 download failed for key: %s", r2_key)
        raise


def get_presigned_url(r2_key: str, expires: int = 3600) -> str:
    """Generate a presigned URL for temporary access to the file."""
    client = _get_client()
    try:
        url: str = client.generate_presigned_url(  # type: ignore[attr-defined]
            "get_object",
            Params={"Bucket": settings.r2_bucket_name, "Key": r2_key},
            ExpiresIn=expires,
        )
        return url
    except ClientError:
        logger.exception("Failed to generate presigned URL for key: %s", r2_key)
        raise


def delete_file(r2_key: str) -> None:
    """Delete a file from R2 (used in tests / admin)."""
    client = _get_client()
    try:
        client.delete_object(Bucket=settings.r2_bucket_name, Key=r2_key)  # type: ignore[attr-defined]
        logger.info("Deleted R2 key: %s", r2_key)
    except ClientError:
        logger.exception("R2 delete failed for key: %s", r2_key)
        raise
