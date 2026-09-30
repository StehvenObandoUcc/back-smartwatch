"""Postgres y Redis reales con Testcontainers (requiere Docker)."""

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.config import RateLimit, Settings
from app.main import create_app
from tests.conftest import ROOT, make_client

# Límites holgados por defecto; las pruebas de 429 los bajan explícitamente.
_GENEROUS = RateLimit(limit=10_000, window_seconds=60)
RATE_LIMIT_FIELDS = [name for name in Settings.model_fields if name.startswith("rate_")]

AppFactory = Callable[..., FastAPI]


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
        ).model_copy(update=dict.fromkeys(RATE_LIMIT_FIELDS, _GENEROUS))

        alembic_cfg = Config(str(ROOT / "alembic.ini"))
        alembic_cfg.attributes["db_url"] = str(settings.database_url)
        command.upgrade(alembic_cfg, "head")

        yield settings


@pytest.fixture(scope="session")
async def db_engine(integration_settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(str(integration_settings.database_url))
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_state(integration_settings: Settings, db_engine: AsyncEngine) -> None:
    """Cada prueba empieza con la base y Redis vacíos."""
    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        tables: list[str] = list(result.scalars())
        names = ", ".join(f'"{name}"' for name in tables)
        if names:
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
    redis = Redis.from_url(str(integration_settings.redis_url))
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
def app_factory(integration_settings: Settings) -> AppFactory:
    """Crea una app con la configuración de integración y los cambios indicados."""

    def factory(**overrides: Any) -> FastAPI:
        return create_app(integration_settings.model_copy(update=overrides))

    return factory


@pytest.fixture
def integration_app(app_factory: AppFactory) -> FastAPI:
    return app_factory()


@pytest.fixture
async def client(integration_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async for c in make_client(integration_app):
        yield c
