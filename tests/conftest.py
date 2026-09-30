import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Valores por defecto para las pruebas unitarias: no se abre ninguna conexión real.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")

from app.core.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "openapi.yaml"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

Operation = tuple[str, str, str]


def _operations(spec: dict[str, Any]) -> set[Operation]:
    """(método, ruta, operationId) de cada operación de un documento OpenAPI."""
    return {
        (method, path, op["operationId"])
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in _HTTP_METHODS
    }


def load_contract() -> dict[str, Any]:
    contract: dict[str, Any] = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    return contract


def contract_operations() -> set[Operation]:
    return set(contract_phases())


def contract_phases() -> dict[Operation, int | None]:
    """Fase (`x-phase`) de cada operación del contrato; None si no la declara."""
    return {
        (method, path, op["operationId"]): op.get("x-phase")
        for path, item in load_contract()["paths"].items()
        for method, op in item.items()
        if method in _HTTP_METHODS
    }


def app_operations(app: FastAPI) -> set[Operation]:
    return _operations(app.openapi())


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def make_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP en proceso que ejecuta el lifespan de la app."""
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client,
    ):
        yield client
