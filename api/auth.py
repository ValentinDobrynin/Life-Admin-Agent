from __future__ import annotations

from fastapi import Header, HTTPException

from config import settings


async def verify_token(x_api_key: str = Header(...)) -> None:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
