import schemathesis
from fastapi import FastAPI
from httpx import AsyncClient
from schemathesis.core.result import Ok
from sqlalchemy import text

from tests.conftest import CONTRACT_PATH, app_operations


async def test_readiness_with_real_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_migrations_applied(integration_app: FastAPI, client: AsyncClient) -> None:
    async with integration_app.state.engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()

    assert version == "0001"


def test_api_conforms_to_contract(integration_app: FastAPI) -> None:
    """Schemathesis: cada operación implementada responde según el esquema del contrato."""
    schema = schemathesis.openapi.from_path(CONTRACT_PATH)
    schema.app = integration_app
    implemented = {(method, path) for method, path, _ in app_operations(integration_app)}

    checked = 0
    for result in schema.get_all_operations():
        assert isinstance(result, Ok), result
        operation = result.ok()
        if (operation.method.lower(), operation.path) in implemented:
            operation.Case().call_and_validate()
            checked += 1

    assert checked == len(implemented)
