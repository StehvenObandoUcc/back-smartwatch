"""Postgres y Redis reales con Testcontainers (requiere Docker)."""

from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.config import Settings
from app.main import create_app
from tests.conftest import ROOT, make_client


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def integration_settings() -> Iterator[Settings]:
    with (
        PostgresContainer("postgres:17-alpine", driver="asyncpg") as pg,
        RedisContainer("redis:7-alpine") as redis,
    ):
        host, port = redis.get_container_host_ip(), redis.get_exposed_port(6379)
        settings = Settings(
            app_env="test",
            database_url=pg.get_connection_url(),
            redis_url=f"redis://{host}:{port}/0",
        )

        alembic_cfg = Config(str(ROOT / "alembic.ini"))
        alembic_cfg.attributes["db_url"] = str(settings.database_url)
        command.upgrade(alembic_cfg, "head")

        yield settings


@pytest.fixture(scope="session")
def integration_app(integration_settings: Settings) -> FastAPI:
    return create_app(integration_settings)


@pytest.fixture
async def client(integration_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async for c in make_client(integration_app):
        yield c
