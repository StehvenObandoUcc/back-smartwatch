from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import ROOT


async def test_readiness_with_real_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_migrations_applied(integration_app: FastAPI, client: AsyncClient) -> None:
    async with integration_app.state.engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()

    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    assert version == ScriptDirectory.from_config(alembic_cfg).get_current_head()
