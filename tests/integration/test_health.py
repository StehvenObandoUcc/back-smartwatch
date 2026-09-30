import schemathesis
from fastapi import FastAPI
from httpx import AsyncClient
from schemathesis.core.result import Ok
from sqlalchemy import text

from tests.conftest import CONTRACT_PATH


async def test_readiness_with_real_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_migrations_applied(integration_app: FastAPI, client: AsyncClient) -> None:
    async with integration_app.state.engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()

    assert version == "0001"


def test_api_conforms_to_contract(integration_app: FastAPI) -> None:
    """Schemathesis: cada operación del contrato responde según su esquema."""
    schema = schemathesis.openapi.from_path(CONTRACT_PATH)
    schema.app = integration_app

    for result in schema.get_all_operations():
        assert isinstance(result, Ok), result
        result.ok().Case().call_and_validate()
