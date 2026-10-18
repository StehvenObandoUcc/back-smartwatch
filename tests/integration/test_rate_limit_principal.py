"""Tope general por usuario autenticado: un token válido no puede pedir datos sin límite."""

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.core.config import RateLimit
from tests.conftest import make_client
from tests.integration.conftest import AppFactory
from tests.integration.helpers import assert_problem, bearer, register


@pytest.fixture
async def tight_client(app_factory: AppFactory) -> AsyncIterator[AsyncClient]:
    app = app_factory(rate_principal=RateLimit(limit=2, window_seconds=60))
    async for c in make_client(app):
        yield c


async def test_authenticated_requests_are_limited_per_principal(
    tight_client: AsyncClient,
) -> None:
    session = await register(tight_client)

    first = await tight_client.get("/users/me", headers=bearer(session))
    second = await tight_client.get("/users/me", headers=bearer(session))
    third = await tight_client.get("/users/me", headers=bearer(session))

    assert (first.status_code, second.status_code) == (200, 200)
    assert_problem(third, 429, "rate_limited")
    assert 0 < int(third.headers["retry-after"]) <= 60


async def test_the_limit_is_per_user_not_shared(tight_client: AsyncClient) -> None:
    first_user = await register(tight_client)
    second_user = await register(tight_client, role="caregiver")
    for _ in range(2):
        await tight_client.get("/users/me", headers=bearer(first_user))

    other = await tight_client.get("/users/me", headers=bearer(second_user))

    assert other.status_code == 200
