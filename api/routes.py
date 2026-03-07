from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.auth import verify_token

router = APIRouter(prefix="/api", dependencies=[Depends(verify_token)])

_NOT_IMPLEMENTED = JSONResponse({"detail": "Not implemented"}, status_code=501)


@router.get("/entities")
async def list_entities() -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: int) -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.post("/entities")
async def create_entity() -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.patch("/entities/{entity_id}")
async def update_entity(entity_id: int) -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.post("/entities/{entity_id}/archive")
async def archive_entity(entity_id: int) -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.get("/reminders")
async def list_reminders() -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.get("/contacts")
async def list_contacts() -> JSONResponse:
    return _NOT_IMPLEMENTED


@router.get("/digest/preview")
async def digest_preview() -> JSONResponse:
    return _NOT_IMPLEMENTED
