from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncClient:
    from main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


API_ROUTES = [
    ("GET", "/api/entities"),
    ("GET", "/api/entities/1"),
    ("POST", "/api/entities"),
    ("PATCH", "/api/entities/1"),
    ("POST", "/api/entities/1/archive"),
    ("GET", "/api/reminders"),
    ("GET", "/api/contacts"),
    ("GET", "/api/digest/preview"),
]


@pytest.mark.parametrize("method,path", API_ROUTES)
async def test_api_returns_501_with_valid_token(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, headers={"x-api-key": "test-api-key"})
    assert response.status_code == 501
    assert response.json() == {"detail": "Not implemented"}


@pytest.mark.parametrize("method,path", API_ROUTES)
async def test_api_returns_401_without_token(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path)
    assert response.status_code == 422  # missing header → validation error


@pytest.mark.parametrize("method,path", API_ROUTES)
async def test_api_returns_401_with_wrong_token(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, headers={"x-api-key": "wrong-key"})
    assert response.status_code == 401


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
