from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.main import create_app
from app.modules.health.router import get_health_service
from app.modules.health.service import HealthService
from tests.conftest import make_client
from tests.unit.test_health_service import FakeProbe


@pytest.fixture
async def client_with_probe(request: pytest.FixtureRequest) -> AsyncIterator[AsyncClient]:
    probe: FakeProbe = getattr(request, "param", FakeProbe())
    app = create_app()
    app.dependency_overrides[get_health_service] = lambda: HealthService(probe, 0.05)
    async for client in make_client(app):
        yield client


async def test_health_returns_ok(client_with_probe: AsyncClient) -> None:
    response = await client_with_probe.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_sets_request_id(client_with_probe: AsyncClient) -> None:
    response = await client_with_probe.get("/health", headers={"X-Request-ID": "abc-123"})

    assert response.headers["x-request-id"] == "abc-123"


async def test_health_replaces_unsafe_request_id(client_with_probe: AsyncClient) -> None:
    response = await client_with_probe.get("/health", headers={"X-Request-ID": "bad id\n"})

    assert response.headers["x-request-id"] != "bad id\n"
    assert len(response.headers["x-request-id"]) == 36


async def test_readiness_returns_ok(client_with_probe: AsyncClient) -> None:
    response = await client_with_probe.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize("client_with_probe", [FakeProbe(db_error=OSError())], indirect=True)
async def test_readiness_returns_problem_when_dependency_down(
    client_with_probe: AsyncClient,
) -> None:
    response = await client_with_probe.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.example.com/problems/dependency-unavailable",
        "title": "Dependencia no disponible",
        "status": 503,
        "detail": "Postgres no responde.",
        "code": "dependency_unavailable",
        "instance": "/health/ready",
    }
